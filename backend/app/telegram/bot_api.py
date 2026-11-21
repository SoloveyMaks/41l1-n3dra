"""Minimal Telegram Bot API client with token-safe logging.

The token is read from the process environment or from a 0600 runtime file and
is kept only inside this object. It never appears in repr, logs, errors, argv or
returned dictionaries. All URLs/errors are redacted before they leave the
client.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import httpx

from backend.app.telegram.proxy_bridge import UnixSocketProxyBridge

TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
TOKEN_FILE_ENV = "TELEGRAM_BOT_TOKEN_FILE"
PROXY_SOCKET_ENV = "TELEGRAM_PROXY_SOCKET"
RUNTIME_ENV = Path.home() / ".config" / "nedra" / "runtime.env"
DEFAULT_TOKEN_FILE = Path.home() / ".config" / "nedra" / "telegram-bot.token"
DEFAULT_API_BASE = "https://api.telegram.org"
DEFAULT_BOT_KEY = "default"
MAX_MESSAGE_CHARS = 4096
MAX_DOCUMENT_BYTES = 45 * 1024 * 1024

_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")
_URL_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]+")
_TELEGRAM_URL_RE = re.compile(r"https?://[^\s\"']*api\.telegram\.org[^\s\"']*")


class _TelegramLogFilter(logging.Filter):
    """Keep httpx's request-line logs from exposing a bot token."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = _URL_TOKEN_RE.sub("bot***", record.msg)
            if record.args:
                record.args = tuple(
                    _URL_TOKEN_RE.sub("bot***", item) if isinstance(item, str) else item
                    for item in record.args
                )
        except Exception:  # noqa: BLE001 - logging must never fail the request
            pass
        return True


for _logger_name in ("httpx", "httpcore"):
    _lg = logging.getLogger(_logger_name)
    if not any(isinstance(item, _TelegramLogFilter) for item in _lg.filters):
        _lg.addFilter(_TelegramLogFilter())


class TelegramConfigError(RuntimeError):
    """Token/config is absent or unsafe to use."""


class TelegramUnavailable(RuntimeError):
    """Telegram is unreachable or rejected a request; sanitized text only."""


class TelegramAPIError(TelegramUnavailable):
    def __init__(self, status_code: int, description: str):
        super().__init__(f"telegram api {status_code}: {description}")
        self.status_code = status_code
        self.description = description


@dataclass(frozen=True)
class TelegramBotSettings:
    token: str = field(repr=False)
    base_url: str = DEFAULT_API_BASE
    bot_key: str = DEFAULT_BOT_KEY
    proxy_url: Optional[str] = field(default=None, repr=False)
    proxy_socket: Optional[str] = field(default=None, repr=False)

    @property
    def safe_base_url(self) -> str:
        return self.base_url.rstrip("/")


def _validate_proxy_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise TelegramConfigError("TELEGRAM_PROXY_URL is invalid") from exc
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise TelegramConfigError("TELEGRAM_PROXY_URL must be http(s)://host:port")
    if parts.username is not None or parts.password is not None:
        raise TelegramConfigError("TELEGRAM_PROXY_URL must not contain credentials")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise TelegramConfigError("TELEGRAM_PROXY_URL must be a bare proxy URL")
    host = parts.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default_port = 443 if parts.scheme == "https" else 80
    return f"{parts.scheme}://{host}:{port or default_port}"


def _validate_proxy_socket(value: str) -> str:
    if "\x00" in value or not value.startswith("/"):
        raise TelegramConfigError("TELEGRAM_PROXY_SOCKET must be an absolute path")
    normalized = os.path.normpath(value)
    if normalized in ("/", "//"):
        raise TelegramConfigError("TELEGRAM_PROXY_SOCKET must name a socket file")
    return normalized


def _read_secret_file(path: Path) -> str:
    try:
        info = path.stat()
    except OSError as exc:
        raise TelegramConfigError("token file unavailable") from exc
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise TelegramConfigError("token file permissions must be 0600")
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise TelegramConfigError("token file unreadable") from exc
    if not value:
        raise TelegramConfigError("token file is empty")
    return value


def _read_env_file_token(path: Path) -> str:
    try:
        info = path.stat()
    except OSError:
        return ""
    if stat.S_IMODE(info.st_mode) & 0o077:
        return ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, value = line.split("=", 1)
        if key.strip() != TOKEN_ENV:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        return value
    return ""


def load_bot_settings() -> TelegramBotSettings:
    """Load token from env, TELEGRAM_BOT_TOKEN_FILE, or the 0600 runtime.env."""
    token = os.getenv(TOKEN_ENV, "").strip()
    if not token:
        file_name = os.getenv(TOKEN_FILE_ENV, "").strip()
        path = Path(file_name).expanduser() if file_name else DEFAULT_TOKEN_FILE
        if path.exists():
            token = _read_secret_file(path)
        else:
            token = _read_env_file_token(RUNTIME_ENV)
    if not token:
        raise TelegramConfigError("telegram token is not configured")
    if not _TOKEN_RE.fullmatch(token):
        raise TelegramConfigError("telegram token format is invalid")
    base = os.getenv("TELEGRAM_API_BASE", DEFAULT_API_BASE).strip().rstrip("/")
    if not base.startswith("https://"):
        raise TelegramConfigError("TELEGRAM_API_BASE must be https")
    bot_key = os.getenv("TELEGRAM_BOT_KEY", DEFAULT_BOT_KEY).strip() or DEFAULT_BOT_KEY
    if len(bot_key) > 128:
        raise TelegramConfigError("TELEGRAM_BOT_KEY is too long")
    proxy_raw = os.getenv("TELEGRAM_PROXY_URL", "").strip()
    socket_raw = os.getenv(PROXY_SOCKET_ENV, "").strip()
    if proxy_raw and socket_raw:
        raise TelegramConfigError(
            "set only one of TELEGRAM_PROXY_URL or TELEGRAM_PROXY_SOCKET"
        )
    proxy_url = _validate_proxy_url(proxy_raw) if proxy_raw else None
    proxy_socket = _validate_proxy_socket(socket_raw) if socket_raw else None
    return TelegramBotSettings(
        token=token, base_url=base, bot_key=bot_key, proxy_url=proxy_url,
        proxy_socket=proxy_socket,
    )


def redact_telegram_text(
    text: Any,
    token: Optional[str] = None,
    proxy_url: Optional[str] = None,
) -> str:
    value = str(text or "")
    if token:
        value = value.replace(token, "***")
    if proxy_url:
        value = value.replace(proxy_url, "***")
    value = _URL_TOKEN_RE.sub("bot***", value)
    value = _TELEGRAM_URL_RE.sub("https://api.telegram.org/***", value)
    return value[:500]


class TelegramBotAPI:
    def __init__(self, settings: TelegramBotSettings):
        self._settings = settings
        self._client: Optional[httpx.AsyncClient] = None
        self._bridge: Optional[UnixSocketProxyBridge] = None
        self._client_lock = asyncio.Lock()
        self._closed = False
        self.bot_username: Optional[str] = None
        if settings.proxy_url:
            self._client = self._make_client(settings.proxy_url)

    def _make_client(self, proxy_url: Optional[str] = None) -> httpx.AsyncClient:
        client_kwargs: Dict[str, Any] = {
            "timeout": httpx.Timeout(35.0, connect=15.0),
            "follow_redirects": False,
            "headers": {"Accept": "application/json"},
            # Only the Telegram proxy setting is used for Bot API; global
            # HTTP(S)_PROXY and LLM httpx settings must not leak into it.
            "trust_env": False,
        }
        if proxy_url:
            client_kwargs["proxy"] = proxy_url
        return httpx.AsyncClient(**client_kwargs)

    async def _require_client(self) -> httpx.AsyncClient:
        if self._closed:
            raise TelegramUnavailable("telegram client is closed")
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._closed:
                raise TelegramUnavailable("telegram client is closed")
            if self._client is not None:
                return self._client
            socket_path = self._settings.proxy_socket
            if socket_path is None:
                self._client = self._make_client()
                return self._client
            bridge = UnixSocketProxyBridge(socket_path)
            try:
                info = os.stat(socket_path)
            except OSError:
                raise TelegramUnavailable(
                    f"telegram proxy socket unavailable: {socket_path}"
                ) from None
            if not stat.S_ISSOCK(info.st_mode):
                raise TelegramUnavailable(
                    f"telegram proxy socket unavailable: {socket_path} is not a socket"
                )
            try:
                port = await bridge.start()
            except OSError as exc:
                raise TelegramUnavailable(
                    f"telegram proxy socket unavailable: {exc.strerror or 'connect failed'}"
                ) from None
            try:
                self._client = self._make_client(f"http://127.0.0.1:{port}")
            except Exception:
                await bridge.close()
                raise
            self._bridge = bridge
            return self._client

    def __repr__(self) -> str:
        return "TelegramBotAPI(<redacted>)"

    @property
    def bot_key(self) -> str:
        return self._settings.bot_key

    @property
    def proxy_configured(self) -> bool:
        return bool(self._settings.proxy_url or self._settings.proxy_socket)

    @property
    def proxy_mode(self) -> Optional[str]:
        if self._settings.proxy_socket:
            return "socket"
        if self._settings.proxy_url:
            return "url"
        return None

    def redact(self, text: Any) -> str:
        return redact_telegram_text(
            text, self._settings.token, self._settings.proxy_url,
        )

    @classmethod
    def from_runtime(cls) -> "TelegramBotAPI":
        return cls(load_bot_settings())

    async def close(self) -> None:
        self._closed = True
        client, self._client = self._client, None
        bridge, self._bridge = self._bridge, None
        if client is not None:
            await client.aclose()
        if bridge is not None:
            await bridge.close()

    async def _call(
        self,
        method: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        timeout: Optional[float] = None,
    ) -> Any:
        url = f"{self._settings.safe_base_url}/bot{self._settings.token}/{method}"
        client = await self._require_client()
        try:
            response = await client.post(
                url,
                json=payload or {},
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            raise TelegramUnavailable(redact_telegram_text(exc, self._settings.token, self._settings.proxy_url)) from None

        if response.status_code >= 400:
            description = "request failed"
            try:
                body = response.json()
                if isinstance(body, dict) and isinstance(body.get("description"), str):
                    description = body["description"]
            except ValueError:
                pass
            raise TelegramAPIError(
                response.status_code,
                redact_telegram_text(
                    description, self._settings.token, self._settings.proxy_url,
                ),
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise TelegramUnavailable("telegram returned non-json") from exc
        if not isinstance(body, dict) or body.get("ok") is not True:
            description = body.get("description") if isinstance(body, dict) else "bad response"
            raise TelegramUnavailable(
                redact_telegram_text(
                    description, self._settings.token, self._settings.proxy_url,
                )
            )
        return body.get("result")

    async def get_me(self) -> Dict[str, Any]:
        result = await self._call("getMe")
        if not isinstance(result, dict):
            raise TelegramUnavailable("telegram getMe shape is invalid")
        username = result.get("username")
        if isinstance(username, str) and username:
            self.bot_username = username
        return result

    async def get_updates(
        self,
        offset: int,
        *,
        timeout: int = 25,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        safe_timeout = max(1, min(int(timeout), 50))
        result = await self._call(
            "getUpdates",
            {
                "offset": max(0, int(offset)),
                "timeout": safe_timeout,
                "limit": max(1, min(int(limit), 100)),
                "allowed_updates": ["message"],
            },
            timeout=float(safe_timeout + 15),
        )
        if not isinstance(result, list):
            raise TelegramUnavailable("telegram getUpdates shape is invalid")
        return [item for item in result if isinstance(item, dict)]

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        disable_web_page_preview: bool = True,
    ) -> Dict[str, Any]:
        body = (text or "")[:MAX_MESSAGE_CHARS]
        result = await self._call(
            "sendMessage",
            {
                "chat_id": int(chat_id),
                "text": body,
                "disable_web_page_preview": bool(disable_web_page_preview),
            },
        )
        return result if isinstance(result, dict) else {}

    async def send_document(
        self,
        chat_id: int,
        filename: str,
        content: bytes,
        *,
        caption: str = "",
    ) -> Dict[str, Any]:
        if not content or len(content) > MAX_DOCUMENT_BYTES:
            raise TelegramUnavailable("document is empty or too large")
        safe_name = Path(str(filename)).name
        if not safe_name or safe_name != str(filename) or len(safe_name) > 120:
            safe_name = "nedra-report.jsonc"
        url = f"{self._settings.safe_base_url}/bot{self._settings.token}/sendDocument"
        client = await self._require_client()
        try:
            response = await client.post(
                url,
                data={"chat_id": str(int(chat_id)), "caption": (caption or "")[:1024]},
                files={"document": (safe_name, content, "application/json")},
                timeout=60.0,
            )
        except httpx.HTTPError as exc:
            raise TelegramUnavailable(redact_telegram_text(exc, self._settings.token, self._settings.proxy_url)) from None
        if response.status_code >= 400:
            try:
                body = response.json()
                description = body.get("description") if isinstance(body, dict) else None
            except ValueError:
                description = None
            raise TelegramAPIError(
                response.status_code,
                redact_telegram_text(
                    description or "sendDocument failed",
                    self._settings.token, self._settings.proxy_url,
                ),
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise TelegramUnavailable("telegram returned non-json") from exc
        if not isinstance(body, dict) or body.get("ok") is not True:
            raise TelegramUnavailable("telegram sendDocument was not accepted")
        return body.get("result") if isinstance(body.get("result"), dict) else {}