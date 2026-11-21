"""Workspace-scoped API for user-defined custom checks.

Root mounts this after auth/workspace middleware:

    from backend.app.api.custom_checks import create_custom_checks_router
    api_router.include_router(create_custom_checks_router(db))

The router never writes runtime.findings or changes a security verdict. A
successful check returns a separate custom fact stream.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request, status

from backend.app.api.session import session_from_request
from backend.app.codefacts.identity import workspace_scope
from backend.app.custom_checks.models import (
    CustomCheckDefinition,
    CustomCheckPreview,
    CustomCheckRunRequest,
    CustomCheckRunResult,
    CustomCheckRunSummary,
    CustomCheckSandboxStatus,
    CustomCheckSpec,
    CustomCheckValidation,
)
from backend.app.custom_checks.catalog import LANGUAGE, RUNNER, TEMPLATES
from backend.app.custom_checks.sandbox import SandboxUnavailable, sandbox_availability
from backend.app.custom_checks.service import (
    CustomCheckValidationError,
    create_definition,
    ensure_sandbox_available,
    preview_definition,
    run_definition,
    validate_definition,
)
from backend.app.custom_checks.store import (
    CustomCheckConflict,
    CustomCheckNotFound,
    CustomCheckStore,
)
from backend.app.storage.database import DatabaseRepository


def _principal(request: Request) -> Dict[str, Any]:
    session = session_from_request(request)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH_REQUIRED", "message": "Нужна авторизованная сессия"},
        )
    workspace = session.get("workspaceId")
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "WORKSPACE_REQUIRED", "message": "Нет workspace в сессии"},
        )
    role = str(session.get("role") or "user")
    if role not in {"user", "admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "CUSTOM_CHECK_FORBIDDEN", "message": "Роль не может управлять проверками"},
        )
    return {
        "workspace": str(workspace),
        "role": role,
        "created_by": str(session.get("userId") or "demo"),
    }


def create_custom_checks_router(
    db: Optional[DatabaseRepository] = None,
    *,
    prefix: str = "/custom-checks",
    max_concurrent: int = 2,
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["custom-checks"])
    repository = db or DatabaseRepository()
    store = CustomCheckStore()
    slots = threading.BoundedSemaphore(max(1, min(int(max_concurrent), 4)))

    def _busy() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "CUSTOM_CHECK_BUSY", "message": "Слишком много одновременных custom-проверок"},
            headers={"Retry-After": "2"},
        )

    def _unavailable(exc: Exception) -> HTTPException:
        detail = str(exc)
        message = detail if detail.lower().startswith("sandbox") else f"Sandbox недоступен: {detail}"
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "CUSTOM_SANDBOX_UNAVAILABLE",
                "message": message,
            },
        )

    def _acquire() -> None:
        if not slots.acquire(blocking=False):
            raise _busy()

    @router.get("", response_model=list[CustomCheckDefinition])
    def list_custom_checks(request: Request) -> list[CustomCheckDefinition]:
        principal = _principal(request)
        return store.list_definitions(principal["workspace"])

    @router.get("/templates")
    def list_templates(request: Request) -> list[dict[str, str]]:
        _principal(request)
        return [
            {"id": template_id, "title": item["title"], "language": LANGUAGE, "runner": RUNNER}
            for template_id, item in TEMPLATES.items()
        ]

    @router.get("/status", response_model=CustomCheckSandboxStatus)
    def custom_check_status(request: Request, refresh: bool = False) -> CustomCheckSandboxStatus:
        _principal(request)
        available, reason = sandbox_availability(refresh=refresh)
        return CustomCheckSandboxStatus(
            available=available, language=LANGUAGE, runner=RUNNER, reason=reason,
        )

    @router.get("/runs/{workflow_run_id}/findings", response_model=list[CustomCheckRunSummary])
    def list_custom_findings(workflow_run_id: str, request: Request) -> list[CustomCheckRunSummary]:
        principal = _principal(request)
        return store.list_workflow_runs(principal["workspace"], workflow_run_id)

    @router.post("/preview", response_model=CustomCheckPreview)
    def preview_custom_check(payload: CustomCheckSpec, request: Request) -> CustomCheckPreview:
        _principal(request)
        try:
            return preview_definition(payload)
        except CustomCheckValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "CUSTOM_CHECK_INVALID", "message": str(exc)},
            ) from None

    @router.post("/validate", response_model=CustomCheckValidation)
    def validate_custom_check(payload: CustomCheckSpec, request: Request) -> CustomCheckValidation:
        principal = _principal(request)
        _acquire()
        try:
            return validate_definition(payload, principal["workspace"])
        except SandboxUnavailable as exc:
            raise _unavailable(exc) from None
        except CustomCheckValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "CUSTOM_CHECK_INVALID", "message": str(exc)},
            ) from None
        finally:
            slots.release()

    @router.post("", response_model=CustomCheckDefinition, status_code=status.HTTP_201_CREATED)
    def create_custom_check(payload: CustomCheckSpec, request: Request) -> CustomCheckDefinition:
        principal = _principal(request)
        _acquire()
        try:
            return create_definition(
                payload,
                principal["workspace"],
                principal["created_by"],
                store=store,
            )
        except SandboxUnavailable as exc:
            raise _unavailable(exc) from None
        except CustomCheckValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "CUSTOM_CHECK_NOT_VALIDATED", "message": str(exc)},
            ) from None
        except CustomCheckConflict as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "CUSTOM_CHECK_EXISTS", "message": str(exc)},
            ) from None
        finally:
            slots.release()

    @router.post("/{check_id}/run", response_model=CustomCheckRunResult)
    def run_custom_check(check_id: str, payload: CustomCheckRunRequest, request: Request) -> CustomCheckRunResult:
        principal = _principal(request)
        workspace = principal["workspace"]
        try:
            definition = store.get_definition(workspace, check_id)
        except CustomCheckNotFound as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "CUSTOM_CHECK_NOT_FOUND", "message": str(exc)},
            ) from None
        if not definition.lastValidatedAt:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "CUSTOM_CHECK_NOT_VALIDATED", "message": "Проверка не прошла validate"},
            )
        _acquire()
        try:
            ensure_sandbox_available()
            source = store.get_source(workspace, check_id)
            with workspace_scope(workspace):
                return run_definition(definition, source, workspace, payload.artifactId, repository, store=store)
        except SandboxUnavailable as exc:
            raise _unavailable(exc) from None
        except CustomCheckNotFound as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "CUSTOM_CHECK_RUN_REJECTED", "message": str(exc)},
            ) from None
        except CustomCheckValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "CUSTOM_CHECK_RUN_REJECTED", "message": str(exc)},
            ) from None
        except CustomCheckConflict as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "CUSTOM_CHECK_CONFLICT", "message": str(exc)},
            ) from None
        finally:
            slots.release()

    return router
