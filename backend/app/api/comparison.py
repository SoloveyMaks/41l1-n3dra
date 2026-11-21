"""Сравнение двух published runs через пакет packages/comparison (Node 22).

Пакет не запускает сканеры и не переписывает bundle: он получает ровно
опубликованные envelopes с storage manifests. Мы не превращаем partial coverage
в comparable и не ретроспективно чиним bundleHash.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from backend.app.codefacts.identity import run_uuid
from backend.app.codefacts.publish import get_published_envelope
from backend.app.codefacts.store import CodeFactsStore


_COMPARE_SEM = threading.BoundedSemaphore(int(os.getenv("CG_MAX_CONCURRENT_COMPARISONS", "1")))
_MAX_OUTPUT_BYTES = int(os.getenv("CG_COMPARISON_MAX_OUTPUT_BYTES", str(2 * 1024 * 1024)))
_COMPARE_TIMEOUT = float(os.getenv("CG_COMPARISON_TIMEOUT_SECONDS", "15"))


class ComparisonError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def comparison_cli_path() -> Path:
    return Path(__file__).resolve().parents[3] / "packages" / "comparison" / "compare-cli.mjs"


def _snapshot_id_for_run(*, workspace: str, run_id: str, run_row: Dict[str, Any],
                         store: CodeFactsStore) -> Optional[str]:
    payload = store.load_run(workspace, run_uuid(run_id))
    if payload:
        snapshot = payload.get("identity", {}).get("snapshotId")
        if snapshot:
            return str(snapshot)
    artifact_id = run_row.get("artifact_id")
    if artifact_id:
        meta = store.load_artifact_meta(workspace, str(artifact_id))
        if meta and meta.get("snapshotId"):
            return str(meta["snapshotId"])
    return None


def _published_envelope(workspace: str, run_id: str, run_row: Dict[str, Any],
                        store: CodeFactsStore) -> Dict[str, Any]:
    snapshot_id = _snapshot_id_for_run(workspace=workspace, run_id=run_id, run_row=run_row, store=store)
    if not snapshot_id:
        raise ComparisonError(409, "SNAPSHOT_UNKNOWN", f"Не найден snapshot для run {run_id}")
    published = get_published_envelope(workspace, run_uuid(run_id), snapshot_id)
    if not published:
        raise ComparisonError(
            409,
            "NOT_PUBLISHED",
            f"Run {run_id} не имеет published canonical envelope (или storage bridge недоступен)",
        )
    envelope = {
        "bundle": published["bundle"],
        "manifest": published.get("manifest"),
        "provenance": published.get("provenance"),
    }
    if not envelope["manifest"] or not envelope["provenance"]:
        raise ComparisonError(409, "CANONICAL_INCOMPLETE", f"Published envelope {run_id} без manifest/provenance")
    return envelope


def compare_published_runs(*, workspace: str, baseline_run_id: str, current_run_id: str,
                           baseline_run: Dict[str, Any], current_run: Dict[str, Any],
                           store: Optional[CodeFactsStore] = None) -> Dict[str, Any]:
    """Возвращает ComparisonDto из packages/comparison; не фабрикует status=resolved."""
    if baseline_run_id == current_run_id:
        raise ComparisonError(400, "SAME_RUN", "Baseline и current run должны различаться")
    store = store or CodeFactsStore()
    before = _published_envelope(workspace, baseline_run_id, baseline_run, store)
    after = _published_envelope(workspace, current_run_id, current_run, store)
    cli = comparison_cli_path()
    if not cli.is_file():
        raise ComparisonError(503, "COMPARISON_CLI_MISSING", "packages/comparison/compare-cli.mjs не найден")
    node = os.getenv("CG_NODE_BIN", "node")
    if not _COMPARE_SEM.acquire(blocking=False):
        raise ComparisonError(429, "COMPARISON_BUSY", "Сравнение уже выполняется; повторите позже")
    try:
        with tempfile.TemporaryDirectory(prefix="cg_compare_") as temp_dir:
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
                with stdout_path.open("wb") as stdout_handle, stderr_path.open("wb") as stderr_handle:
                    completed = subprocess.run(
                        [node, str(cli), str(before_path), str(after_path)],
                        stdout=stdout_handle,
                        stderr=stderr_handle,
                        timeout=_COMPARE_TIMEOUT,
                        check=False,
                    )
            except FileNotFoundError as exc:
                raise ComparisonError(503, "NODE_MISSING", f"Node 22 не найден: {node}") from exc
            except subprocess.TimeoutExpired as exc:
                raise ComparisonError(504, "COMPARISON_TIMEOUT", "Сравнение превысило лимит времени") from exc
            if stdout_path.stat().st_size > _MAX_OUTPUT_BYTES or stderr_path.stat().st_size > _MAX_OUTPUT_BYTES:
                raise ComparisonError(502, "COMPARISON_OUTPUT_TOO_LARGE", "compare-cli превысил лимит вывода")
            stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace")
            stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
    finally:
        _COMPARE_SEM.release()
    if completed.returncode != 0:
        detail = (stderr_text or stdout_text or "").strip().splitlines()
        reason = detail[-1] if detail else "comparison rejected"
        raise ComparisonError(422, "COMPARISON_REJECTED", reason[:500])
    try:
        result = json.loads(stdout_text)
    except ValueError as exc:
        raise ComparisonError(502, "COMPARISON_BAD_OUTPUT", "compare-cli вернул не JSON") from exc
    required = {
        "workspaceId", "id", "baselineRunId", "baselineSnapshotId",
        "currentRunId", "currentSnapshotId", "comparable", "reasons", "items", "deletedFiles",
    }
    if not isinstance(result, dict) or not required.issubset(result):
        raise ComparisonError(502, "COMPARISON_BAD_OUTPUT", "compare-cli вернул неполный ComparisonDto")
    return result
