"""Same-origin session для публичного demo.

Секрет подписи живёт в окружении (`CG_SESSION_SECRET`), а не в репозитории.
Если переменная не задана, генерируется эфемерный секрет на процесс — это
годится только для локального demo, о чём main пишет warning.

Печать: cookie HttpOnly/SameSite=Lax, HMAC-SHA256 над workspace/issued/nonce.
Same-origin запросы без cookie получают сессию на GET; unsafe-методы без
валидной сессии отклоняются. Это не заменяет настоящий login, но убирает
публичную запись в API и CSRF с чужих origin.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import Response

from backend.app.codefacts.identity import workspace_id

SESSION_COOKIE = "cg_session"
SESSION_TTL_SECONDS = int(os.getenv("CG_SESSION_TTL_SECONDS", str(12 * 60 * 60)))
_EPHEMERAL_SECRET = secrets.token_urlsafe(48)


def session_secret() -> bytes:
    value = os.getenv("CG_SESSION_SECRET")
    if value:
        return value.encode("utf-8")
    return _EPHEMERAL_SECRET.encode("utf-8")


def secret_is_ephemeral() -> bool:
    return not bool(os.getenv("CG_SESSION_SECRET"))


def allowed_origins() -> List[str]:
    raw = os.getenv("CG_ALLOWED_ORIGINS", "")
    return [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]


def _sign(payload: str) -> str:
    return hmac.new(session_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def issue_session(workspace: Optional[str] = None) -> str:
    ws = workspace or workspace_id()
    issued = int(time.time())
    nonce = secrets.token_urlsafe(12)
    payload = f"v1.{ws}.{issued}.{nonce}"
    return f"{payload}.{_sign(payload)}"


def verify_session(token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token or not isinstance(token, str) or token.count(".") != 4:
        return None
    version, workspace, issued_raw, nonce, signature = token.split(".")
    if version != "v1":
        return None
    try:
        issued = int(issued_raw)
    except ValueError:
        return None
    if issued > int(time.time()) + 60 or int(time.time()) - issued > SESSION_TTL_SECONDS:
        return None
    payload = f"{version}.{workspace}.{issued_raw}.{nonce}"
    if not hmac.compare_digest(signature, _sign(payload)):
        return None
    if workspace != workspace_id():
        return None
    return {"workspaceId": workspace, "issuedAt": issued, "expiresAt": issued + SESSION_TTL_SECONDS}


def request_origin_allowed(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin is None:
        referer = request.headers.get("referer")
        if referer is None:
            return True  # curl / server-to-server without browser origin
        origin = referer
    if origin == "null":
        return False
    origin_norm = origin.rstrip("/")
    if origin_norm in allowed_origins():
        return True
    try:
        origin_host = urlsplit(origin_norm).netloc.lower()
    except ValueError:
        return False
    if not origin_host:
        return False
    forwarded = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip().lower()
    request_host = forwarded or request.headers.get("host", "").lower() or request.url.netloc.lower()
    return origin_host == request_host


def session_from_request(request: Request) -> Optional[Dict[str, Any]]:
    state = getattr(request, "state", None)
    cached = getattr(state, "session", None) if state is not None else None
    if cached is not None:
        return cached
    result = verify_session(request.cookies.get(SESSION_COOKIE))
    if state is not None:
        state.session = result
    return result


def request_workspace(request: Request) -> str:
    session = session_from_request(request)
    return session["workspaceId"] if session else workspace_id()


def set_session_cookie(response: Response, token: str, request: Request) -> None:
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "").lower()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=proto == "https",
        samesite="lax",
        path="/",
    )