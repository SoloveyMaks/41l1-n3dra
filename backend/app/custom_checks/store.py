"""PostgreSQL persistence for custom-check definitions and sealed run results."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from psycopg.types.json import Jsonb

from backend.app.custom_checks.models import (
    CustomCheckDefinition,
    CustomCheckRunResult,
    CustomCheckRunSummary,
    CustomFinding,
)
from backend.app.storage.database import get_pool


class CustomCheckConflict(ValueError):
    pass


class CustomCheckNotFound(ValueError):
    pass


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _definition(row: Dict[str, Any]) -> CustomCheckDefinition:
    return CustomCheckDefinition(
        id=str(row["id"]),
        name=str(row["name"]),
        version=str(row["version"]),
        language=str(row["language"]),
        runner=str(row["runner"]),
        sourceKind=str(row["source_kind"]),
        templateId=row.get("template_id"),
        sourceSha256=str(row["source_sha256"]),
        timeoutSeconds=int(row["timeout_seconds"]),
        memoryMb=int(row["memory_mb"]),
        maxOutputBytes=int(row["max_output_bytes"]),
        createdBy=str(row["created_by"]),
        createdAt=_iso(row.get("created_at")),
        updatedAt=_iso(row.get("updated_at")),
        lastValidatedAt=_iso(row.get("last_validated_at")),
    )


class CustomCheckStore:
    def create_definition(
        self,
        workspace: str,
        *,
        name: str,
        version: str,
        source_kind: str,
        template_id: Optional[str],
        source_text: str,
        source_sha256: str,
        timeout_seconds: int,
        memory_mb: int,
        max_output_bytes: int,
        created_by: str,
        last_validated_at: Any = None,
    ) -> CustomCheckDefinition:
        check_id = str(uuid.uuid4())
        ws = uuid.UUID(str(workspace))
        try:
            with get_pool().connection() as conn:
                row = conn.execute(
                    """
                    INSERT INTO custom_checks.definitions (
                        workspace_id, id, name, version, language, runner,
                        source_kind, template_id, source_text, source_sha256,
                        timeout_seconds, memory_mb, max_output_bytes,
                        created_by, created_at, updated_at, last_validated_at, enabled
                    ) VALUES (
                        %s, %s, %s, %s, 'python', 'bwrap-python3',
                        %s, %s, %s, %s, %s, %s, %s, %s, now(), now(), %s, true
                    )
                    RETURNING *
                    """,
                    (
                        ws, check_id, name, version, source_kind, template_id,
                        source_text, source_sha256, timeout_seconds, memory_mb,
                        max_output_bytes, created_by, last_validated_at,
                    ),
                ).fetchone()
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "sqlstate", None) == "23505":
                raise CustomCheckConflict("Проверка с таким названием и версией уже существует") from None
            raise
        return _definition(dict(row))

    def list_definitions(self, workspace: str) -> List[CustomCheckDefinition]:
        ws = uuid.UUID(str(workspace))
        with get_pool().connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM custom_checks.definitions
                 WHERE workspace_id = %s AND enabled = true
                 ORDER BY updated_at DESC, name, version
                """,
                (ws,),
            ).fetchall()
        return [_definition(dict(row)) for row in rows]

    def get_definition(self, workspace: str, check_id: str) -> CustomCheckDefinition:
        try:
            parsed = uuid.UUID(str(check_id))
            ws = uuid.UUID(str(workspace))
        except (ValueError, TypeError, AttributeError) as exc:
            raise CustomCheckNotFound("Проверка не найдена") from exc
        with get_pool().connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM custom_checks.definitions
                 WHERE workspace_id = %s AND id = %s AND enabled = true
                """,
                (ws, parsed),
            ).fetchone()
        if not row:
            raise CustomCheckNotFound("Проверка не найдена")
        return _definition(dict(row))

    def get_source(self, workspace: str, check_id: str) -> str:
        self.get_definition(workspace, check_id)
        with get_pool().connection() as conn:
            row = conn.execute(
                "SELECT source_text FROM custom_checks.definitions WHERE workspace_id = %s AND id = %s",
                (uuid.UUID(str(workspace)), uuid.UUID(str(check_id))),
            ).fetchone()
        if not row:
            raise CustomCheckNotFound("Проверка не найдена")
        return str(row["source_text"])

    def mark_validated(self, workspace: str, check_id: str) -> None:
        with get_pool().connection() as conn:
            conn.execute(
                "UPDATE custom_checks.definitions SET last_validated_at = now(), updated_at = now() "
                "WHERE workspace_id = %s AND id = %s",
                (uuid.UUID(str(workspace)), uuid.UUID(str(check_id))),
            )

    def save_run(
        self,
        workspace: str,
        *,
        result: CustomCheckRunResult,
        artifact_hash: Optional[str] = None,
        workflow_run_id: Optional[str] = None,
    ) -> None:
        with get_pool().connection() as conn:
            conn.execute(
                """
                INSERT INTO custom_checks.runs (
                    workspace_id, id, definition_id, artifact_id, artifact_hash,
                    workflow_run_id, status, exit_code, duration_ms,
                    stdout_redacted, stderr_redacted, result_json,
                    diagnostics_json, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                """,
                (
                    uuid.UUID(str(workspace)), uuid.UUID(result.id), uuid.UUID(result.definitionId),
                    result.artifactId, artifact_hash, workflow_run_id, result.status,
                    result.exitCode, result.durationMs, result.stdout, result.stderr,
                    Jsonb({"findings": [item.model_dump() for item in result.findings]}),
                    Jsonb(result.diagnostics),
                ),
            )

    def list_workflow_runs(
        self, workspace: str, workflow_run_id: str
    ) -> List[CustomCheckRunSummary]:
        """Custom facts for one workflow run; never merged into runtime.findings."""
        with get_pool().connection() as conn:
            rows = conn.execute(
                """
                SELECT r.id, r.definition_id, r.status, r.duration_ms, r.created_at,
                       r.result_json, d.name AS definition_name, d.version AS definition_version
                  FROM custom_checks.runs r
                  JOIN custom_checks.definitions d
                    ON d.workspace_id = r.workspace_id AND d.id = r.definition_id
                 WHERE r.workspace_id = %s AND r.workflow_run_id = %s
                 ORDER BY r.created_at, r.id
                """,
                (uuid.UUID(str(workspace)), str(workflow_run_id)),
            ).fetchall()
        summaries: List[CustomCheckRunSummary] = []
        for row in rows:
            payload = row.get("result_json") or {}
            raw_findings = payload.get("findings") if isinstance(payload, dict) else []
            findings = [
                CustomFinding(**item)
                for item in (raw_findings or [])
                if isinstance(item, dict)
            ]
            summaries.append(
                CustomCheckRunSummary(
                    id=str(row["id"]),
                    definitionId=str(row["definition_id"]),
                    definitionName=str(row.get("definition_name") or ""),
                    definitionVersion=str(row.get("definition_version") or ""),
                    status=str(row["status"]),
                    durationMs=int(row.get("duration_ms") or 0),
                    createdAt=_iso(row.get("created_at")),
                    findings=findings,
                )
            )
        return summaries
