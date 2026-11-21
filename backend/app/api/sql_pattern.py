"""Supplemental SQL-pattern proof from two published scanner envelopes.

This is not a replacement for compareBundles/ComparisonStore and never marks a
general comparison comparable or creates a vetted fix. SQL worker owns the
Node CLI and DTO shape at packages/comparison/sql-rule-cli.mjs.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Optional

from backend.app.api.comparison import ComparisonError, load_published_envelope
from backend.app.codefacts.store import CodeFactsStore


def _bounded_env(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
        if not math.isfinite(value):
            return default
        return max(low, min(high, value))
    except ValueError:
        return default


_SQL_SEM = threading.BoundedSemaphore(int(_bounded_env("CG_MAX_CONCURRENT_SQL_PATTERN", 1, 1, 8)))
_MAX_OUTPUT_BYTES = int(_bounded_env("CG_SQL_PATTERN_MAX_OUTPUT_BYTES", 2 * 1024 * 1024, 1024, 8 * 1024 * 1024))
_TIMEOUT = _bounded_env("CG_SQL_PATTERN_TIMEOUT_SECONDS", 15, 0.1, 60)


def _run_cli(command: list[str]) -> tuple[int, str, str]:
    """Drain both pipes with a byte cap; reject excess output before buffering it."""
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        oversized = threading.Event()

        def read_output(pipe) -> bytes:
            chunks = []
            total = 0
            while True:
                chunk = pipe.read1(65536)
                if not chunk:
                    return b"".join(chunks)
                total += len(chunk)
                if total > _MAX_OUTPUT_BYTES:
                    oversized.set()
                    process.kill()
                    return b""
                chunks.append(chunk)

        with ThreadPoolExecutor(max_workers=2) as readers:
            stdout = readers.submit(read_output, process.stdout)
            stderr = readers.submit(read_output, process.stderr)
            try:
                process.wait(timeout=_TIMEOUT)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise
        if oversized.is_set():
            raise ComparisonError(502, "SQL_PATTERN_OUTPUT_TOO_LARGE", "sql-rule-cli превысил лимит вывода")
        return (
            process.returncode,
            stdout.result().decode("utf-8", errors="replace"),
            stderr.result().decode("utf-8", errors="replace"),
        )


def sql_pattern_cli_path() -> Path:
    override = os.getenv("CG_SQL_PATTERN_CLI", "").strip()
    if override:
        return Path(override).resolve()
    return Path(__file__).resolve().parents[3] / "packages" / "comparison" / "sql-rule-cli.mjs"


def sql_pattern_proof(*, workspace: str, baseline_run_id: str, current_run_id: str,
                      baseline_run: Dict[str, Any], current_run: Dict[str, Any],
                      store: Optional[CodeFactsStore] = None) -> Dict[str, Any]:
    if baseline_run_id == current_run_id:
        raise ComparisonError(400, "SAME_RUN", "Baseline и current run должны различаться")
    store = store or CodeFactsStore()
    cli = sql_pattern_cli_path()
    if not cli.is_file():
        raise ComparisonError(503, "SQL_PATTERN_CLI_MISSING", "packages/comparison/sql-rule-cli.mjs не найден")
    before = load_published_envelope(workspace, baseline_run_id, baseline_run, store)
    after = load_published_envelope(workspace, current_run_id, current_run, store)
    node = os.getenv("CG_NODE_BIN", "node")
    if not _SQL_SEM.acquire(blocking=False):
        raise ComparisonError(429, "SQL_PATTERN_BUSY", "SQL-pattern проверка уже выполняется")
    try:
        with tempfile.TemporaryDirectory(prefix="cg_sql_pattern_") as temp_dir:
            os.chmod(temp_dir, 0o700)
            before_path = Path(temp_dir) / "baseline.json"
            after_path = Path(temp_dir) / "current.json"
            before_path.write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")
            after_path.write_text(json.dumps(after, ensure_ascii=False), encoding="utf-8")
            os.chmod(before_path, 0o600)
            os.chmod(after_path, 0o600)
            try:
                returncode, stdout_text, stderr_text = _run_cli(
                    [node, str(cli), str(before_path), str(after_path)],
                )
            except FileNotFoundError as exc:
                raise ComparisonError(503, "NODE_MISSING", f"Node 22 не найден: {node}") from exc
            except subprocess.TimeoutExpired as exc:
                raise ComparisonError(504, "SQL_PATTERN_TIMEOUT", "SQL-pattern проверка превысила лимит") from exc
    finally:
        _SQL_SEM.release()
    if returncode != 0:
        detail = (stderr_text or stdout_text or "sql-rule-cli rejected").strip().splitlines()
        raise ComparisonError(422, "SQL_PATTERN_REJECTED", (detail[-1] if detail else "rejected")[:500])
    try:
        result = json.loads(stdout_text)
    except ValueError as exc:
        raise ComparisonError(502, "SQL_PATTERN_BAD_OUTPUT", "sql-rule-cli вернул не JSON") from exc
    if not isinstance(result, dict):
        raise ComparisonError(502, "SQL_PATTERN_BAD_OUTPUT", "sql-rule-cli вернул не объект")
    return result
