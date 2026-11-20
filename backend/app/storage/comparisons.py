"""Storage for deterministic comparison results (data_contract.comparisons).

The comparison engine builds ComparisonDto from two published envelopes; this
module persists the result atomically and reads it back for the API. SQL still
owns publication: the header starts as draft, members are inserted, then the
header is published and ``guard_comparison`` validates bundles, signatures,
fingerprints and coverage. No pseudo-published comparison is possible.

Deleted files are derived from the two sealed manifests on read; the DTO
``deletedFiles`` field is not stored separately.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence

from backend.app.storage.canonical import (
    CanonicalConflictError, _arr, _enum, _lock, _obj, _require, _text, _uuid,
)
from backend.app.storage.database import _workspace_id, get_pool

STATUSES = ("persistent", "introduced", "changed", "resolved", "inconclusive", "file_deleted")
BASES = ("unique_anchor", "ambiguous", "none")


class ComparisonValidationError(ValueError):
    pass


class ComparisonStore:
    def __init__(self, workspace_id: Optional[str] = None):
        self.workspace_id = _uuid(str(workspace_id or _workspace_id()), "workspaceId")

    def save(self, comparison: Mapping[str, Any]) -> Dict[str, Any]:
        item_input = _obj(comparison, "comparison")
        input_workspace = item_input.get("workspaceId")
        if input_workspace is not None:
            _require(_uuid(input_workspace, "comparison.workspaceId") == self.workspace_id,
                     "comparison: workspace differs from store")
        comparison_id = _uuid(item_input.get("id"), "comparison.id")
        baseline_run = _uuid(item_input.get("baselineRunId"), "comparison.baselineRunId")
        baseline_snapshot = _uuid(item_input.get("baselineSnapshotId"), "comparison.baselineSnapshotId")
        current_run = _uuid(item_input.get("currentRunId"), "comparison.currentRunId")
        current_snapshot = _uuid(item_input.get("currentSnapshotId"), "comparison.currentSnapshotId")
        _require(baseline_run != current_run, "comparison: baseline and current runs must differ")
        comparable = item_input.get("comparable")
        _require(isinstance(comparable, bool), "comparison.comparable: expected boolean")
        reasons = [_text(x, f"comparison.reasons[{i}]") for i, x in enumerate(_arr(item_input.get("reasons"), "comparison.reasons"))]
        _require(comparable or reasons, "comparison: incomparable result requires reasons")
        items = [self._normalize_item(raw, index, baseline_run, baseline_snapshot,
                                      current_run, current_snapshot)
                 for index, raw in enumerate(_arr(item_input.get("items"), "comparison.items"))]

        with get_pool().connection() as conn:
            _lock(conn, "comparison", self.workspace_id, comparison_id)
            existing = conn.execute(
                "SELECT baseline_run_id, baseline_snapshot_id, current_run_id, current_snapshot_id,"
                " comparable, reasons FROM data_contract.comparisons "
                "WHERE workspace_id = %s AND comparison_id = %s AND published_at IS NOT NULL",
                (self.workspace_id, comparison_id),
            ).fetchone()
            if existing is not None:
                same_header = (
                    str(existing["baseline_run_id"]) == baseline_run
                    and str(existing["baseline_snapshot_id"]) == baseline_snapshot
                    and str(existing["current_run_id"]) == current_run
                    and str(existing["current_snapshot_id"]) == current_snapshot
                    and existing["comparable"] == comparable
                    and list(existing["reasons"]) == reasons
                )
                stored = self._get_with_conn(conn, comparison_id)
                if (not same_header
                        or stored is None
                        or self._item_key(stored["items"]) != self._item_key(items)):
                    raise ComparisonConflictError(
                        "comparison id is already published with a different header or items"
                    )
                return stored

            conn.execute(
                """
                INSERT INTO data_contract.comparisons (
                    workspace_id, comparison_id,
                    baseline_run_id, baseline_snapshot_id,
                    current_run_id, current_snapshot_id,
                    comparable, reasons
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (self.workspace_id, comparison_id, baseline_run, baseline_snapshot,
                 current_run, current_snapshot, comparable, reasons),
            )
            for item in items:
                conn.execute(
                    """
                    INSERT INTO data_contract.comparison_items (
                        workspace_id, comparison_id, item_id,
                        baseline_run_id, baseline_snapshot_id,
                        current_run_id, current_snapshot_id,
                        baseline_finding_id, current_finding_id,
                        status, matching_basis, reason
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (self.workspace_id, comparison_id, item["id"],
                     baseline_run, baseline_snapshot, current_run, current_snapshot,
                     item["baselineFindingId"], item["currentFindingId"],
                     item["status"], item["matchingBasis"], item["reason"]),
                )
            conn.execute(
                "UPDATE data_contract.comparisons SET published_at = clock_timestamp() "
                "WHERE workspace_id = %s AND comparison_id = %s AND published_at IS NULL",
                (self.workspace_id, comparison_id),
            )
            return self._get_with_conn(conn, comparison_id)

    def get(self, comparison_id: str) -> Optional[Dict[str, Any]]:
        with get_pool().connection() as conn:
            return self._get_with_conn(conn, _uuid(str(comparison_id), "comparison.id"))

    def list(self, limit: int = 50) -> List[Dict[str, Any]]:
        _require(isinstance(limit, int) and 1 <= limit <= 1000, "limit: expected 1..1000")
        with get_pool().connection() as conn:
            rows = conn.execute(
                "SELECT comparison_id FROM data_contract.comparisons "
                "WHERE workspace_id = %s AND published_at IS NOT NULL "
                "ORDER BY published_at DESC LIMIT %s",
                (self.workspace_id, limit),
            ).fetchall()
            return [self._get_with_conn(conn, str(row["comparison_id"])) for row in rows]

    def _get_with_conn(self, conn, comparison_id: str) -> Optional[Dict[str, Any]]:
        header = conn.execute(
            """
            SELECT comparison_id, baseline_run_id, baseline_snapshot_id,
                   current_run_id, current_snapshot_id, comparable, reasons, published_at
              FROM data_contract.comparisons
             WHERE workspace_id = %s AND comparison_id = %s AND published_at IS NOT NULL
            """,
            (self.workspace_id, comparison_id),
        ).fetchone()
        if header is None:
            return None
        rows = conn.execute(
            """
            SELECT item_id, baseline_finding_id, current_finding_id,
                   status, matching_basis, reason
              FROM data_contract.comparison_items
             WHERE workspace_id = %s AND comparison_id = %s
             ORDER BY item_id
            """,
            (self.workspace_id, comparison_id),
        ).fetchall()
        deleted = conn.execute(
            """
            SELECT baseline.path, baseline.source_hash
              FROM data_contract.snapshot_files baseline
             WHERE baseline.workspace_id = %s AND baseline.snapshot_id = %s
               AND NOT EXISTS (
                   SELECT 1 FROM data_contract.snapshot_files current
                    WHERE current.workspace_id = baseline.workspace_id
                      AND current.snapshot_id = %s AND current.path = baseline.path
               )
             ORDER BY baseline.path COLLATE "C"
            """,
            (self.workspace_id, header["baseline_snapshot_id"], header["current_snapshot_id"]),
        ).fetchall()
        return {
            "workspaceId": self.workspace_id,
            "id": str(header["comparison_id"]),
            "baselineRunId": str(header["baseline_run_id"]),
            "baselineSnapshotId": str(header["baseline_snapshot_id"]),
            "currentRunId": str(header["current_run_id"]),
            "currentSnapshotId": str(header["current_snapshot_id"]),
            "comparable": header["comparable"],
            "reasons": list(header["reasons"]),
            "items": [
                {
                    "id": str(row["item_id"]),
                    "baselineFindingId": str(row["baseline_finding_id"]) if row["baseline_finding_id"] else None,
                    "currentFindingId": str(row["current_finding_id"]) if row["current_finding_id"] else None,
                    "status": row["status"],
                    "matchingBasis": row["matching_basis"],
                    "reason": row["reason"],
                }
                for row in rows
            ],
            "deletedFiles": [
                {
                    "path": row["path"],
                    "baselineSourceHash": row["source_hash"],
                    "reason": "Файл отсутствует в current snapshot",
                }
                for row in deleted
            ],
            "publishedAt": header["published_at"].isoformat(),
        }

    def _normalize_item(self, raw: Any, index: int, baseline_run: str, baseline_snapshot: str,
                        current_run: str, current_snapshot: str) -> Dict[str, Any]:
        field = f"comparison.items[{index}]"
        item = _obj(raw, field)
        status = _enum(item.get("status"), f"{field}.status", STATUSES)
        basis = _enum(item.get("matchingBasis"), f"{field}.matchingBasis", BASES)
        reason = _text(item.get("reason"), f"{field}.reason")
        baseline_finding = item.get("baselineFindingId")
        current_finding = item.get("currentFindingId")
        if baseline_finding is not None:
            baseline_finding = _uuid(baseline_finding, f"{field}.baselineFindingId")
        if current_finding is not None:
            current_finding = _uuid(current_finding, f"{field}.currentFindingId")
        if status in ("persistent", "changed"):
            _require(baseline_finding is not None and current_finding is not None,
                     f"{field}: {status} requires both findings")
            _require(basis == "unique_anchor", f"{field}: {status} requires unique_anchor")
        elif status in ("resolved", "file_deleted"):
            _require(baseline_finding is not None and current_finding is None,
                     f"{field}: {status} requires baseline finding only")
            _require(status == "file_deleted" or basis == "unique_anchor",
                     f"{field}: resolved requires unique_anchor")
        elif status == "introduced":
            _require(baseline_finding is None and current_finding is not None,
                     f"{field}: introduced requires current finding only")
            _require(basis == "unique_anchor", f"{field}: introduced requires unique_anchor")
        else:  # inconclusive
            _require(baseline_finding is not None or current_finding is not None,
                     f"{field}: inconclusive requires at least one finding")
        return {
            "id": _uuid(item.get("id"), f"{field}.id"),
            "baselineFindingId": baseline_finding,
            "currentFindingId": current_finding,
            "status": status,
            "matchingBasis": basis,
            "reason": reason,
        }

    @staticmethod
    def _item_key(items: Sequence[Mapping[str, Any]]) -> List[Any]:
        return sorted(
            (item["id"], item["baselineFindingId"], item["currentFindingId"],
             item["status"], item["matchingBasis"], item["reason"])
            for item in items
        )


class ComparisonConflictError(CanonicalConflictError):
    """Comparison id already published with different content."""