"""Bounded bwrap sandbox for one user Python check.

Isolation and hard limits for the whole check:

- new namespaces (including network and PID), no host env;
- one read-only project/input mount and ``/work`` tmpfs (1 MiB);
- ``/dev`` is a 64 KiB tmpfs with only ``/dev/null`` and ``/dev/urandom``; there
  is no writable ``/dev/shm``;
- an inner ``prlimit --nproc=1`` makes the process tree exactly one task, so
  the per-process address-space/CPU/file-size limits apply to the whole tree:
  fork, threads and subprocesses fail with ``EAGAIN``;
- a seccomp filter denies ``memfd_create`` and SysV shared memory, because
  those allocate page cache outside ``RLIMIT_AS`` (and ``RLIMIT_FSIZE`` does not
  bound shmem files);
- ``prlimit --as/--cpu/--fsize/--nofile`` in both layers (outer bwrap and the
  interpreter itself).

The availability probe actually forks, calls ``memfd_create`` and inspects the
mount layout. If any boundary is missing, the runner reports ``unavailable``
with a reason and refuses to start. There is no host fallback.

``/proc`` is deliberately not mounted: it is unavailable inside a rootless
podman/read-only container, and the check does not need it.
"""

from __future__ import annotations

import json
import os
import platform
import selectors
import shutil
import signal
import struct
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
# Paths inside the sandbox (the host's /usr is mounted read-only there).
SANDBOX_PRLIMIT = "/usr/bin/prlimit"
PYTHON_CANDIDATES = ("/usr/bin/python3", "/usr/local/bin/python3")

# Mounts shared by the real run and the availability probe. Only paths that
# exist on this host are bound; the interpreter is addressed absolutely.
_READ_ONLY_ROOTS = ("/usr", "/usr/local", "/lib", "/lib64", "/bin", "/sbin")

DEV_TMPFS_BYTES = 65536
WORK_TMPFS_BYTES = 1048576
MAX_OPEN_FILES = 16
MAX_TASKS = 1

_PROBE_LOCK = threading.Lock()
_PROBE: Optional[Tuple[bool, str]] = None

# seccomp classic BPF constants.
_BPF_LD_W_ABS = 0x20
_BPF_JEQ_K = 0x15
_BPF_RET_K = 0x06
_RET_ALLOW = 0x7FFF0000
_RET_ERRNO = 0x00050000
_EPERM = 1
_AUDIT_ARCH = {
    # arch -> (AUDIT_ARCH value, {blocked syscall: number})
    "x86_64": (
        0xC000003E,
        {"memfd_create": 319, "shmget": 29, "shmat": 30, "shmctl": 31, "shmdt": 67},
    ),
    "aarch64": (
        0xC00000B7,
        {"memfd_create": 279, "shmget": 194, "shmat": 196, "shmctl": 195, "shmdt": 197},
    ),
}


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


class SandboxCancelled(SandboxError):
    """The check was cancelled by its owner; nothing may be persisted."""


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


def _seccomp_blocks() -> Tuple[int, Dict[str, int]]:
    machine = platform.machine().strip().lower()
    entry = _AUDIT_ARCH.get(machine)
    if entry is None:
        raise SandboxUnavailable(f"Архитектура {machine!r} не поддержана sandbox-фильтром")
    return entry


def _seccomp_program(blocks: Dict[str, int]) -> bytes:
    instructions: List[Tuple[int, int, int, int]] = []
    # Verify the ABI arch first; a mismatched number would silently allow the
    # blocked syscalls, so anything else is killed.
    instructions.append((_BPF_LD_W_ABS, 0, 0, 4))  # seccomp_data.arch
    instructions.append((_BPF_JEQ_K, 1, 0, _seccomp_blocks()[0]))
    instructions.append((_BPF_RET_K, 0, 0, 0x80000000))  # SECCOMP_RET_KILL_PROCESS
    instructions.append((_BPF_LD_W_ABS, 0, 0, 0))  # seccomp_data.nr
    for number in sorted(set(blocks.values())):
        instructions.append((_BPF_JEQ_K, 0, 1, number))
        instructions.append((_BPF_RET_K, 0, 0, _RET_ERRNO | _EPERM))
    instructions.append((_BPF_RET_K, 0, 0, _RET_ALLOW))
    return b"".join(struct.pack("<HBBI", *item) for item in instructions)


def _write_seccomp_file(input_dir: Path) -> Tuple[Path, int]:
    _, blocks = _seccomp_blocks()
    path = input_dir / "seccomp.bpf"
    path.write_bytes(_seccomp_program(blocks))
    os.chmod(path, 0o400)
    return path, os.open(path, os.O_RDONLY)


def _inner_limits(limits: SandboxLimits) -> List[str]:
    memory = limits.memory_mb * 1024 * 1024
    cpu = max(1, limits.timeout_seconds + 1)
    return [
        SANDBOX_PRLIMIT,
        f"--nproc={MAX_TASKS}",
        f"--as={memory}",
        f"--cpu={cpu}",
        "--fsize=1048576",
        f"--nofile={MAX_OPEN_FILES}",
        "--",
    ]


def _sandbox_argv(
    python: str,
    limits: SandboxLimits,
    seccomp_fd: int,
    mounts: List[Tuple[str, str]],
    child: List[str],
) -> List[str]:
    memory = limits.memory_mb * 1024 * 1024
    cpu = max(1, limits.timeout_seconds + 1)
    args = [
        PRLIMIT,
        f"--as={memory}",
        f"--cpu={cpu}",
        "--fsize=1048576",
        f"--nofile={MAX_OPEN_FILES}",
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
    args += [
        # Bounded /dev: no /dev/shm and no writable shared memory.
        "--size", str(DEV_TMPFS_BYTES),
        "--tmpfs", "/dev",
        "--ro-bind", "/dev/null", "/dev/null",
        "--ro-bind", "/dev/urandom", "/dev/urandom",
        "--size", str(WORK_TMPFS_BYTES),
        "--tmpfs", "/work",
    ]
    for source, target in mounts:
        args += ["--ro-bind", source, target]
    args += ["--seccomp", str(seccomp_fd), "--chdir", "/work"]
    args += _inner_limits(limits)
    args += child
    return args


def _checked_tooling() -> Tuple[str, str]:
    if not Path(BWRAP).is_file():
        raise SandboxUnavailable("bwrap не установлен на сервере")
    if not Path(PRLIMIT).is_file():
        raise SandboxUnavailable("prlimit не установлен на сервере")
    python = _python_binary()
    if python is None:
        raise SandboxUnavailable("python3 не найден в sandbox-окружении")
    _seccomp_blocks()
    return BWRAP, python


def _host_env() -> Dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "LD_LIBRARY_PATH": "/usr/local/lib",
    }


_PROBE_SCRIPT = r"""
import json, os
result = {
    "env": sorted(os.environ),
    "cwd": os.getcwd(),
    "shm": os.path.exists("/dev/shm"),
    "tmp": os.path.exists("/tmp"),
    "work_write": False,
    "fork": "allowed",
    "thread": "allowed",
    "subprocess": "allowed",
    "memfd": "allowed",
    "shmget": "allowed",
    "big_alloc": "allowed",
}
try:
    with open("/work/probe", "w", encoding="utf-8") as handle:
        handle.write("x")
    result["work_write"] = True
except OSError:
    pass
try:
    os.fork()
    result["fork"] = "allowed"
except OSError:
    result["fork"] = "blocked"
try:
    import threading
    threading.Thread(target=lambda: None).start()
    result["thread"] = "allowed"
except RuntimeError:
    result["thread"] = "blocked"
except OSError:
    result["thread"] = "blocked"
try:
    import subprocess
    subprocess.run(["/usr/bin/true"], check=False)
    result["subprocess"] = "allowed"
except (OSError, subprocess.SubprocessError):
    result["subprocess"] = "blocked"
try:
    fd = os.memfd_create("probe")
    os.write(fd, b"x" * 1048576)
    result["memfd"] = "allowed"
except OSError:
    result["memfd"] = "blocked"
try:
    import ctypes, ctypes.util
    libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    libc.shmget.restype = ctypes.c_int
    rc = libc.shmget(0, 4096, 0)
    result["shmget"] = "blocked" if rc == -1 else "allowed"
except OSError:
    result["shmget"] = "blocked"
except Exception:
    result["shmget"] = "blocked"
try:
    bytearray(80 * 1024 * 1024)
    result["big_alloc"] = "allowed"
except MemoryError:
    result["big_alloc"] = "blocked"
print("PROBE " + json.dumps(result))
"""


def _parse_probe(stdout: str) -> Optional[Dict[str, Any]]:
    for line in stdout.splitlines():
        if line.startswith("PROBE "):
            try:
                payload = json.loads(line[6:])
            except ValueError:
                return None
            return payload if isinstance(payload, dict) else None
    return None


def _probe_failure(payload: Dict[str, Any]) -> Optional[str]:
    expected_env = {
        "PATH", "LANG", "LC_ALL", "PYTHONIOENCODING", "PYTHONDONTWRITEBYTECODE",
        "LD_LIBRARY_PATH", "PWD",
    }
    extra_env = set(payload.get("env") or []) - expected_env
    if extra_env:
        return f"sandbox наследует лишние env: {sorted(extra_env)}"
    if payload.get("cwd") != "/work":
        return "рабочий каталог sandbox не /work"
    if payload.get("shm"):
        return "в sandbox доступен /dev/shm"
    if payload.get("tmp"):
        return "в sandbox доступен общий /tmp"
    if payload.get("work_write") is not True:
        return "/work в sandbox недоступен для записи"
    for key, label in (
        ("fork", "fork"), ("thread", "threads"), ("subprocess", "subprocess"),
        ("memfd", "memfd_create"), ("shmget", "SysV shm"), ("big_alloc", "memory limit"),
    ):
        if payload.get(key) != "blocked":
            return f"не удалось ограничить {label} ({key})"
    return None


def sandbox_availability(*, refresh: bool = False) -> Tuple[bool, str]:
    """Cached probe: is every hard boundary (limits, seccomp, mounts) real?"""
    global _PROBE
    with _PROBE_LOCK:
        if _PROBE is not None and not refresh:
            return _PROBE
        input_dir: Optional[Path] = None
        seccomp_fd: Optional[int] = None
        try:
            _, python = _checked_tooling()
            input_dir = Path(tempfile.mkdtemp(prefix="custom-check-probe-"))
            os.chmod(input_dir, 0o700)
            _, seccomp_fd = _write_seccomp_file(input_dir)
            limits = SandboxLimits(timeout_seconds=5, memory_mb=64, max_output_bytes=65536)
            command = _sandbox_argv(
                python, limits, seccomp_fd, [],
                [python, "-I", "-B", "-c", _PROBE_SCRIPT],
            )
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_host_env(),
                timeout=10,
                start_new_session=True,
                pass_fds=(seccomp_fd,),
            )
            payload = _parse_probe(completed.stdout.decode("utf-8", "replace"))
            if completed.returncode != 0 or payload is None:
                detail = completed.stderr.decode("utf-8", "replace").strip().splitlines()
                _PROBE = (
                    False,
                    "bwrap не может поднять изоляцию: "
                    + (detail[0][:200] if detail else f"exit {completed.returncode}"),
                )
            else:
                failure = _probe_failure(payload)
                if failure:
                    _PROBE = (False, f"sandbox не изолирует: {failure}")
                else:
                    _PROBE = (True, "bwrap-python3: nproc=1, seccomp, AS/CPU/FSIZE, /work 1MiB")
        except (SandboxUnavailable, OSError, subprocess.SubprocessError) as exc:
            _PROBE = (False, f"Sandbox недоступен: {exc}")
        finally:
            if seccomp_fd is not None:
                try:
                    os.close(seccomp_fd)
                except OSError:
                    pass
            if input_dir is not None:
                shutil.rmtree(input_dir, ignore_errors=True)
        return _PROBE


def _kill(proc: subprocess.Popen[bytes]) -> None:
    """Kill the whole sandbox. bwrap tears down the PID namespace with it."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def _read_bounded(
    proc: subprocess.Popen[bytes],
    max_bytes: int,
    deadline: float,
    cancel_event: Optional[threading.Event] = None,
) -> tuple[str, str, str]:
    selector = selectors.DefaultSelector()
    streams = {}
    for name, stream in (("stdout", proc.stdout), ("stderr", proc.stderr)):
        if stream is not None:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
            streams[name] = bytearray()
    total = 0
    status = "ok"

    def absorb(bucket: bytearray, chunk: bytes) -> bool:
        nonlocal total
        remaining = max_bytes - total
        if len(chunk) > remaining:
            bucket.extend(chunk[: max(0, remaining)])
            total = max_bytes
            return False
        bucket.extend(chunk)
        total += len(chunk)
        return True

    while True:
        if cancel_event is not None and cancel_event.is_set():
            _kill(proc)
            status = "cancelled"
            break
        remaining_time = deadline - time.monotonic()
        if remaining_time <= 0:
            _kill(proc)
            status = "timeout"
            break
        events = selector.select(min(0.1, remaining_time))
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
            if not absorb(streams[key.data], chunk):
                _kill(proc)
                status = "output_limit"
                break
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
                    if not absorb(streams[key.data], chunk):
                        status = "output_limit"
                        break
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
        "maxTasks": MAX_TASKS,
        "workScratchBytes": WORK_TMPFS_BYTES,
        "devScratchBytes": DEV_TMPFS_BYTES,
        "maxOpenFiles": MAX_OPEN_FILES,
    }


def run_sandbox(
    script: str,
    project_path: str,
    artifact: Dict[str, Any],
    limits: SandboxLimits,
    cancel_event: Optional[threading.Event] = None,
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
    if cancel_event is not None and cancel_event.is_set():
        raise SandboxCancelled("Проверка отменена до запуска")
    project = _safe_project(Path(project_path))
    _, python = _checked_tooling()
    input_dir = Path(tempfile.mkdtemp(prefix="custom-check-"))
    seccomp_fd: Optional[int] = None
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
        _, seccomp_fd = _write_seccomp_file(input_dir)
        command = _sandbox_argv(
            python,
            limits,
            seccomp_fd,
            [(str(input_dir), "/input"), (str(project), "/project")],
            [python, "-I", "-B", "/input/check.py", "/input/input.json"],
        )
        started = time.monotonic()
        try:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_host_env(),
                start_new_session=True,
                pass_fds=(seccomp_fd,),
            )
        except OSError as exc:
            raise SandboxUnavailable(f"Sandbox не запустился: {exc}") from exc
        stdout, stderr, read_status = _read_bounded(
            proc, limits.max_output_bytes, started + limits.timeout_seconds, cancel_event
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        diagnostics: List[Dict[str, Any]] = []
        if read_status == "cancelled" or (cancel_event is not None and cancel_event.is_set()):
            raise SandboxCancelled("Проверка отменена")
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
        if seccomp_fd is not None:
            try:
                os.close(seccomp_fd)
            except OSError:
                pass
        shutil.rmtree(input_dir, ignore_errors=True)
