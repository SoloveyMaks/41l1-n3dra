"""Same-origin session для публичного demo.

Секрет подписи живёт в окружении (`CG_SESSION_SECRET`), а не в репозитории.
Если переменная не задана, генерируется эфемерный секрет на процесс — это
годится только для локального demo, о чём main пишет warning.

Печать: cookie HttpOnly/SameSite=Lax, HMAC-SHA256 над версией/workspace/ролью/
issued/nonce. Локальный режим может выдать user-сессию на GET; режим
`CG_PUBLIC_DEMO=1` требует passphrase `CG_ACCESS_CODE`, не выдаёт cookie
автоматически и закрывает весь `/api` кроме `/api/session` и `/api/health`.
Admin-вход использует отдельный `CG_ADMIN_ACCESS_CODE`; роль живёт только в
подписанном токене. Старые v1-токены принимаются как `user`. Секреты и коды
живут только в окружении сервера, не в репозитории.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import Response

from backend.app.codefacts.identity import configured_workspace_id
from backend.app.storage import accounts

SESSION_COOKIE = "cg_session"
SESSION_TTL_SECONDS = int(os.getenv("CG_SESSION_TTL_SECONDS", str(12 * 60 * 60)))
ROLE_USER = "user"
ROLE_ADMIN = "admin"
ROLES = {ROLE_USER, ROLE_ADMIN}
MODE_ACCOUNT = "account"
MODE_DEMO = "demo"
_RATE_LOCK = threading.Lock()
_RATE_EVENTS: dict[tuple[str, str], deque[float]] = defaultdict(deque)


def trust_proxy_headers() -> bool:
    return os.getenv("CG_TRUST_PROXY", "").strip().lower() in {"1", "true", "yes", "on"}


def client_key(request: Request) -> str:
    if trust_proxy_headers():
        cf = (request.headers.get("cf-connecting-ip") or "").strip()
        if cf:
            return cf
        forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        if forwarded:
            return forwarded
    return request.client.host if request.client else "unknown"


def allow_attempt(key: str, bucket: str, *, limit: int, window_seconds: int) -> bool:
    """Простой in-memory sliding window для login/upload/run."""
    now = time.monotonic()
    cutoff = now - window_seconds
    with _RATE_LOCK:
        events = _RATE_EVENTS[(bucket, key)]
        while events and events[0] < cutoff:
            events.popleft()
        if len(events) >= limit:
            return False
        events.append(now)
        return True
_EPHEMERAL_SECRET = secrets.token_urlsafe(48)


def public_demo() -> bool:
    return (os.getenv("CG_PUBLIC_DEMO", "").strip().lower() in {"1", "true", "yes", "on"})


def access_code() -> str:
    return os.getenv("CG_ACCESS_CODE", "").strip()


def admin_access_code() -> str:
    return os.getenv("CG_ADMIN_ACCESS_CODE", "").strip()


def registration_code() -> str:
    """Опциональный invite-код для регистрации. Пусто = регистрация открыта."""
    return os.getenv("CG_REGISTRATION_CODE", "").strip()


def registration_invite_required() -> bool:
    return bool(registration_code())


def _code_matches(value: Optional[str], expected: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        candidate = value.strip().encode("utf-8")
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(candidate, expected.encode("utf-8"))


def verify_registration_code(value: Optional[str]) -> bool:
    expected = registration_code()
    if not expected:
        return True
    return _code_matches(value, expected)


def admin_available() -> bool:
    """Admin login is available only with a non-empty code distinct from the user code."""
    admin = admin_access_code()
    return bool(admin) and admin != access_code()


def verify_admin_code(value: Optional[str]) -> bool:
    expected = admin_access_code()
    if not expected or expected == access_code():
        return False
    return _code_matches(value, expected)


def public_config_error() -> Optional[str]:
    if not public_demo():
        return None
    missing = []
    if not os.getenv("CG_SESSION_SECRET", "").strip():
        missing.append("CG_SESSION_SECRET")
    if not access_code():
        missing.append("CG_ACCESS_CODE")
    if missing:
        return "CG_PUBLIC_DEMO=1 требует " + ", ".join(missing)
    return None


def verify_access_code(value: Optional[str]) -> bool:
    expected = access_code()
    if not expected:
        return not public_demo()
    return _code_matches(value, expected)


def auth_required() -> bool:
    return public_demo()


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


def issue_session(workspace: Optional[str] = None, role: str = ROLE_USER) -> str:
    """Demo-токен v2: без user id, общий workspace. Явный demo-режим."""
    ws = workspace or configured_workspace_id()
    if not isinstance(role, str) or role not in ROLES:
        raise ValueError("role должен быть user или admin")
    issued = int(time.time())
    nonce = secrets.token_urlsafe(12)
    payload = f"v2.{ws}.{role}.{issued}.{nonce}"
    return f"{payload}.{_sign(payload)}"


def issue_account_session(user: Dict[str, Any], role: Optional[str] = None) -> tuple[str, Dict[str, Any]]:
    """Создать серверную сессию аккаунта и подписать токен v3.

    Возвращает (token, session_record). Серверная строка нужна для отзыва:
    logout инвалидирует её, и старый cookie перестаёт работать.
    """
    session = accounts.create_session(user, SESSION_TTL_SECONDS, role)
    nonce = secrets.token_urlsafe(12)
    payload = (
        f"v3.{session['sessionId']}.{session['userId']}.{session['workspaceId']}."
        f"{session['role']}.{session['issued']}.{nonce}"
    )
    return f"{payload}.{_sign(payload)}", session


def verify_session(token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token or not isinstance(token, str):
        return None
    parts = token.split(".")
    if len(parts) == 5:
        # Legacy v1 demo token: no role in the payload, therefore user only.
        version, workspace, issued_raw, nonce, signature = parts
        if version != "v1":
            return None
        role = ROLE_USER
        payload = f"v1.{workspace}.{issued_raw}.{nonce}"
    elif len(parts) == 6:
        version, workspace, role, issued_raw, nonce, signature = parts
        if version != "v2" or role not in ROLES:
            return None
        payload = f"v2.{workspace}.{role}.{issued_raw}.{nonce}"
    elif len(parts) == 8:
        version, session_id, user_id, workspace, role, issued_raw, nonce, signature = parts
        if version != "v3" or role not in ROLES:
            return None
        payload = f"v3.{session_id}.{user_id}.{workspace}.{role}.{issued_raw}.{nonce}"
    else:
        return None
    try:
        issued = int(issued_raw)
    except ValueError:
        return None
    if issued > int(time.time()) + 60 or int(time.time()) - issued > SESSION_TTL_SECONDS:
        return None
    if not hmac.compare_digest(signature.encode("utf-8"), _sign(payload).encode("ascii")):
        return None
    if len(parts) == 8:
        stored = accounts.load_session(session_id)
        if not stored:
            return None
        if (
            stored["sessionId"] != session_id
            or stored["userId"] != user_id
            or stored["workspaceId"] != workspace
            or stored["role"] != role
            or stored["issuedAt"] != issued
        ):
            return None
        return {
            "workspaceId": workspace,
            "role": role,
            "issuedAt": issued,
            "expiresAt": stored["expiresAt"],
            "mode": MODE_ACCOUNT,
            "sessionId": session_id,
            "userId": user_id,
            "email": stored["email"],
            "displayName": stored["displayName"],
        }
    if workspace != configured_workspace_id():
        return None
    return {
        "workspaceId": workspace,
        "role": role,
        "issuedAt": issued,
        "expiresAt": issued + SESSION_TTL_SECONDS,
        "mode": MODE_DEMO,
        "sessionId": None,
        "userId": None,
    }


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
    forwarded = ""
    if trust_proxy_headers():
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
    return session["workspaceId"] if session else configured_workspace_id()


def revoke_current_session(request: Request) -> bool:
    """Отозвать серверную сессию аккаунта; demo-токену отзывать нечего."""
    session = session_from_request(request)
    if not session or session.get("mode") != MODE_ACCOUNT:
        return False
    return accounts.revoke_session(session.get("sessionId"))


def request_role(request: Request) -> str:
    session = session_from_request(request)
    return session["role"] if session else "anonymous"


def require_admin(request: Request) -> Dict[str, Any]:
    session = session_from_request(request)
    if not session:
        raise HTTPException(status_code=401, detail="Требуется вход")
    if session.get("role") != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Требуется административный доступ")
    return session


def require_workspace_actor(request: Request) -> Dict[str, Any]:
    """Verified account (user/admin) или legacy demo-admin.

    Открывает только пользовательские триггеры своего workspace: schedules и
    repository watches/settings/setup/check. Demo-user сюда не проходит, а
    админские overview/config/secret endpoints остаются на ``require_admin``.
    """
    session = session_from_request(request)
    if not session:
        raise HTTPException(status_code=401, detail="Требуется вход")
    mode = session.get("mode")
    role = session.get("role")
    if mode == MODE_ACCOUNT and role in ROLES:
        return session
    if mode == MODE_DEMO and role == ROLE_ADMIN:
        return session
    raise HTTPException(
        status_code=403,
        detail={
            "code": "ACCOUNT_OR_DEMO_ADMIN_REQUIRED",
            "message": "Нужен подтверждённый аккаунт или legacy demo-admin",
        },
    )


def set_session_cookie(response: Response, token: str, request: Request) -> None:
    forwarded_proto = request.headers.get("x-forwarded-proto") if trust_proxy_headers() else ""
    proto = (forwarded_proto or request.url.scheme or "").lower()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=proto == "https",
        samesite="lax",
        path="/",
    )
