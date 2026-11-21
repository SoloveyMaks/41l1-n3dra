"""Аккаунты, пароли и серверные сессии.

Схема ``account`` создаётся миграцией ``db/migrations/009_accounts.up.sql``.
Здесь только данные и проверки; выпуск/разбор подписанного токена живёт в
``backend.app.api.session``.

Пароль: stdlib ``hashlib.scrypt`` (n=16384, r=8, p=1, 16-байтная соль).
Формат строки: ``scrypt$n$r$p$salt_hex$hash_hex``. Внешних argon2/bcrypt
зависимостей нет и не добавляется.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from backend.app.storage.database import get_pool

SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SALT_BYTES = 16
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 256
MAX_EMAIL_LENGTH = 254
SESSION_ROLE_USER = "user"
SESSION_ROLE_ADMIN = "admin"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AccountError(ValueError):
    """Некорректный ввод или состояние аккаунта."""


class EmailTaken(AccountError):
    """Такой e-mail уже зарегистрирован."""


def normalize_email(value: Any) -> str:
    if not isinstance(value, str):
        raise AccountError("email должен быть строкой")
    email = value.strip().lower()
    if not email or len(email) > MAX_EMAIL_LENGTH or not _EMAIL_RE.fullmatch(email):
        raise AccountError("Некорректный email")
    return email


def validate_password(value: Any) -> str:
    if not isinstance(value, str):
        raise AccountError("password должен быть строкой")
    if not (MIN_PASSWORD_LENGTH <= len(value) <= MAX_PASSWORD_LENGTH):
        raise AccountError(
            f"Пароль должен быть от {MIN_PASSWORD_LENGTH} до {MAX_PASSWORD_LENGTH} символов"
        )
    if "\x00" in value:
        raise AccountError("Пароль содержит недопустимый символ")
    return value


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return (
        f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}$"
        f"{salt.hex()}${digest.hex()}"
    )


def _parse_hash(encoded: str) -> Optional[tuple[int, int, int, bytes, bytes]]:
    parts = encoded.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return None
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt = bytes.fromhex(parts[4])
        digest = bytes.fromhex(parts[5])
    except ValueError:
        return None
    if n <= 1 or r <= 0 or p <= 0 or not salt or not digest:
        return None
    return n, r, p, salt, digest


def verify_password(password: Any, encoded: Any) -> bool:
    if not isinstance(password, str) or not isinstance(encoded, str):
        return False
    parsed = _parse_hash(encoded)
    if parsed is None:
        return False
    n, r, p, salt, expected = parsed
    try:
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
        )
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(actual, expected)


# Фиктивный хэш для выравнивания времени ответа на несуществующий e-mail.
_DUMMY_HASH = hash_password("not-a-real-password")


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _epoch(value: Any) -> Optional[int]:
    if isinstance(value, datetime):
        return int(value.timestamp())
    return None


def public_user(row: Dict[str, Any]) -> Dict[str, Any]:
    """Безопасная проекция аккаунта: хэш пароля и служебные поля не уходят."""
    return {
        "userId": str(row["user_id"]),
        "email": row["email"],
        "displayName": row.get("display_name"),
        "role": row["role"],
        "workspaceId": str(row["workspace_id"]),
        "createdAt": _iso(row.get("created_at")),
    }


def register_user(email: Any, password: Any, display_name: Optional[str] = None) -> Dict[str, Any]:
    """Создать аккаунт и его личный workspace одной транзакцией."""
    normalized = normalize_email(email)
    valid_password = validate_password(password)
    name = display_name.strip()[:80] if isinstance(display_name, str) and display_name.strip() else None
    user_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    password_hash = hash_password(valid_password)
    now = datetime.now(timezone.utc)
    try:
        with get_pool().connection() as conn:
            exists = conn.execute(
                "SELECT 1 AS found FROM account.users WHERE lower(email) = %s",
                (normalized,),
            ).fetchone()
            if exists is not None:
                raise EmailTaken("Аккаунт с таким email уже существует")
            conn.execute(
                "INSERT INTO data_contract.workspaces (workspace_id, name) VALUES (%s, %s)",
                (workspace_id, f"user:{user_id}"),
            )
            row = conn.execute(
                """
                INSERT INTO account.users (
                    user_id, email, password_hash, display_name, role, workspace_id,
                    created_at, updated_at
                ) VALUES (%s, %s, %s, %s, 'user', %s, %s, %s)
                RETURNING user_id, email, display_name, role, workspace_id, created_at
                """,
                (user_id, normalized, password_hash, name, workspace_id, now, now),
            ).fetchone()
    except EmailTaken:
        raise
    except Exception as exc:  # noqa: BLE001
        if getattr(exc, "sqlstate", None) == "23505":
            raise EmailTaken("Аккаунт с таким email уже существует") from None
        raise
    return public_user(dict(row))


def authenticate(email: Any, password: Any) -> Optional[Dict[str, Any]]:
    """Вернуть активного пользователя или None. Одинаковое время для обоих исходов."""
    try:
        normalized = normalize_email(email)
    except AccountError:
        verify_password(password if isinstance(password, str) else "", _DUMMY_HASH)
        return None
    if not isinstance(password, str) or len(password) > MAX_PASSWORD_LENGTH:
        verify_password("", _DUMMY_HASH)
        return None
    with get_pool().connection() as conn:
        row = conn.execute(
            """
            SELECT user_id, email, password_hash, display_name, role, workspace_id,
                   created_at, disabled_at
            FROM account.users WHERE lower(email) = %s
            """,
            (normalized,),
        ).fetchone()
    if row is None:
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    if row["disabled_at"] is not None:
        return None
    return public_user(dict(row))


def get_user(user_id: str) -> Optional[Dict[str, Any]]:
    try:
        parsed = uuid.UUID(str(user_id))
    except (ValueError, TypeError, AttributeError):
        return None
    with get_pool().connection() as conn:
        row = conn.execute(
            """
            SELECT user_id, email, display_name, role, workspace_id, created_at
            FROM account.users WHERE user_id = %s AND disabled_at IS NULL
            """,
            (parsed,),
        ).fetchone()
    return public_user(dict(row)) if row else None


def create_session(user: Dict[str, Any], ttl_seconds: int, role: Optional[str] = None) -> Dict[str, Any]:
    session_id = secrets.token_hex(16)
    issued = int(time.time())
    expires = issued + max(1, int(ttl_seconds))
    session_role = role or user.get("role") or SESSION_ROLE_USER
    if session_role not in {SESSION_ROLE_USER, SESSION_ROLE_ADMIN}:
        raise AccountError("Некорректная роль сессии")
    workspace_id = uuid.UUID(str(user["workspaceId"]))
    with get_pool().connection() as conn:
        conn.execute(
            """
            INSERT INTO account.sessions (
                session_id, user_id, workspace_id, role, issued_at, expires_at
            ) VALUES (%s, %s, %s, %s, to_timestamp(%s), to_timestamp(%s))
            """,
            (session_id, uuid.UUID(str(user["userId"])), workspace_id,
             session_role, issued, expires),
        )
    return {
        "sessionId": session_id,
        "userId": str(user["userId"]),
        "workspaceId": str(workspace_id),
        "role": session_role,
        "issued": issued,
        "expires": expires,
    }


def load_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Активная серверная сессия с данными пользователя, либо None."""
    if not isinstance(session_id, str) or not re.fullmatch(r"[0-9a-f]{32}", session_id):
        return None
    with get_pool().connection() as conn:
        row = conn.execute(
            """
            SELECT s.session_id, s.user_id, s.workspace_id, s.role,
                   s.issued_at, s.expires_at, s.revoked_at,
                   u.email, u.display_name, u.disabled_at
              FROM account.sessions s
              JOIN account.users u ON u.user_id = s.user_id
             WHERE s.session_id = %s
            """,
            (session_id,),
        ).fetchone()
    if row is None:
        return None
    if row["revoked_at"] is not None or row["disabled_at"] is not None:
        return None
    if row["expires_at"].timestamp() <= time.time():
        return None
    return {
        "sessionId": row["session_id"],
        "userId": str(row["user_id"]),
        "workspaceId": str(row["workspace_id"]),
        "role": row["role"],
        "issuedAt": _epoch(row["issued_at"]),
        "expiresAt": _epoch(row["expires_at"]),
        "email": row["email"],
        "displayName": row["display_name"],
    }


def revoke_session(session_id: Optional[str]) -> bool:
    if not isinstance(session_id, str) or not re.fullmatch(r"[0-9a-f]{32}", session_id):
        return False
    with get_pool().connection() as conn:
        row = conn.execute(
            """
            UPDATE account.sessions SET revoked_at = now()
             WHERE session_id = %s AND revoked_at IS NULL
             RETURNING session_id
            """,
            (session_id,),
        ).fetchone()
    return row is not None
