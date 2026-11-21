"""История и сводка запусков реального аккаунта.

Аккаунт получает собственный workspace (``account.users.workspace_id``),
поэтому вся история — это строки ``runtime.runs`` этого workspace. Отдельных
bookmark-таблиц и nonce-псевдонимов больше нет: профиль не подделывается под
то, чем он не является.

Файл не содержит авторизации: вызывающий обязан передать workspace из
проверенной сессии.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List

from backend.app.storage.database import get_pool

MAX_HISTORY_LIMIT = 100
DEFAULT_HISTORY_LIMIT = 20

_RUN_COLUMNS = (
    "r.id",
    "r.workflow_id",
    "r.revision_id",
    "r.artifact_id",
    "r.trigger_type",
    "r.execution_status",
    "r.security_verdict",
    "r.total_findings",
    "r.critical_count",
    "r.high_count",
    "r.medium_count",
    "r.low_count",
    "r.info_count",
    "r.started_at",
    "r.finished_at",
    "r.created_at",
)


def _workspace_uuid(value: Any) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("workspace должен быть UUID") from exc


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def run_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Безопасная проекция runtime.runs: workspace_id наружу не идёт."""
    return {key: _iso(value) for key, value in row.items() if key != "workspace_id"}


def run_history(workspace_id: Any, limit: int = DEFAULT_HISTORY_LIMIT) -> List[Dict[str, Any]]:
    ws = _workspace_uuid(workspace_id)
    safe_limit = max(1, min(int(limit), MAX_HISTORY_LIMIT))
    with get_pool().connection() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(_RUN_COLUMNS)} FROM runtime.runs r "
            "WHERE r.workspace_id = %s "
            "ORDER BY r.created_at DESC, r.id LIMIT %s",
            (ws, safe_limit),
        ).fetchall()
    return [run_row(dict(row)) for row in rows]


def history_page(
    workspace_id: Any, *, limit: int = DEFAULT_HISTORY_LIMIT, offset: int = 0,
    project: str | None = None, workflow: str | None = None,
    status: str | None = None, since: datetime | None = None,
    until: datetime | None = None,
) -> Dict[str, Any]:
    """Фильтры применяются ко всей истории; until — исключительная граница."""
    ws = _workspace_uuid(workspace_id)
    conditions = ["r.workspace_id = %s"]
    params: List[Any] = [ws]
    if project == "__none__":
        conditions.append("r.artifact_id IS NULL")
    elif project:
        conditions.append("r.artifact_id = %s")
        params.append(project)
    for column, value in (("workflow_id", workflow), ("execution_status", status)):
        if value:
            conditions.append(f"r.{column} = %s")
            params.append(value)
    for operator, value in ((">=", since), ("<", until)):
        if value:
            conditions.append(f"r.created_at {operator} %s")
            params.append(value)
    where = " AND ".join(conditions)
    joins = (
        " FROM runtime.runs r LEFT JOIN runtime.workflows w "
        "ON w.workspace_id = r.workspace_id AND w.id = r.workflow_id "
        "LEFT JOIN runtime.artifacts a "
        "ON a.workspace_id = r.workspace_id AND a.id = r.artifact_id "
    )
    with get_pool().connection() as conn:
        # Count, page and options describe one snapshot, including during a new run.
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        total = conn.execute(
            f"SELECT count(*) AS total FROM runtime.runs r WHERE {where}", params,
        ).fetchone()["total"]
        rows = conn.execute(
            f"SELECT {', '.join(_RUN_COLUMNS)}, w.name AS workflow_name, "
            f"a.filename AS project_name {joins} WHERE {where} "
            "ORDER BY r.created_at DESC, r.id LIMIT %s OFFSET %s",
            [*params, limit, offset],
        ).fetchall()
        projects = conn.execute(
            "SELECT DISTINCT r.artifact_id AS id, a.filename AS name "
            f"{joins} WHERE r.workspace_id = %s ORDER BY name NULLS LAST, id", (ws,),
        ).fetchall()
        workflows = conn.execute(
            "SELECT DISTINCT r.workflow_id AS id, w.name "
            f"{joins} WHERE r.workspace_id = %s ORDER BY name NULLS LAST, id", (ws,),
        ).fetchall()
        statuses = conn.execute(
            "SELECT DISTINCT execution_status FROM runtime.runs "
            "WHERE workspace_id = %s ORDER BY execution_status", (ws,),
        ).fetchall()
    return {
        "items": [run_row(dict(row)) for row in rows],
        "total": total, "limit": limit, "offset": offset,
        "projects": [dict(row) for row in projects],
        "workflows": [dict(row) for row in workflows],
        "statuses": [row["execution_status"] for row in statuses],
    }


def run_summary(workspace_id: Any) -> Dict[str, Any]:
    ws = _workspace_uuid(workspace_id)
    with get_pool().connection() as conn:
        row = conn.execute(
            """
            SELECT count(*) AS run_count,
                   max(created_at) AS last_run_at,
                   count(*) FILTER (WHERE execution_status = 'queued') AS queued,
                   count(*) FILTER (WHERE execution_status = 'running') AS running,
                   count(*) FILTER (WHERE execution_status = 'succeeded') AS succeeded,
                   count(*) FILTER (WHERE execution_status = 'failed') AS failed,
                   count(*) FILTER (WHERE security_verdict = 'pass') AS verdict_pass,
                   count(*) FILTER (WHERE security_verdict = 'fail') AS verdict_fail,
                   COALESCE(sum(total_findings), 0) AS total_findings
              FROM runtime.runs WHERE workspace_id = %s
            """,
            (ws,),
        ).fetchone()
    return {
        "runs": int(row["run_count"]),
        "lastRunAt": _iso(row["last_run_at"]),
        "byStatus": {
            "queued": int(row["queued"]),
            "running": int(row["running"]),
            "succeeded": int(row["succeeded"]),
            "failed": int(row["failed"]),
        },
        "byVerdict": {
            "pass": int(row["verdict_pass"]),
            "fail": int(row["verdict_fail"]),
        },
        "totalFindings": int(row["total_findings"]),
    }
