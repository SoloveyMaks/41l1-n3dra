"""Telegram Mini App authentication: initData check and scoped bearer tokens.

The client never supplies an identity. ``initData`` is verified only with the
official Telegram WebApp HMAC (secret key = HMAC_SHA256("WebAppData", bot
token)), then the signed ``user.id`` is used as the private-chat binding key:
in a private Telegram chat ``chat_id`` equals the user id. No binding is ever
created or guessed here; an unbound account gets an explicit error.

The issued token is a short-lived, server-signed bearer (15 minutes max),
bound to ``chat_id/user_id/workspace_id/binding_epoch``. It is not a normal
account session: it cannot be used instead of the cookie session and carries
``role=user`` only. Every Mini App request re-reads the live binding and the
active account, so unbind/rebind revokes outstanding tokens without a store.

The bot token and the token secret stay in this process. Neither appears in
logs, errors, argv or returned payloads.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import parse_qsl

from backend.app.storage import accounts
from backend.app.storage.telegram import Binding, TelegramRepository

INIT_DATA_MAX_BYTES = 8192
INIT_DATA_DEFAULT_MAX_AGE = 3600
INIT_DATA_MAX_AGE_CAP = 86400
INIT_DATA_FUTURE_SKEW = 60
TOKEN_MAX_TTL_SECONDS = 900
TOKEN_DEFAULT_TTL_SECONDS = 900
TOKEN_MAX_BYTES = 4096
TOKEN_MAX_FUTURE_IAT = 60
TOKEN_SECRET_ENV = "TELEGRAM_MINIAPP_TOKEN_SECRET"
TOKEN_ROLE = "user"

_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_AUTH_DATE_RE = re.compile(r"^\d{1,12}$")
_B64_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_TOKEN_PARTS = 3


class MiniAppAuthError(Exception):
    """Base class; the HTTP layer maps subclasses to stable error codes."""


class MiniConfigError(MiniAppAuthError):
    """Bot token or signing secret is not configured safely."""


class InitDataError(MiniAppAuthError):
    """initData is missing, malformed, expired, duplicated or fails HMAC."""


class MiniTokenError(MiniAppAuthError):
    """Bearer token is malformed, expired or signed by someone else."""


class MiniTokenRevoked(MiniTokenError):
    """Live binding/account no longer matches the token claims."""


class MiniNotBound(MiniAppAuthError):
    """Verified Telegram identity has no usable private-chat binding."""


class MiniBindError(MiniAppAuthError):
    """Binding code is malformed, expired or already used by another chat."""


@dataclass(frozen=True)
class InitDataUser:
    id: int
    first_name: str = ""
    last_name: str = ""
    username: Optional[str] = None
    auth_date: int = 0


@dataclass(frozen=True)
class MiniIdentity:
    chat_id: int
    user_id: str
    workspace_id: str
    binding_epoch: int
    display_name: str


@dataclass(frozen=True)
class MiniTokenClaims:
    chat_id: int
    user_id: str
    workspace_id: str
    binding_epoch: int
    issued_at: int
    expires_at: int


def _env_int(name: str, default: int, *, low: int, high: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(low, min(value, high))


def _as_uuid(value: Any, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise MiniAppAuthError(f"{field} is not a UUID") from exc


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    if not value or not _B64_RE.fullmatch(value):
        raise MiniTokenError("token encoding is invalid")
    padded = value + "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, ValueError) as exc:
        raise MiniTokenError("token encoding is invalid") from exc


def derive_token_secret(bot_token: str) -> bytes:
    """Stable server secret when the operator did not set an explicit one."""
    return hmac.new(
        b"nedra-miniapp-token-v1", bot_token.encode("utf-8"), hashlib.sha256
    ).digest()


def configured_token_secret() -> Optional[bytes]:
    raw = os.getenv(TOKEN_SECRET_ENV, "").strip()
    if not raw:
        return None
    if len(raw) < 16:
        raise MiniConfigError(f"{TOKEN_SECRET_ENV} must be at least 16 characters")
    return raw.encode("utf-8")


def init_data_secret(bot_token: str) -> bytes:
    """Official Telegram WebApp secret: HMAC(key="WebAppData", data=bot_token)."""
    return hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()


def parse_init_data(raw: str) -> Dict[str, str]:
    """Parse initData with duplicate/malformed input rejected.

    Values are percent-decoded exactly as Telegram's clients produce them; the
    signature is checked over the decoded values (official algorithm).
    """
    if not isinstance(raw, str) or not raw:
        raise InitDataError("initData is missing")
    try:
        encoded_size = len(raw.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise InitDataError("initData is not valid UTF-8") from exc
    if encoded_size > INIT_DATA_MAX_BYTES:
        raise InitDataError("initData is too large")
    try:
        pairs = parse_qsl(
            raw,
            keep_blank_values=True,
            strict_parsing=True,
            encoding="utf-8",
            errors="strict",
            max_num_fields=64,
        )
    except (ValueError, UnicodeDecodeError) as exc:
        raise InitDataError("initData is malformed") from exc
    if not pairs:
        raise InitDataError("initData is empty")

    values: Dict[str, str] = {}
    for key, value in pairs:
        if not key or key in values:
            raise InitDataError("initData has duplicate or empty keys")
        values[key] = value
    return values


def _data_check_string(values: Dict[str, str]) -> str:
    return "\n".join(
        f"{key}={values[key]}" for key in sorted(values) if key != "hash"
    )


def verify_init_data(
    raw: str,
    bot_token: str,
    *,
    now: Optional[int] = None,
    max_age: Optional[int] = None,
    future_skew: int = INIT_DATA_FUTURE_SKEW,
) -> InitDataUser:
    """Verify Telegram initData and return the signed user.

    Order matters: shape/duplicates, hash, freshness, then JSON parsing. A
    malformed or tampered payload never reaches binding/account lookup.
    """
    if not isinstance(bot_token, str) or not bot_token:
        raise MiniConfigError("telegram bot token is not configured")
    values = parse_init_data(raw)
    provided_hash = values.get("hash", "")
    if not _HASH_RE.fullmatch(provided_hash):
        raise InitDataError("initData hash is malformed")

    expected_hash = hmac.new(
        init_data_secret(bot_token),
        _data_check_string(values).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_hash, provided_hash.lower()):
        raise InitDataError("initData signature is invalid")

    auth_date_raw = values.get("auth_date", "")
    if not _AUTH_DATE_RE.fullmatch(auth_date_raw):
        raise InitDataError("initData auth_date is malformed")
    auth_date = int(auth_date_raw)
    current = int(time.time() if now is None else now)
    limit = (
        _env_int("TELEGRAM_MINIAPP_INIT_MAX_AGE_SECONDS", INIT_DATA_DEFAULT_MAX_AGE,
                 low=60, high=INIT_DATA_MAX_AGE_CAP)
        if max_age is None
        else max(60, min(int(max_age), INIT_DATA_MAX_AGE_CAP))
    )
    if auth_date > current + max(0, int(future_skew)):
        raise InitDataError("initData auth_date is in the future")
    if auth_date < current - limit:
        raise InitDataError("initData is expired; reopen the Mini App")

    if values.get("chat_type") and values["chat_type"] != "private":
        raise InitDataError("Mini App is available only from a private chat")

    user_raw = values.get("user")
    if not user_raw:
        raise InitDataError("initData has no user")
    try:
        user = json.loads(user_raw)
    except (TypeError, ValueError) as exc:
        raise InitDataError("initData user is malformed") from exc
    if not isinstance(user, dict):
        raise InitDataError("initData user is malformed")
    user_id = user.get("id")
    if type(user_id) is not int or user_id <= 0 or user_id > 2**63 - 1:
        raise InitDataError("initData user id is malformed")

    first_name = user.get("first_name")
    last_name = user.get("last_name")
    username = user.get("username")
    return InitDataUser(
        id=user_id,
        first_name=first_name.strip()[:80] if isinstance(first_name, str) else "",
        last_name=last_name.strip()[:80] if isinstance(last_name, str) else "",
        username=username.strip()[:64] if isinstance(username, str) else None,
        auth_date=auth_date,
    )


def mint_mini_token(
    identity: MiniIdentity,
    secret: bytes,
    *,
    ttl_seconds: Optional[int] = None,
    now: Optional[int] = None,
) -> Tuple[str, int]:
    """Sign a scoped bearer token; returns ``(token, expires_at)``."""
    if not isinstance(secret, (bytes, bytearray)) or len(secret) < 16:
        raise MiniConfigError("Mini App token secret is missing")
    ttl = TOKEN_DEFAULT_TTL_SECONDS if ttl_seconds is None else int(ttl_seconds)
    ttl = max(60, min(ttl, TOKEN_MAX_TTL_SECONDS))
    issued = int(time.time() if now is None else now)
    expires = issued + ttl
    payload = {
        "v": 1,
        "cid": int(identity.chat_id),
        "uid": str(_as_uuid(identity.user_id, "user_id")),
        "wid": str(_as_uuid(identity.workspace_id, "workspace_id")),
        "ep": int(identity.binding_epoch),
        "role": TOKEN_ROLE,
        "iat": issued,
        "exp": expires,
        "jti": secrets.token_urlsafe(9),
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    body = _b64encode(raw)
    signed = f"v1.{body}"
    signature = _b64encode(hmac.new(secret, signed.encode("ascii"), hashlib.sha256).digest())
    return f"{signed}.{signature}", expires


def _strict_int(value: Any) -> Optional[int]:
    if type(value) is not int:
        return None
    return int(value)


def verify_mini_token(
    token: str,
    secret: bytes,
    *,
    now: Optional[int] = None,
) -> MiniTokenClaims:
    """Verify signature, shape, role and lifetime before any DB lookup."""
    if not isinstance(token, str) or not token or len(token.encode("utf-8")) > TOKEN_MAX_BYTES:
        raise MiniTokenError("token is missing or too large")
    parts = token.split(".")
    if len(parts) != _TOKEN_PARTS or parts[0] != "v1":
        raise MiniTokenError("token version is invalid")
    signed = f"{parts[0]}.{parts[1]}"
    provided_signature = _b64decode(parts[2])
    expected_signature = hmac.new(secret, signed.encode("ascii"), hashlib.sha256).digest()
    if not hmac.compare_digest(expected_signature, provided_signature):
        raise MiniTokenError("token signature is invalid")

    try:
        payload = json.loads(_b64decode(parts[1]).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise MiniTokenError("token payload is malformed") from exc
    if not isinstance(payload, dict):
        raise MiniTokenError("token payload is malformed")
    if payload.get("v") != 1 or payload.get("role") != TOKEN_ROLE:
        raise MiniTokenError("token claims are invalid")

    chat_id = _strict_int(payload.get("cid"))
    issued = _strict_int(payload.get("iat"))
    expires = _strict_int(payload.get("exp"))
    epoch = _strict_int(payload.get("ep"))
    if chat_id is None or chat_id <= 0 or issued is None or expires is None or epoch is None:
        raise MiniTokenError("token claims are invalid")
    if epoch < 1 or expires <= issued or expires - issued > TOKEN_MAX_TTL_SECONDS:
        raise MiniTokenError("token lifetime is invalid")
    current = int(time.time() if now is None else now)
    if issued > current + TOKEN_MAX_FUTURE_IAT:
        raise MiniTokenError("token is not valid yet")
    if expires <= current:
        raise MiniTokenError("token is expired")

    try:
        user_id = str(uuid.UUID(str(payload.get("uid"))))
        workspace_id = str(uuid.UUID(str(payload.get("wid"))))
    except (TypeError, ValueError, AttributeError) as exc:
        raise MiniTokenError("token claims are invalid") from exc
    return MiniTokenClaims(
        chat_id=chat_id,
        user_id=user_id,
        workspace_id=workspace_id,
        binding_epoch=epoch,
        issued_at=issued,
        expires_at=expires,
    )


def _display_name(
    account: Dict[str, Any],
    user: Optional[InitDataUser] = None,
    telegram_username: Optional[str] = None,
) -> str:
    name = account.get("displayName")
    if isinstance(name, str) and name.strip():
        return name.strip()[:120]
    if user is not None:
        full = " ".join(part for part in (user.first_name, user.last_name) if part)
        if full.strip():
            return full.strip()[:120]
    if isinstance(telegram_username, str) and telegram_username.strip():
        return f"@{telegram_username.strip()[:64]}"
    return "Пользователь"


class MiniAppAuth:
    """Binding-aware initData exchange and bearer verification.

    ``bot_token_provider`` and the repository are injected so tests do not read
    a real token file. Production wiring passes nothing and uses the existing
    ``load_bot_settings`` path.
    """

    def __init__(
        self,
        repository: TelegramRepository,
        *,
        bot_token_provider: Optional[Callable[[], str]] = None,
        token_secret: Optional[bytes] = None,
        clock: Callable[[], float] = time.time,
        init_max_age: Optional[int] = None,
        token_ttl_seconds: Optional[int] = None,
    ) -> None:
        self._repository = repository
        self._bot_token_provider = bot_token_provider or _load_bot_token_from_settings
        self._token_secret = token_secret
        self._clock = clock
        self._init_max_age = init_max_age
        self._token_ttl = token_ttl_seconds

    def _bot_token(self) -> str:
        try:
            token = self._bot_token_provider()
        except MiniAppAuthError:
            raise
        except Exception as exc:  # noqa: BLE001 - config errors must stay generic
            raise MiniConfigError("telegram bot token is not configured") from exc
        if not isinstance(token, str) or not token:
            raise MiniConfigError("telegram bot token is not configured")
        return token

    def _secret(self) -> bytes:
        if self._token_secret is not None:
            if len(self._token_secret) < 16:
                raise MiniConfigError("Mini App token secret is too short")
            return self._token_secret
        explicit = configured_token_secret()
        if explicit is not None:
            return explicit
        return derive_token_secret(self._bot_token())

    def _now(self) -> int:
        return int(self._clock())

    def _identity(
        self,
        binding: Binding,
        account: Dict[str, Any],
        *,
        user: Optional[InitDataUser] = None,
    ) -> MiniIdentity:
        binding_workspace = str(_as_uuid(binding.workspace_id, "workspace_id"))
        account_workspace = str(_as_uuid(account.get("workspaceId"), "workspaceId"))
        if binding_workspace != account_workspace:
            # A stale binding must never borrow another account's workspace.
            raise MiniNotBound("Привязка Telegram больше не соответствует аккаунту")
        if str(_as_uuid(binding.user_id, "user_id")) != str(_as_uuid(account.get("userId"), "userId")):
            raise MiniNotBound("Привязка Telegram больше не соответствует аккаунту")
        return MiniIdentity(
            chat_id=int(binding.chat_id),
            user_id=str(account["userId"]),
            workspace_id=account_workspace,
            binding_epoch=int(binding.binding_epoch),
            display_name=_display_name(account, user, binding.telegram_username),
        )

    def _binding_and_account(self, chat_id: int) -> Tuple[Binding, Dict[str, Any]]:
        binding = self._repository.get_binding(int(chat_id))
        if binding is None:
            raise MiniNotBound("Telegram ещё не привязан")
        account = accounts.get_user(binding.user_id)
        if account is None:
            raise MiniNotBound("Аккаунт привязки отключён или удалён")
        return binding, account

    def verify_init_data(self, raw: str) -> InitDataUser:
        """Verify initData without touching bindings (used by /bind).

        The same signature, freshness, private-chat and user checks as /auth
        apply; a verified user is not yet an authorized account.
        """
        return verify_init_data(
            raw,
            self._bot_token(),
            now=self._now(),
            max_age=self._init_max_age,
        )

    def bind_with_code(
        self,
        user: InitDataUser,
        code: str,
    ) -> Tuple[MiniIdentity, int, str]:
        """Consume a one-time cabinet code for this private chat, then mint.

        A token is issued only after the code was consumed and the bound
        account is active in exactly the binding workspace. Bad input and a
        missing/expired code share one generic error; the code itself is never
        echoed or logged. Replay of an already-consumed code follows the
        existing ``consume_binding_code`` idempotency window (same chat only).
        """
        if not isinstance(code, str):
            raise MiniBindError("Код привязки недействителен или истёк")
        normalized = code.strip()
        if not 8 <= len(normalized) <= 128:
            raise MiniBindError("Код привязки недействителен или истёк")
        # Resolve the signing secret before consuming the one-time code: a
        # misconfigured secret must not burn a valid code.
        secret = self._secret()
        binding = self._repository.consume_binding_code(
            normalized, int(user.id), user.username
        )
        if binding is None:
            raise MiniBindError("Код привязки недействителен или истёк")
        account = accounts.get_user(binding.user_id)
        if account is None:
            raise MiniNotBound("Аккаунт привязки отключён или удалён")
        identity = self._identity(binding, account, user=user)
        token, expires = mint_mini_token(
            identity,
            secret,
            ttl_seconds=self._token_ttl,
            now=self._now(),
        )
        return identity, expires, token

    def authenticate_init_data(self, raw: str) -> Tuple[MiniIdentity, int, str]:
        """Verify initData, resolve the private-chat binding, mint a token.

        Returns ``(identity, expires_at, token)``; raises ``InitDataError`` for
        bad input and ``MiniNotBound`` when no usable binding exists.
        """
        bot_token = self._bot_token()
        user = verify_init_data(
            raw,
            bot_token,
            now=self._now(),
            max_age=self._init_max_age,
        )
        # A private Telegram chat has chat_id == user id; groups are rejected
        # by verify_init_data, so this cannot select a group binding.
        binding, account = self._binding_and_account(user.id)
        identity = self._identity(binding, account, user=user)
        token, expires = mint_mini_token(
            identity,
            self._secret(),
            ttl_seconds=self._token_ttl,
            now=self._now(),
        )
        return identity, expires, token

    def authorize(self, token: str) -> MiniIdentity:
        """Verify a bearer token and require the live binding/account matches."""
        claims = verify_mini_token(token, self._secret(), now=self._now())
        binding, account = self._binding_and_account(claims.chat_id)
        identity = self._identity(binding, account)
        if (
            identity.user_id != claims.user_id
            or identity.workspace_id != claims.workspace_id
            or identity.binding_epoch != claims.binding_epoch
        ):
            raise MiniTokenRevoked("Привязка Telegram изменилась; откройте Mini App заново")
        return identity

    def assert_live(self, identity: MiniIdentity) -> MiniIdentity:
        """Re-read binding/account after I/O; used before starting any work."""
        binding, account = self._binding_and_account(identity.chat_id)
        fresh = self._identity(binding, account)
        if (
            fresh.user_id != identity.user_id
            or fresh.workspace_id != identity.workspace_id
            or fresh.binding_epoch != identity.binding_epoch
        ):
            raise MiniTokenRevoked("Привязка Telegram изменилась; откройте Mini App заново")
        return fresh


def _load_bot_token_from_settings() -> str:
    # Delayed import keeps this module usable in isolation and avoids a cycle.
    from backend.app.telegram.bot_api import load_bot_settings

    return load_bot_settings().token
