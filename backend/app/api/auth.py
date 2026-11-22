"""Регистрация, вход, выход и профиль реального аккаунта.

Сессия v3 подписана HMAC и дополнительно проверяется по строке
``account.sessions``: logout реально отзывает её, а не только стирает cookie.
Demo-вход по ``CG_ACCESS_CODE`` остаётся отдельным явным режимом (``mode=demo``)
и не выдаёт себя за аккаунт.

Монтирование (router.py):

    from backend.app.api.auth import create_auth_router
    api_router.include_router(create_auth_router())
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from backend.app.api.session import (
    MODE_ACCOUNT,
    MODE_DEMO,
    SESSION_COOKIE,
    issue_account_session,
    registration_invite_required,
    request_origin_allowed,
    revoke_current_session,
    session_from_request,
    set_session_cookie,
    verify_registration_code,
)
from backend.app.storage import accounts
from backend.app.storage.profile import (
    DEFAULT_HISTORY_LIMIT,
    MAX_HISTORY_LIMIT,
    history_page,
)

AUTH_PREFIX = "/auth"


def _require_session(request: Request) -> Dict[str, Any]:
    session = session_from_request(request)
    if not session:
        raise HTTPException(
            status_code=401,
            detail={"code": "AUTH_REQUIRED", "message": "Требуется вход"},
        )
    return session


def _public_account(session: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if session.get("mode") != MODE_ACCOUNT:
        return None
    user = accounts.get_user(session["userId"])
    return user or {
        "userId": session["userId"],
        "email": session.get("email"),
        "displayName": session.get("displayName"),
        "role": session["role"],
        "workspaceId": session["workspaceId"],
        "createdAt": None,
    }


def _session_view(session: Dict[str, Any]) -> Dict[str, Any]:
    now = int(time.time())
    return {
        "authenticated": True,
        "mode": session.get("mode", MODE_DEMO),
        "user": _public_account(session),
        "role": session["role"],
        "workspaceId": session["workspaceId"],
        "sessionId": session.get("sessionId"),
        "expiresInSec": max(0, int(session.get("expiresAt", now)) - now),
    }


def _origin_guard(request: Request) -> None:
    if not request_origin_allowed(request):
        raise HTTPException(status_code=403, detail="Запрос с чужого origin запрещён")


def create_auth_router() -> APIRouter:
    router = APIRouter(prefix=AUTH_PREFIX)

    @router.post("/register")
    def register(request: Request, response: Response,
                 payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        _origin_guard(request)
        data = payload or {}
        if registration_invite_required() and not verify_registration_code(
            data.get("registrationCode", data.get("registration_code"))
        ):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "REGISTRATION_CODE_REQUIRED",
                    "message": "Регистрация по приглашению: нужен registrationCode",
                },
            )
        try:
            user = accounts.register_user(
                data.get("email"),
                data.get("password"),
                data.get("displayName", data.get("display_name")),
            )
        except accounts.EmailTaken as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "EMAIL_TAKEN", "message": str(exc)},
            ) from None
        except accounts.AccountError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": "INVALID_ACCOUNT", "message": str(exc)},
            ) from None
        token, session = issue_account_session(user)
        set_session_cookie(response, token, request)
        return _session_view(
            {
                "mode": MODE_ACCOUNT,
                "role": session["role"],
                "workspaceId": session["workspaceId"],
                "sessionId": session["sessionId"],
                "expiresAt": session["expires"],
                "userId": session["userId"],
                "email": user["email"],
                "displayName": user["displayName"],
            }
        )

    @router.post("/login")
    def login(request: Request, response: Response,
              payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        _origin_guard(request)
        data = payload or {}
        user = accounts.authenticate(data.get("email"), data.get("password"))
        if not user:
            raise HTTPException(
                status_code=401,
                detail={
                    "code": "INVALID_CREDENTIALS",
                    "message": "Неверный email или пароль",
                },
                headers={"WWW-Authenticate": "Session"},
            )
        token, session = issue_account_session(user)
        set_session_cookie(response, token, request)
        return _session_view(
            {
                "mode": MODE_ACCOUNT,
                "role": session["role"],
                "workspaceId": session["workspaceId"],
                "sessionId": session["sessionId"],
                "expiresAt": session["expires"],
                "userId": session["userId"],
                "email": user["email"],
                "displayName": user["displayName"],
            }
        )

    @router.post("/logout")
    def logout(request: Request, response: Response) -> Dict[str, Any]:
        _origin_guard(request)
        revoked = revoke_current_session(request)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"authenticated": False, "revoked": revoked}

    @router.get("/me")
    def me(request: Request) -> Dict[str, Any]:
        return _session_view(_require_session(request))

    @router.get("/history")
    def history(
        request: Request,
        limit: int = Query(default=DEFAULT_HISTORY_LIMIT, ge=1, le=MAX_HISTORY_LIMIT),
        offset: int = Query(default=0, ge=0),
        project: Optional[str] = Query(default=None, max_length=256),
        workflow: Optional[str] = Query(default=None, max_length=256),
        status: Optional[str] = Query(default=None, max_length=64),
    ) -> Dict[str, Any]:
        session = _require_session(request)
        page = history_page(
            session["workspaceId"],
            limit=limit,
            offset=offset,
            project=project,
            workflow=workflow,
            status=status,
        )
        return {
            **page,
            "mode": session.get("mode", MODE_DEMO),
            "userId": session.get("userId"),
            "workspaceId": session["workspaceId"],
            "sharedDemoWorkspace": session.get("mode") == MODE_DEMO,
        }

    return router