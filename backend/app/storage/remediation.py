"""Remediation examples storage (data_contract.remediation_examples).

Only verified before/after evidence may be inserted as ``vetted``; everything
else stays ``draft``. A vetted row requires a published source bundle (and a
published fixed bundle when given), non-empty verification basis, reviewer and
review timestamp. SQL ``guard_example`` enforces the published part; this store
enforces the textual basis and content hash.

No AI-generated advice is promoted to ``vetted`` here. Limitations are stored
explicitly so the API can show them next to the fix.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional

from backend.app.storage.canonical import (
    CanonicalConflictError,
    _arr,
    _canonical_bytes,
    _enum,
    _obj,
    _require,
    _sha,
    _text,
    _uuid,
)
from backend.app.storage.database import _workspace_id, get_pool

STATUSES = ("draft", "vetted", "retired")


class RemediationValidationError(ValueError):
    pass


def remediation_content_hash(workspace_id: str, example: Mapping[str, Any]) -> str:
    payload = [
        "remediation/v1",
        str(workspace_id),
        example["sourceRef"],
        example["language"],
        example["framework"],
        example["ruleFamily"],
        example["ruleVersion"],
        example["scenarioVersion"],
        example["redactedBefore"],
        example["redactedFix"],
    ]
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


class RemediationStore:
    def __init__(self, workspace_id: Optional[str] = None):
        self.workspace_id = _uuid(str(workspace_id or _workspace_id()), "workspaceId")

    def save(self, example: Mapping[str, Any]) -> Dict[str, Any]:
        row = self._normalize(example)
        with get_pool().connection() as conn:
            conn.execute(
                """
                INSERT INTO data_contract.remediation_examples (
                    workspace_id, example_id,
                    source_run_id, source_snapshot_id, source_finding_id,
                    fixed_run_id, fixed_snapshot_id,
                    source_ref, content_hash,
                    language, framework, rule_family, rule_version, scenario_version,
                    redacted_before, redacted_fix,
                    status, verification_basis, reviewed_by, reviewed_at, limitations
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s::timestamptz, %s
                )
                ON CONFLICT (workspace_id, example_id) DO NOTHING
                """,
                (
                    self.workspace_id, row["exampleId"],
                    row["sourceRunId"], row["sourceSnapshotId"], row["sourceFindingId"],
                    row["fixedRunId"], row["fixedSnapshotId"],
                    row["sourceRef"], row["contentHash"],
                    row["language"], row["framework"], row["ruleFamily"],
                    row["ruleVersion"], row["scenarioVersion"],
                    row["redactedBefore"], row["redactedFix"],
                    row["status"], row["verificationBasis"], row["reviewedBy"],
                    row["reviewedAt"], row["limitations"],
                ),
            )
            stored = self._get_with_conn(conn, row["exampleId"])
            if stored is None:  # pragma: no cover
                raise RemediationValidationError("example insert was not persisted")
            if stored["contentHash"] != row["contentHash"]:
                raise CanonicalConflictError(
                    "remediation example id already exists with a different content hash"
                )
            return stored

    def get(self, example_id: str) -> Optional[Dict[str, Any]]:
        with get_pool().connection() as conn:
            return self._get_with_conn(conn, _uuid(str(example_id), "exampleId"))

    def list(self, status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        _require(isinstance(limit, int) and 1 <= limit <= 1000, "limit: expected 1..1000")
        params: List[Any] = [self.workspace_id]
        where = "workspace_id = %s"
        if status is not None:
            _enum(status, "status", STATUSES)
            where += " AND status = %s"
            params.append(status)
        params.append(limit)
        with get_pool().connection() as conn:
            rows = conn.execute(
                f"SELECT example_id FROM data_contract.remediation_examples WHERE {where} "
                "ORDER BY example_id LIMIT %s",
                params,
            ).fetchall()
            return [self._get_with_conn(conn, str(row["example_id"])) for row in rows]

    def _normalize(self, example: Mapping[str, Any]) -> Dict[str, Any]:
        item = _obj(example, "example")
        source_run = _uuid(item.get("sourceRunId"), "example.sourceRunId")
        source_snapshot = _uuid(item.get("sourceSnapshotId"), "example.sourceSnapshotId")
        source_finding = _uuid(item.get("sourceFindingId"), "example.sourceFindingId")
        fixed_run = item.get("fixedRunId")
        fixed_snapshot = item.get("fixedSnapshotId")
        if fixed_run is not None or fixed_snapshot is not None:
            _require(fixed_run is not None and fixed_snapshot is not None,
                     "example: fixedRunId and fixedSnapshotId must be given together")
            fixed_run = _uuid(fixed_run, "example.fixedRunId")
            fixed_snapshot = _uuid(fixed_snapshot, "example.fixedSnapshotId")
        status = _enum(item.get("status"), "example.status", STATUSES)
        reviewed_at = item.get("reviewedAt")
        if reviewed_at is not None:
            _require(isinstance(reviewed_at, (str, datetime)), "example.reviewedAt: expected timestamp")
            reviewed_at = reviewed_at.isoformat() if isinstance(reviewed_at, datetime) else reviewed_at
        verification_basis = item.get("verificationBasis")
        if verification_basis is not None:
            verification_basis = _text(verification_basis, "example.verificationBasis")
        reviewed_by = item.get("reviewedBy")
        if reviewed_by is not None:
            reviewed_by = _text(reviewed_by, "example.reviewedBy")
        limitations = [
            _text(x, f"example.limitations[{i}]")
            for i, x in enumerate(_arr(item.get("limitations"), "example.limitations"))
        ]
        if status == "vetted":
            _require(verification_basis is not None, "example: vetted requires verificationBasis")
            _require(reviewed_by is not None, "example: vetted requires reviewedBy")
            _require(reviewed_at is not None, "example: vetted requires reviewedAt")
            _require(limitations, "example: vetted requires explicit limitations")
        row = {
            "exampleId": _uuid(item.get("exampleId"), "example.exampleId"),
            "sourceRunId": source_run,
            "sourceSnapshotId": source_snapshot,
            "sourceFindingId": source_finding,
            "fixedRunId": fixed_run,
            "fixedSnapshotId": fixed_snapshot,
            "sourceRef": _text(item.get("sourceRef"), "example.sourceRef"),
            "language": _text(item.get("language"), "example.language"),
            "framework": _text(item.get("framework"), "example.framework"),
            "ruleFamily": _text(item.get("ruleFamily"), "example.ruleFamily"),
            "ruleVersion": _text(item.get("ruleVersion"), "example.ruleVersion"),
            "scenarioVersion": _text(item.get("scenarioVersion"), "example.scenarioVersion"),
            "redactedBefore": _text(item.get("redactedBefore"), "example.redactedBefore"),
            "redactedFix": _text(item.get("redactedFix"), "example.redactedFix"),
            "status": status,
            "verificationBasis": verification_basis,
            "reviewedBy": reviewed_by,
            "reviewedAt": reviewed_at,
            "limitations": limitations,
        }
        row["contentHash"] = remediation_content_hash(self.workspace_id, row)
        provided_hash = item.get("contentHash")
        if provided_hash is not None:
            _require(_sha(provided_hash, "example.contentHash") == row["contentHash"],
                     "example: contentHash differs from the canonical recipe")
        return row

    def _get_with_conn(self, conn, example_id: str) -> Optional[Dict[str, Any]]:
        row = conn.execute(
            """
            SELECT example_id, source_run_id, source_snapshot_id, source_finding_id,
                   fixed_run_id, fixed_snapshot_id, source_ref, content_hash,
                   language, framework, rule_family, rule_version, scenario_version,
                   redacted_before, redacted_fix, status, verification_basis,
                   reviewed_by, reviewed_at, limitations
              FROM data_contract.remediation_examples
             WHERE workspace_id = %s AND example_id = %s
            """,
            (self.workspace_id, example_id),
        ).fetchone()
        if row is None:
            return None
        return {
            "workspaceId": self.workspace_id,
            "exampleId": str(row["example_id"]),
            "sourceRunId": str(row["source_run_id"]),
            "sourceSnapshotId": str(row["source_snapshot_id"]),
            "sourceFindingId": str(row["source_finding_id"]),
            "fixedRunId": str(row["fixed_run_id"]) if row["fixed_run_id"] else None,
            "fixedSnapshotId": str(row["fixed_snapshot_id"]) if row["fixed_snapshot_id"] else None,
            "sourceRef": row["source_ref"],
            "contentHash": row["content_hash"],
            "language": row["language"],
            "framework": row["framework"],
            "ruleFamily": row["rule_family"],
            "ruleVersion": row["rule_version"],
            "scenarioVersion": row["scenario_version"],
            "redactedBefore": row["redacted_before"],
            "redactedFix": row["redacted_fix"],
            "status": row["status"],
            "verificationBasis": row["verification_basis"],
            "reviewedBy": row["reviewed_by"],
            "reviewedAt": row["reviewed_at"].isoformat() if row["reviewed_at"] else None,
            "limitations": list(row["limitations"]),
        }