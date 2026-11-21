"""Supplemental SQL-pattern proof from two published scanner envelopes.

This is not a replacement for compareBundles/ComparisonStore and never marks a
general comparison comparable or creates a vetted fix. SQL worker owns the
Node CLI and DTO shape at packages/comparison/sql-rule-cli.mjs.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from backend.app.api.comparison import ComparisonError, load_published_envelope
from backend.app.codefacts.store import CodeFactsStore

_SQL_SEM = threading.BoundedSemaphore(int(os.getenv("CG_MAX_CONCURRENT_SQL_PATTERN", "1")))
_MAX_OUTPUT_BYTES = int(os.getenv("CG_SQL_PATTERN_MAX_OUTPUT_BYTES", str(2 * 1024 * 1024)))
_TIMEOUT = float(os.getenv("CG_SQL_PATTERN_TIMEOUT_SECONDS", "15"))


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
            stdout_path = Path(temp_dir) / "stdout.json"
            stderr_path = Path(temp_dir) / "stderr.txt"
            try:
                with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
                    completed = subprocess.run(
                        [node, str(cli), str(before_path), str(after_path)],
                        stdout=out, stderr=err, timeout=_TIMEOUT, check=False,
                    )
            except FileNotFoundError as exc:
                raise ComparisonError(503, "NODE_MISSING", f"Node 22 не найден: {node}") from exc
            except subprocess.TimeoutExpired as exc:
                raise ComparisonError(504, "SQL_PATTERN_TIMEOUT", "SQL-pattern проверка превысила лимит") from exc
            if stdout_path.stat().st_size > _MAX_OUTPUT_BYTES or stderr_path.stat().st_size > _MAX_OUTPUT_BYTES:
                raise ComparisonError(502, "SQL_PATTERN_OUTPUT_TOO_LARGE", "sql-rule-cli превысил лимит вывода")
            stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace")
            stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
    finally:
        _SQL_SEM.release()
    if completed.returncode != 0:
        detail = (stderr_text or stdout_text or "sql-rule-cli rejected").strip().splitlines()
        raise ComparisonError(422, "SQL_PATTERN_REJECTED", (detail[-1] if detail else "rejected")[:500])
    try:
        result = json.loads(stdout_text)
    except ValueError as exc:
        raise ComparisonError(502, "SQL_PATTERN_BAD_OUTPUT", "sql-rule-cli вернул не JSON") from exc
    if not isinstance(result, dict):
        raise ComparisonError(502, "SQL_PATTERN_BAD_OUTPUT", "sql-rule-cli вернул не объект")
    return result
