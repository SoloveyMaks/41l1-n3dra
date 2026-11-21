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
from pathlib import Path
from typing import Any, Dict, Optional

from backend.app.codefacts.identity import run_uuid
from backend.app.codefacts.publish import get_published_envelope
from backend.app.codefacts.store import CodeFactsStore


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
    with tempfile.TemporaryDirectory(prefix="cg_compare_") as temp_dir:
        before_path = Path(temp_dir) / "baseline.json"
        after_path = Path(temp_dir) / "current.json"
        before_path.write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")
        after_path.write_text(json.dumps(after, ensure_ascii=False), encoding="utf-8")
        try:
            completed = subprocess.run(
                [node, str(cli), str(before_path), str(after_path)],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ComparisonError(503, "NODE_MISSING", f"Node 22 не найден: {node}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ComparisonError(504, "COMPARISON_TIMEOUT", "Сравнение превысило 60 секунд") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        reason = detail[-1] if detail else "comparison rejected"
        raise ComparisonError(422, "COMPARISON_REJECTED", reason[:500])
    try:
        result = json.loads(completed.stdout)
    except ValueError as exc:
        raise ComparisonError(502, "COMPARISON_BAD_OUTPUT", "compare-cli вернул не JSON") from exc
    required = {
        "workspaceId", "id", "baselineRunId", "baselineSnapshotId",
        "currentRunId", "currentSnapshotId", "comparable", "reasons", "items", "deletedFiles",
    }
    if not isinstance(result, dict) or not required.issubset(result):
        raise ComparisonError(502, "COMPARISON_BAD_OUTPUT", "compare-cli вернул неполный ComparisonDto")
    return result
