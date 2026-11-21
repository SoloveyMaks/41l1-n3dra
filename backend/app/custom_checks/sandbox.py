"""Bounded bwrap sandbox for one user Python check.

Isolation: new namespaces (including network), no host env, only /usr + libs +
read-only project/input mounts, writable tmpfs /work, resource limits via
prlimit. The project directory is never writable and is not executed.

`/proc` is deliberately not mounted: it is unavailable inside a rootless
podman/read-only container, and the check does not need it. `/dev` is a minimal
bwrap devtmpfs (`/dev/null` only).

If bwrap, prlimit or the interpreter cannot actually start a sandbox, the
runner reports `unavailable` with a reason. There is no host fallback: a broken
sandbox never executes user code directly.
"""

from __future__ import annotations

import json
import os
import selectors
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.app.ai.provider import mask_sensitive_text
from backend.app.custom_checks.catalog import INPUT_CONTRACT

BWRAP = shutil.which("bwrap") or "/usr/bin/bwrap"
PRLIMIT = shutil.which("prlimit") or "/usr/bin/prlimit"
PYTHON_CANDIDATES = ("/usr/bin/python3", "/usr/local/bin/python3")

# Mounts shared by the real run and the availability probe. Only paths that
# exist on this host are bound; the interpreter is addressed absolutely.
_READ_ONLY_ROOTS = ("/usr", "/usr/local", "/lib", "/lib64", "/bin", "/sbin")

_PROBE_LOCK = threading.Lock()
_PROBE: Optional[Tuple[bool, str]] = None


@dataclass(frozen=True)
class SandboxLimits:
    timeout_seconds: int = 3
    memory_mb: int = 256
    max_output_bytes: int = 16384


@dataclass
class SandboxOutcome:
    status: str
    exit_code: Optional[int]
    duration_ms: int
    stdout: str = ""
    stderr: str = ""
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    limits: Dict[str, Any] = field(default_factory=dict)


class SandboxError(RuntimeError):
    pass


class SandboxUnavailable(SandboxError):
    """The runner cannot isolate on this host; do not execute user code."""


def _python_binary() -> Optional[str]:
    for candidate in PYTHON_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


def _safe_project(root: Path) -> Path:
    raw = Path(root)
    if raw.is_symlink():
        raise SandboxError("Каталог проекта не может быть symlink")
    root = raw.resolve()
    if not root.is_dir():
        raise SandboxError("Каталог проекта недоступен")
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in list(dirs) + list(files):
            candidate = current_path / name
            if candidate.is_symlink():
                raise SandboxError("В проекте найден symlink; запуск запрещён")
    return root


def _base_command(python: str, cpu_seconds: int = 2) -> List[str]:
    """prlimit + bwrap prefix shared by probe and real runs."""
    args = [
        PRLIMIT,
        f"--cpu={max(1, int(cpu_seconds))}",
        "--fsize=1048576",
        "--nofile=64",
        "--",
        BWRAP,
        "--unshare-all",
        "--die-with-parent",
        "--clearenv",
        "--setenv", "PATH", "/usr/bin:/bin",
        "--setenv", "LANG", "C.UTF-8",
        "--setenv", "LC_ALL", "C.UTF-8",
        "--setenv", "PYTHONIOENCODING", "utf-8",
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
    ]
    # Debian's distro python lives in /usr/local and finds libpython only via
    # the host ld cache, which is not inside the sandbox.
    if python.startswith("/usr/local/") and Path("/usr/local/lib").is_dir():
        args += ["--setenv", "LD_LIBRARY_PATH", "/usr/local/lib"]
    for path in _READ_ONLY_ROOTS:
        if Path(path).exists():
            args += ["--ro-bind", path, path]
    args += ["--dev", "/dev"]
    return args


def _bwrap_command(input_dir: Path, project: Path, limits: SandboxLimits, python: str) -> List[str]:
    args = _base_command(python, limits.timeout_seconds + 1)
    args += [
        "--size", "1048576",
        "--tmpfs", "/work",
        "--ro-bind", str(input_dir), "/input",
        "--ro-bind", str(project), "/project",
        "--chdir", "/work",
        python,
        "-I",
        "-B",
        "/input/check.py",
        "/input/input.json",
    ]
    return args


def _checked_tooling() -> Tuple[str, str]:
    if not Path(BWRAP).is_file():
        raise SandboxUnavailable("bwrap не установлен на сервере")
    if not Path(PRLIMIT).is_file():
        raise SandboxUnavailable("prlimit не установлен на сервере")
    python = _python_binary()
    if python is None:
        raise SandboxUnavailable("python3 не найден в sandbox-окружении")
    return BWRAP, python


def sandbox_availability(*, refresh: bool = False) -> Tuple[bool, str]:
    """One cached probe: does bwrap actually start an isolated python here?"""
    global _PROBE
    with _PROBE_LOCK:
        if _PROBE is not None and not refresh:
            return _PROBE
        try:
            _, python = _checked_tooling()
            command = _base_command(python) + [python, "-I", "-B", "-c", "print('ok')"]
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                     "LD_LIBRARY_PATH": "/usr/local/lib"},
                timeout=10,
                start_new_session=True,
            )
            if completed.returncode == 0 and completed.stdout.strip() == b"ok":
                _PROBE = (True, "bwrap-python3 готов")
            else:
                detail = completed.stderr.decode("utf-8", "replace").strip().splitlines()
                _PROBE = (
                    False,
                    "bwrap не может поднять изоляцию: "
                    + (detail[0][:200] if detail else f"exit {completed.returncode}"),
                )
        except (SandboxUnavailable, OSError, subprocess.SubprocessError) as exc:
            _PROBE = (False, f"Sandbox недоступен: {exc}")
        return _PROBE


def _kill(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def _read_bounded(proc: subprocess.Popen[bytes], max_bytes: int, deadline: float) -> tuple[str, str, str]:
    selector = selectors.DefaultSelector()
    streams = {}
    for name, stream in (("stdout", proc.stdout), ("stderr", proc.stderr)):
        if stream is not None:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
            streams[name] = bytearray()
    status = "ok"
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _kill(proc)
            status = "timeout"
            break
        events = selector.select(min(0.1, remaining))
        for key, _ in events:
            stream = key.fileobj
            try:
                chunk = os.read(stream.fileno(), 4096)
            except (BlockingIOError, InterruptedError):
                continue
            except OSError:
                selector.unregister(stream)
                continue
            if not chunk:
                selector.unregister(stream)
                continue
            bucket = streams[key.data]
            if len(bucket) + len(chunk) > max_bytes:
                bucket.extend(chunk[: max(0, max_bytes - len(bucket))])
                _kill(proc)
                status = "output_limit"
                break
            bucket.extend(chunk)
        if status != "ok":
            break
        if proc.poll() is not None:
            # Drain already-buffered bytes, then finish.
            for key in list(selector.get_map().values()):
                stream = key.fileobj
                while True:
                    try:
                        chunk = os.read(stream.fileno(), 4096)
                    except (BlockingIOError, InterruptedError):
                        break
                    except OSError:
                        break
                    if not chunk:
                        break
                    bucket = streams[key.data]
                    if len(bucket) + len(chunk) > max_bytes:
                        bucket.extend(chunk[: max(0, max_bytes - len(bucket))])
                        status = "output_limit"
                        break
                    bucket.extend(chunk)
                try:
                    selector.unregister(stream)
                except Exception:
                    pass
            break
    selector.close()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        _kill(proc)
        proc.wait(timeout=2)
    stdout = bytes(streams.get("stdout", b"")).decode("utf-8", errors="replace")
    stderr = bytes(streams.get("stderr", b"")).decode("utf-8", errors="replace")
    return mask_sensitive_text(stdout), mask_sensitive_text(stderr), status


def _limits_payload(limits: SandboxLimits) -> Dict[str, Any]:
    return {
        "timeoutSeconds": limits.timeout_seconds,
        "memoryMb": limits.memory_mb,
        "maxOutputBytes": limits.max_output_bytes,
    }


def run_sandbox(
    script: str,
    project_path: str,
    artifact: Dict[str, Any],
    limits: SandboxLimits,
) -> SandboxOutcome:
    if not isinstance(script, str) or not script.strip():
        raise SandboxError("Пустой скрипт проверки")
    if len(script.encode("utf-8")) > 65536:
        raise SandboxError("Скрипт слишком большой")
    if "\x00" in script:
        raise SandboxError("Скрипт содержит NUL")
    available, reason = sandbox_availability()
    if not available:
        raise SandboxUnavailable(reason)
    project = _safe_project(Path(project_path))
    _, python = _checked_tooling()
    input_dir = Path(tempfile.mkdtemp(prefix="custom-check-"))
    try:
        os.chmod(input_dir, 0o700)
        script_path = input_dir / "check.py"
        input_path = input_dir / "input.json"
        script_path.write_text(script, encoding="utf-8")
        os.chmod(script_path, 0o400)
        payload = {
            "schemaVersion": INPUT_CONTRACT,
            "projectRoot": "/project",
            "artifact": {
                "id": artifact.get("id"),
                "filename": artifact.get("filename"),
                "fileCount": artifact.get("file_count"),
                "hashSha256": artifact.get("hash_sha256"),
            },
            "limits": _limits_payload(limits),
        }
        input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.chmod(input_path, 0o400)
        command = _bwrap_command(input_dir, project, limits, python)
        # The interpreter's own hard limits live in prlimit; memory is applied
        # here because it depends on the per-check limit.
        command.insert(1, f"--as={limits.memory_mb * 1024 * 1024}")
        started = time.monotonic()
        env = {
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "LD_LIBRARY_PATH": "/usr/local/lib",
        }
        try:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                start_new_session=True,
            )
        except OSError as exc:
            raise SandboxUnavailable(f"Sandbox не запустился: {exc}") from exc
        stdout, stderr, read_status = _read_bounded(
            proc, limits.max_output_bytes, started + limits.timeout_seconds
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        diagnostics: List[Dict[str, Any]] = []
        if read_status == "timeout":
            diagnostics.append({"code": "CUSTOM_TIMEOUT", "message": "Проверка превысила timeout"})
            return SandboxOutcome(
                "timeout", proc.returncode, duration_ms, stdout, stderr,
                diagnostics, _limits_payload(limits),
            )
        if read_status == "output_limit":
            diagnostics.append({"code": "CUSTOM_OUTPUT_LIMIT", "message": "Вывод проверки превысил лимит"})
            return SandboxOutcome(
                "output_limit", proc.returncode, duration_ms, stdout, stderr,
                diagnostics, _limits_payload(limits),
            )
        if proc.returncode != 0:
            diagnostics.append({
                "code": "CUSTOM_NONZERO_EXIT",
                "message": f"Проверка завершилась с кодом {proc.returncode}",
            })
            return SandboxOutcome(
                "failed", proc.returncode, duration_ms, stdout, stderr,
                diagnostics, _limits_payload(limits),
            )
        return SandboxOutcome(
            "ok", 0, duration_ms, stdout, stderr, [], _limits_payload(limits),
        )
    finally:
        shutil.rmtree(input_dir, ignore_errors=True)