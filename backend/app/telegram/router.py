"""HTTP factory for Telegram binding/settings.

Root mounts this router after the account auth router. The principal resolver is
injected so this module never imports or reimplements session/auth logic.

Expected principal shape from the account session:
    {"userId": "<uuid>", "workspaceId": "<uuid>"}

Example root wiring:

    runtime = create_telegram_runtime(db_repo, executor, import_repository)
    api_router.include_router(create_telegram_router(
        db_repo, runtime,
        principal_resolver=lambda request: session_from_request(request),
    ))
    # lifespan: await runtime.start(); ...; await runtime.stop()
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Awaitable, Callable, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.app.storage.database import DatabaseRepository
from backend.app.storage.telegram import SettingsOwnershipLost, TelegramRepository
from backend.app.telegram.runtime import TelegramRuntime
from backend.app.telegram.workspace import telegram_workspace_scope

PrincipalResolver = Callable[[Request], Optional[Dict[str, Any]]]


class SettingsRequest(BaseModel):
    deliveryMode: Optional[str] = None
    scenario: Optional[str] = None


def _require_principal(
    resolver: Optional[PrincipalResolver],
    principal: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if resolver is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "AUTH_INTEGRATION_REQUIRED",
                "message": "Telegram router требует principal_resolver из auth-слоя",
            },
        )
    if not isinstance(principal, dict):
        raise HTTPException(
            status_code=401,
            detail={"code": "AUTH_REQUIRED", "message": "Требуется вход в аккаунт"},
        )
    user_id = principal.get("userId")
    workspace_id = principal.get("workspaceId")
    if not isinstance(user_id, str) or not user_id or not isinstance(workspace_id, str) or not workspace_id:
        raise HTTPException(
            status_code=401,
            detail={"code": "AUTH_REQUIRED", "message": "Нужен аккаунт с userId и workspaceId"},
        )
    return {"userId": user_id, "workspaceId": workspace_id}


def create_telegram_router(
    db: DatabaseRepository,
    runtime: TelegramRuntime,
    *,
    principal_resolver: Optional[PrincipalResolver] = None,
    repository: Optional[TelegramRepository] = None,
) -> APIRouter:
    router = APIRouter(prefix="/telegram", tags=["telegram"])
    repo = repository or runtime.repo

    async def principal(request: Request) -> Dict[str, Any]:
        value = principal_resolver(request) if principal_resolver is not None else None
        if inspect.isawaitable(value):
            value = await value
        return _require_principal(principal_resolver, value)

    def _bot_username() -> Optional[str]:
        status = runtime.status()
        username = status.get("botUsername")
        return username if isinstance(username, str) and username else None

    @router.post("/binding")
    async def create_binding(request: Request) -> Dict[str, Any]:
        who = await principal(request)
        issued = await asyncio.to_thread(
            repo.issue_binding_code, who["userId"], who["workspaceId"]
        )
        username = _bot_username()
        return {
            **issued,
            "deepLink": (
                f"https://t.me/{username}?start={issued['code']}" if username else None
            ),
            "instructions": "Откройте Telegram и отправьте /start <код> боту.",
        }

    @router.get("/me")
    async def get_me(request: Request) -> Dict[str, Any]:
        who = await principal(request)
        binding = await asyncio.to_thread(repo.get_binding_by_user, who["userId"])
        if binding is None:
            return {
                "bound": False,
                "settings": None,
                "botUsername": _bot_username(),
                "externalStatus": runtime.status().get("externalStatus"),
                "proxyConfigured": bool(runtime.status().get("proxyConfigured")),
            }
        settings = await asyncio.to_thread(repo.get_settings, binding.chat_id)
        return {
            "bound": True,
            "chatId": binding.chat_id,
            "telegramUsername": binding.telegram_username,
            "createdAt": binding.created_at,
            "settings": {
                "deliveryMode": settings.delivery_mode,
                "scenario": settings.scenario,
            },
            "botUsername": _bot_username(),
            "externalStatus": runtime.status().get("externalStatus"),
            "proxyConfigured": bool(runtime.status().get("proxyConfigured")),
        }

    @router.post("/unbind")
    async def unbind(request: Request) -> Dict[str, Any]:
        who = await principal(request)
        removed = await asyncio.to_thread(repo.delete_binding_for_user, who["userId"])
        return {"bound": False, "removed": removed}

    @router.get("/scenarios")
    async def scenarios(request: Request) -> Dict[str, Any]:
        who = await principal(request)
        with telegram_workspace_scope(who["workspaceId"]):
            workflows = await asyncio.to_thread(db.list_workflows)
        return {
            "items": [
                {"id": str(item.get("id")), "name": str(item.get("name") or "")}
                for item in workflows
                if item.get("id")
            ]
        }

    @router.put("/settings")
    async def update_settings(request: Request, payload: SettingsRequest) -> Dict[str, Any]:
        who = await principal(request)
        binding = await asyncio.to_thread(repo.get_binding_by_user, who["userId"])
        if binding is None:
            raise HTTPException(
                status_code=409,
                detail={"code": "TELEGRAM_NOT_BOUND", "message": "Telegram ещё не привязан"},
            )
        scenario = payload.scenario
        if scenario:
            with telegram_workspace_scope(who["workspaceId"]):
                workflow = await asyncio.to_thread(db.get_workflow, scenario)
            if workflow is None:
                raise HTTPException(
                    status_code=400,
                    detail={"code": "SCENARIO_NOT_FOUND", "message": "Сценарий не найден в workspace"},
                )
        try:
            settings = await asyncio.to_thread(
                repo.set_settings,
                binding.chat_id,
                user_id=who["userId"],
                workspace_id=who["workspaceId"],
                binding_epoch=binding.binding_epoch,
                delivery_mode=payload.deliveryMode,
                scenario=scenario,
            )
        except SettingsOwnershipLost as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "TELEGRAM_REBOUND", "message": str(exc)},
            ) from None
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": "INVALID_TELEGRAM_SETTINGS", "message": str(exc)},
            ) from None
        return {
            "deliveryMode": settings.delivery_mode,
            "scenario": settings.scenario,
        }

    return router