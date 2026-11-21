"""Профиль реального аккаунта: только сводка, без выдуманных псевдонимов.

Demo-сессия не притворяется профилем: она получает явный ``403 DEMO_SESSION``.
История запусков живёт в ``GET /api/auth/history`` и всегда scoped по
workspace из проверенной сессии.

Монтирование (router.py):

    from backend.app.api.profile import create_profile_router
    api_router.include_router(create_profile_router())
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from backend.app.api.session import MODE_ACCOUNT, MODE_DEMO, session_from_request
from backend.app.storage import accounts
from backend.app.storage.profile import run_summary

PROFILE_PREFIX = "/profile"


def create_profile_router() -> APIRouter:
    router = APIRouter(prefix=PROFILE_PREFIX)

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