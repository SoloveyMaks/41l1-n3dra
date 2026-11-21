"""Профиль и история запусков для вошедшей сессии.

Подключение (router.py, одна строка рядом с другими include_router):

    from backend.app.api.profile import create_profile_router
    api_router.include_router(create_profile_router())

Модуль намеренно не монтирует себя сам и не правит ``session.py``/``router.py``.

Идентичность. В токене ``cg_session`` (v1/v2) есть workspace, роль, issued и
nonce, но нет user id. Поэтому профиль — это анонимный сессионный псевдоним:

    profile_id = "p_" + HMAC-SHA256(CG_SESSION_SECRET, "profile:" + nonce)[:32]

Nonce уже подписан сервером, HMAC необратим, наружу уходит только digest.
Свойства и ограничения:

* один и тот же токен даёт один и тот же profile_id на всех запросах;
* повторный вход выдаёт новый nonce, то есть новый профиль (история старого
  профиля остаётся в БД, но этим токеном недоступна);
* при эфемерном ``CG_SESSION_SECRET`` (переменная не задана) profile_id
  меняется после рестарта процесса — для истории нужен постоянный секрет;
* это не аккаунт и не изоляция аккаунтов: пользователи публичного demo делят
  workspace, а закладка запуска не доказывает авторство запуска.

Эндпоинты:

* ``GET  /api/profile`` — сводка своего профиля;
* ``GET  /api/profile/history`` — свои закладки запусков;
* ``POST /api/profile/runs/{run_id}`` — добавить существующий запуск своего
  workspace в свою историю (идемпотентно).

Все три требуют валидную сессию. Admin-проверки не затрагиваются.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from backend.app.api.session import (
    SESSION_COOKIE,
    request_origin_allowed,
    session_from_request,
    session_secret,
)
from backend.app.storage.profile import (
    DEFAULT_HISTORY_LIMIT,
    MAX_HISTORY_LIMIT,
    ProfileStore,
)

logger = logging.getLogger("ProfileApi")

PROFILE_PREFIX = "/profile"


def _iso_from_epoch(value: Any) -> Optional[str]:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _token_nonce(token: str) -> Optional[str]:
    """Достать nonce из уже проверенного токена (формат v1/v2 session.py)."""
    parts = token.split(".")
    if len(parts) == 5 and parts[0] == "v1":
        return parts[3] or None
    if len(parts) == 6 and parts[0] == "v2":
        return parts[4] or None
    return None


def profile_id_from_token(token: str) -> Optional[str]:
    """Псевдоним профиля из подписанного токена; None, если nonce не разобран."""
    nonce = _token_nonce(token) if isinstance(token, str) else None
    if not nonce:
        return None
    digest = hmac.new(
        session_secret(),
        b"profile:" + nonce.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]
    return f"p_{digest}"


def _require_profile_identity(request: Request) -> Dict[str, Any]:
    """Валидная сессия обязательна; профиль выводится только из неё."""
    session = session_from_request(request)
    if not session:
        raise HTTPException(
            status_code=401,
            detail={"code": "AUTH_REQUIRED", "message": "Требуется вход: cookie cg_session"},
        )
    token = request.cookies.get(SESSION_COOKIE) or ""
    profile_id = profile_id_from_token(token)
    if not profile_id:
        raise HTTPException(
            status_code=401,
            detail={"code": "AUTH_REQUIRED", "message": "Сессия без профильного nonce"},
        )
    return {"session": session, "profileId": profile_id}


def _scope(session: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "kind": "anonymous_session",
        "workspaceId": session.get("workspaceId"),
        "role": session.get("role"),
        "issuedAt": _iso_from_epoch(session.get("issuedAt")),
        "expiresAt": _iso_from_epoch(session.get("expiresAt")),
        "accountIsolation": False,
        "note": (
            "Профиль привязан к подписанной сессии, а не к аккаунту: "
            "регистрации и постоянного user id пока нет."
        ),
    }


def _store(session: Dict[str, Any]) -> ProfileStore:
    return ProfileStore(session["workspaceId"])


def _storage_error(operation: str, exc: Exception) -> HTTPException:
    # Текст драйвера может содержать DSN — наружу только тип причины.
    logger.warning("profile storage %s failed: %s", operation, type(exc).__name__)
    return HTTPException(
        status_code=503,
        detail={
            "code": "PROFILE_STORAGE_UNAVAILABLE",
            "message": "Хранилище профиля недоступно",
        },
    )


def create_profile_router() -> APIRouter:
    """Фабрика роутера; монтируется в api_router без правок session.py."""
    router = APIRouter(prefix=PROFILE_PREFIX)

    @router.get("")
    def get_profile(request: Request) -> Dict[str, Any]:
        identity = _require_profile_identity(request)
        session = identity["session"]
        profile_id = identity["profileId"]
        try:
            store = _store(session)
            store.touch(profile_id)
            summary = store.summary(profile_id)
        except HTTPException:
            raise
        except Exception as exc:
            raise _storage_error("summary", exc) from None
        return {
            "profileId": profile_id,
            "scope": _scope(session),
            "createdAt": summary["createdAt"],
            "lastSeenAt": summary["lastSeenAt"],
            "history": {
                "runs": summary["runs"],
                "lastRunAt": summary["lastRunAt"],
                "lastBookmarkedAt": summary["lastBookmarkedAt"],
            },
        }

    @router.get("/history")
    def get_profile_history(
        request: Request,
        limit: int = Query(default=DEFAULT_HISTORY_LIMIT, ge=1, le=MAX_HISTORY_LIMIT),
    ) -> Dict[str, Any]:
        identity = _require_profile_identity(request)
        session = identity["session"]
        profile_id = identity["profileId"]
        try:
            store = _store(session)
            store.touch(profile_id)
            items = store.history(profile_id, limit=limit)
        except HTTPException:
            raise
        except Exception as exc:
            raise _storage_error("history", exc) from None
        return {
            "profileId": profile_id,
            "scope": _scope(session),
            "limit": limit,
            "items": items,
        }

    @router.post("/runs/{run_id}")
    def add_profile_run(run_id: str, request: Request) -> Dict[str, Any]:
        # Defense in depth: main.py уже проверяет same-origin для /api.
        if not request_origin_allowed(request):
            raise HTTPException(status_code=403, detail="Запрос с чужого origin запрещён")
        identity = _require_profile_identity(request)
        session = identity["session"]
        profile_id = identity["profileId"]
        if not run_id or len(run_id) > 128:
            raise HTTPException(
                status_code=404,
                detail={"code": "RUN_NOT_FOUND", "message": "Запуск не найден"},
            )
        try:
            store = _store(session)
            result = store.bookmark_run(profile_id, run_id)
        except HTTPException:
            raise
        except Exception as exc:
            raise _storage_error("bookmark", exc) from None
        if result["status"] == "run_not_found":
            raise HTTPException(
                status_code=404,
                detail={"code": "RUN_NOT_FOUND", "message": "Запуск не найден в этом workspace"},
            )
        return {
            "profileId": profile_id,
            "scope": _scope(session),
            "runId": run_id,
            "recorded": True,
            "created": result["status"] == "created",
            "createdAt": result["createdAt"],
        }

    return router