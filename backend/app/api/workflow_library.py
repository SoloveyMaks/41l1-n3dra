"""Public workflow-template library API."""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.app.api.session import request_workspace, require_workspace_actor
from backend.app.contracts.schemas import WorkflowDraft
from backend.app.engine.validator import WorkflowValidator
from backend.app.storage.database import DatabaseRepository
from backend.app.storage.workflow_library import (
    disable_delivery,
    get_template,
    list_templates,
    publish_template,
    sanitize_template_draft,
)


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workflowId: str = Field(min_length=1, max_length=200)
    name: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)


class CopyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = Field(default=None, max_length=200)


def _template_view(row: Dict[str, Any], *, include_draft: bool = False) -> Dict[str, Any]:
    view = {
        "id": row["id"],
        "name": row["name"],
        "description": row.get("description") or "",
        "publishedBy": row.get("publishedBy") or "",
        "publishedAt": row.get("publishedAt"),
        "nodeCount": len((row.get("draft") or {}).get("nodes") or []),
    }
    if include_draft:
        view["draft"] = row.get("draft")
    return view


def _validate_draft(draft: Dict[str, Any]) -> WorkflowDraft:
    try:
        parsed = WorkflowDraft.model_validate(draft)
    except Exception as exc:  # noqa: BLE001 - pydantic text is not a public contract
        raise HTTPException(status_code=422, detail="Шаблон не является корректным workflow") from exc
    result = WorkflowValidator().validate(parsed)
    if not result.valid:
        raise HTTPException(
            status_code=422,
            detail="Шаблон не прошёл проверку графа: "
            + "; ".join(issue.message for issue in result.issues if issue.level == "error"),
        )
    return parsed


def _clear_project_artifact(draft: Dict[str, Any]) -> Dict[str, Any]:
    for node in draft.get("nodes") or []:
        if isinstance(node, dict) and node.get("kind") == "project_input":
            config = node.get("config")
            if isinstance(config, dict):
                config.pop("artifactId", None)
    return draft


def create_workflow_library_router(db: DatabaseRepository) -> APIRouter:
    router = APIRouter(prefix="/workflow-templates", tags=["workflow-library"])

    @router.get("")
    def list_public_templates(request: Request):
        require_workspace_actor(request)
        return {"items": [_template_view(row) for row in list_templates()]}

    @router.get("/{template_id}")
    def read_public_template(template_id: str, request: Request):
        require_workspace_actor(request)
        row = get_template(template_id)
        if not row:
            raise HTTPException(status_code=404, detail="Шаблон не найден")
        return _template_view(row, include_draft=True)

    @router.post("")
    def publish_public_template(value: PublishRequest, request: Request):
        require_workspace_actor(request)
        workspace = request_workspace(request)
        workflow = db.get_workflow(value.workflowId)
        if not workflow or not workflow.get("draft"):
            raise HTTPException(status_code=404, detail="Сценарий не найден")
        draft = disable_delivery(sanitize_template_draft(workflow["draft"]))
        _validate_draft(draft)
        name = (value.name or workflow.get("name") or "Шаблон проверки").strip()
        description = (value.description or workflow.get("description") or "").strip()
        row = publish_template(
            workspace=workspace,
            name=name[:200],
            description=description[:2000],
            draft=draft,
        )
        return _template_view(row)

    @router.post("/{template_id}/copy")
    def copy_public_template(template_id: str, value: CopyRequest, request: Request):
        require_workspace_actor(request)
        row = get_template(template_id)
        if not row:
            raise HTTPException(status_code=404, detail="Шаблон не найден")
        draft = _clear_project_artifact(disable_delivery(sanitize_template_draft(row.get("draft") or {})))
        _validate_draft(draft)
        workflow_id = f"wf_{uuid.uuid4().hex[:12]}"
        name = (value.name or f"{row['name']} (копия)")[:200]
        revision_id = db.save_workflow(
            workflow_id,
            name,
            row.get("description") or "",
            draft,
        )
        return {
            "workflowId": workflow_id,
            "revisionId": revision_id,
            "name": name,
        }

    return router
