"""Профиль реального аккаунта: только сводка, без выдуманных псевдонимов.

Demo-сессия не притворяется профилем: ``GET /api/profile`` отвечает ей явным
``403 DEMO_SESSION``. История живёт в ``GET /api/profile/history``: она всегда
scoped по workspace из проверенной сессии, поддерживает фильтры проект/сценарий/
статус/дата и отдаёт ``sharedDemoWorkspace=true`` для demo, чтобы общий workspace
не выглядел личным кабинетом. ``GET /api/auth/history`` остаётся коротким
срезом последних запусков для навбара.

Монтирование (router.py):

    from backend.app.api.profile import create_profile_router
    api_router.include_router(create_profile_router())
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query, Request

from backend.app.api.session import MODE_ACCOUNT, MODE_DEMO, session_from_request
from backend.app.storage import accounts
from backend.app.storage.profile import DEFAULT_HISTORY_LIMIT, MAX_HISTORY_LIMIT, history_page, run_summary

PROFILE_PREFIX = "/profile"


def create_profile_router() -> APIRouter:
    router = APIRouter(prefix=PROFILE_PREFIX)

    @router.get("/history")
    def history(
        request: Request,
        limit: int = Query(default=DEFAULT_HISTORY_LIMIT, ge=1, le=MAX_HISTORY_LIMIT),
        offset: int = Query(default=0, ge=0),
        project: str | None = Query(default=None, max_length=256),
        workflow: str | None = Query(default=None, max_length=256),
        status: str | None = Query(default=None, max_length=64),
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Dict[str, Any]:
        session = session_from_request(request)
        if not session:
            raise HTTPException(status_code=401, detail={"code": "AUTH_REQUIRED", "message": "Требуется вход"})
        if any(value is not None and value.utcoffset() is None for value in (since, until)):
            raise HTTPException(status_code=422, detail="Укажите часовой пояс даты")
        if since and until and since >= until:
            raise HTTPException(status_code=422, detail="Конец периода должен быть позже начала")
        return {
            **history_page(session["workspaceId"], limit=limit, offset=offset,
                           project=project, workflow=workflow, status=status, since=since, until=until),
            "mode": session.get("mode", MODE_DEMO),
            "userId": session.get("userId"),
            "sharedDemoWorkspace": session.get("mode") == MODE_DEMO,
        }

    @router.get("")
    def get_profile(request: Request) -> Dict[str, Any]:
        session = session_from_request(request)
        if not session:
            raise HTTPException(
                status_code=401,
                detail={"code": "AUTH_REQUIRED", "message": "Требуется вход"},
            )
        if session.get("mode") == MODE_DEMO:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "DEMO_SESSION",
                    "message": (
                        "Demo-сессия не является аккаунтом. "
                        "Зарегистрируйтесь или войдите, чтобы получить профиль."
                    ),
                },
            )
        user = accounts.get_user(session["userId"])
        if not user:
            # Аккаунт отключён или удалён между проверкой токена и запросом.
            raise HTTPException(
                status_code=401,
                detail={"code": "AUTH_REQUIRED", "message": "Аккаунт недоступен"},
            )
        return {
            "mode": MODE_ACCOUNT,
            "user": user,
            "session": {
                "sessionId": session.get("sessionId"),
                "issuedAt": session.get("issuedAt"),
                "expiresAt": session.get("expiresAt"),
            },
            "history": run_summary(session["workspaceId"]),
        }

    return router
