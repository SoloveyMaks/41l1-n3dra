"""Public workflow template library storage.

Templates are deliberately cross-workspace (that is the feature), but the
stored draft is sanitized before insert: node/webhook secrets never enter the
public copy.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from backend.app.storage.database import _jsonb, _now, get_pool

_SECRET_KEYS = {
    "secretToken", "secret_token", "webhookSecret", "webhook_secret",
    "webhookUrl", "webhook_url",
}


def sanitize_template_draft(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: sanitize_template_draft(item)
            for key, item in value.items()
            if key not in _SECRET_KEYS
        }
    if isinstance(value, list):
        return [sanitize_template_draft(item) for item in value]
    return value


def disable_delivery(draft: Dict[str, Any]) -> Dict[str, Any]:
    """A public template must not carry a live receiver or auto-send."""
    for node in draft.get("nodes") or []:
        if not isinstance(node, dict) or node.get("kind") != "notify":
            continue
        config = node.get("config")
        if isinstance(config, dict):
            config.pop("webhookUrl", None)
            config.pop("webhook_url", None)
            config["enabled"] = False
            config["allowLocal"] = False
            config["allowExternal"] = False
    return draft


def _template_row(row: Any) -> Dict[str, Any]:
    return {
        "id": row["template_id"],
        "workspaceId": str(row["workspace_id"]),
        "name": row["name"],
        "description": row["description"],
        "draft": row["draft_json"],
        "publishedBy": row["published_by"],
        "publishedAt": row["published_at"].isoformat() if row["published_at"] else None,
    }


def publish_template(*, workspace: str, name: str, description: str,
                     draft: Dict[str, Any], published_by: str = "") -> Dict[str, Any]:
    template_id = f"tpl_{uuid.uuid4().hex[:16]}"
    with get_pool().connection() as conn:
        row = conn.execute(
            """
            INSERT INTO data_contract.workflow_templates (
                template_id, workspace_id, name, description, draft_json,
                published_by, published_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (template_id, workspace, name, description, _jsonb(draft), published_by, _now()),
        ).fetchone()
    return _template_row(row)


def list_templates() -> List[Dict[str, Any]]:
    with get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT * FROM data_contract.workflow_templates ORDER BY published_at DESC"
        ).fetchall()
    return [_template_row(row) for row in rows]


def get_template(template_id: str) -> Optional[Dict[str, Any]]:
    with get_pool().connection() as conn:
        row = conn.execute(
            "SELECT * FROM data_contract.workflow_templates WHERE template_id = %s",
            (template_id,),
        ).fetchone()
    return _template_row(row) if row else None
