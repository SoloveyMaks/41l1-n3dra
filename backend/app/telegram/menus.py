"""Telegram menu policy: signed callback data, WebApp URLs and keyboards.

The bot only shows menus in private chats. Callback data is short because
Telegram allows 64 bytes, so every button carries a signed action plus the
binding epoch. The signature covers the sender and chat ids that Telegram
supplied with the callback, which keeps a copied button from another chat or
user from being replayed. Nothing here trusts a workspace or chat id chosen by
the client: the runtime resolves those from the current binding.

This module is transport-free and has no DB access; it is pure policy and
payload building so it can be checked without Telegram or PostgreSQL.
"""

from __future__ import annotations

import hmac
import os
import re
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlsplit, urlunsplit

DEFAULT_MINI_APP_URL = "https://nedra.soloveyka.bond/miniapp"
DEFAULT_CABINET_URL = "https://nedra.soloveyka.bond/"
MINI_APP_ENV = "TELEGRAM_MINI_APP_URL"
CABINET_ENV = "TELEGRAM_WEB_CABINET_URL"

CALLBACK_VERSION = "n1"
CALLBACK_MAX_BYTES = 64
CALLBACK_SIGNATURE_HEX = 12
CALLBACK_ACTIONS = frozenset(
    {"menu", "checks", "settings", "help", "mode", "page", "scen", "refresh", "back"}
)
CALLBACK_ARG_RE = re.compile(r"^[A-Za-z0-9_-]{0,40}$")
SCENARIO_FINGERPRINT_RE = re.compile(r"^\d{1,4}-[0-9a-f]{6}$")

MODE_ORDER = ("alerts", "jsonc", "both")
MODE_LABELS = {"alerts": "Кратко", "jsonc": "JSON", "both": "Оба"}
SCENARIO_PAGE_SIZE = 6
BUTTON_TEXT_LIMIT = 56

BOT_COMMANDS: List[Dict[str, str]] = [
    {"command": "start", "description": "Меню и привязка аккаунта"},
    {"command": "status", "description": "Статус и последние проверки"},
    {"command": "settings", "description": "Режим доставки и сценарий"},
    {"command": "help", "description": "Справка по командам"},
]


class MenuConfigError(RuntimeError):
    """A public URL is unsafe to publish as a WebApp/menu target."""


def normalize_https_url(value: Any, *, env_name: str) -> str:
    """Return a bare HTTPS URL or raise ``MenuConfigError``.

    Credentials, query strings and fragments are rejected: a Mini App button or
    menu target must never carry a secret, and these settings are not a place
    for ad-hoc tracking parameters.
    """
    text = str(value or "").strip()
    if not text:
        raise MenuConfigError(f"{env_name} пуст")
    if len(text) > 512:
        raise MenuConfigError(f"{env_name} слишком длинный")
    if any(ch.isspace() or ord(ch) < 0x21 for ch in text):
        raise MenuConfigError(f"{env_name} содержит пробелы или управляющие символы")
    try:
        parts = urlsplit(text)
        parts.port  # raises ValueError for a malformed port
    except ValueError as exc:
        raise MenuConfigError(f"{env_name} не является корректным URL") from exc
    if parts.scheme.lower() != "https":
        raise MenuConfigError(f"{env_name} должен начинаться с https://")
    if not parts.hostname:
        raise MenuConfigError(f"{env_name} должен содержать хост")
    if parts.username is not None or parts.password is not None:
        raise MenuConfigError(f"{env_name} не должен содержать логин или пароль")
    if parts.query:
        raise MenuConfigError(f"{env_name} не должен содержать query-параметры")
    if parts.fragment:
        raise MenuConfigError(f"{env_name} не должен содержать fragment")
    path = parts.path or "/"
    if not path.startswith("/"):
        raise MenuConfigError(f"{env_name} должен содержать абсолютный путь")
    return urlunsplit(("https", parts.netloc, path, "", ""))


@dataclass(frozen=True)
class MenuConfig:
    mini_app_url: Optional[str] = DEFAULT_MINI_APP_URL
    cabinet_url: str = DEFAULT_CABINET_URL
    error: Optional[str] = None


def load_menu_config(env: Optional[Dict[str, str]] = None) -> MenuConfig:
    """Load public URLs; a bad override disables only the affected button.

    Polling, bindings and the outbox never depend on this configuration. An
    invalid Mini App override is not published at all (and is reported through
    ``MenuConfig.error``), while the cabinet link falls back to the known-safe
    public base so unbound users still get binding instructions.
    """
    source = os.environ if env is None else env
    error: Optional[str] = None
    mini_app_url: Optional[str]
    try:
        mini_app_url = normalize_https_url(
            source.get(MINI_APP_ENV) or DEFAULT_MINI_APP_URL,
            env_name=MINI_APP_ENV,
        )
    except MenuConfigError as exc:
        mini_app_url = None
        error = str(exc)

    cabinet_raw = source.get(CABINET_ENV) or DEFAULT_CABINET_URL
    try:
        cabinet_url = normalize_https_url(cabinet_raw, env_name=CABINET_ENV)
    except MenuConfigError as exc:
        cabinet_url = DEFAULT_CABINET_URL
        message = str(exc)
        error = f"{error}; {message}" if error else message

    return MenuConfig(mini_app_url=mini_app_url, cabinet_url=cabinet_url, error=error)


def callback_secret_from_token(token: str) -> str:
    """Derive the callback-signing secret from the bot token.

    The token already lives only in memory; deriving a separate key means a
    callback cannot be forged by a user who merely sees button labels, and no
    new secret has to be stored or deployed.
    """
    return hmac.new(
        str(token).encode("utf-8"),
        b"nedra-telegram-menu-v1",
        sha256,
    ).hexdigest()


def _callback_signature(secret: str, body: str, user_id: int, chat_id: int) -> str:
    message = f"{body}|{int(user_id)}|{int(chat_id)}".encode("utf-8")
    return hmac.new(
        str(secret).encode("utf-8"), message, sha256
    ).hexdigest()[:CALLBACK_SIGNATURE_HEX]


def encode_callback(
    secret: str,
    action: str,
    *,
    epoch: int,
    user_id: int,
    chat_id: int,
    arg: str = "",
) -> str:
    """Build compact signed callback data (always <= 64 bytes)."""
    if action not in CALLBACK_ACTIONS:
        raise ValueError("callback action is not allowed")
    if not isinstance(arg, str) or not CALLBACK_ARG_RE.fullmatch(arg):
        raise ValueError("callback argument is invalid")
    if not isinstance(secret, str) or not secret:
        raise ValueError("callback secret is not configured")
    safe_epoch = int(epoch)
    if safe_epoch < 0:
        raise ValueError("callback epoch is invalid")
    body = f"{CALLBACK_VERSION}.{action}"
    if arg:
        body = f"{body}.{arg}"
    body = f"{body}.{safe_epoch}"
    data = f"{body}.{_callback_signature(secret, body, user_id, chat_id)}"
    if len(data.encode("utf-8")) > CALLBACK_MAX_BYTES:
        raise ValueError("callback data exceeds 64 bytes")
    return data


@dataclass(frozen=True)
class MenuCallback:
    action: str
    arg: str
    epoch: int


def decode_callback(
    data: Any,
    secret: str,
    *,
    user_id: int,
    chat_id: int,
) -> Optional[MenuCallback]:
    """Verify signed callback data supplied by the sender's own private chat."""
    if not isinstance(data, str) or not data:
        return None
    try:
        raw = data.encode("utf-8")
    except UnicodeEncodeError:
        return None
    if len(raw) > CALLBACK_MAX_BYTES or not data.isascii():
        return None
    parts = data.split(".")
    if len(parts) == 4:
        version, action, epoch_text, signature = parts
        arg = ""
    elif len(parts) == 5:
        version, action, arg, epoch_text, signature = parts
    else:
        return None
    if version != CALLBACK_VERSION or action not in CALLBACK_ACTIONS:
        return None
    if not CALLBACK_ARG_RE.fullmatch(arg):
        return None
    if not epoch_text.isdigit() or len(epoch_text) > 10:
        return None
    if len(signature) != CALLBACK_SIGNATURE_HEX:
        return None
    body = f"{version}.{action}"
    if arg:
        body = f"{body}.{arg}"
    body = f"{body}.{epoch_text}"
    expected = _callback_signature(secret, body, user_id, chat_id)
    if not hmac.compare_digest(expected, signature):
        return None
    return MenuCallback(action=action, arg=arg, epoch=int(epoch_text))


def scenario_fingerprint(scenario_id: str) -> str:
    """Short stable tag that catches a shifted/stale scenario list."""
    return sha256(str(scenario_id).encode("utf-8")).hexdigest()[:6]


def scenario_arg(index: int, scenario_id: str) -> str:
    return f"{int(index)}-{scenario_fingerprint(scenario_id)}"


def parse_scenario_arg(arg: str) -> Optional[tuple]:
    if not isinstance(arg, str) or not SCENARIO_FINGERPRINT_RE.fullmatch(arg):
        return None
    index_text, fingerprint = arg.split("-", 1)
    return int(index_text), fingerprint


def truncate_button(text: str, limit: int = BUTTON_TEXT_LIMIT) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)].rstrip() + "…"


@dataclass(frozen=True)
class MenuContext:
    """Everything a keyboard needs; ids are the Telegram ids already verified."""

    secret: str = field(repr=False)
    user_id: int
    chat_id: int
    epoch: int
    config: MenuConfig

    def callback(self, action: str, arg: str = "") -> str:
        return encode_callback(
            self.secret,
            action,
            epoch=self.epoch,
            user_id=self.user_id,
            chat_id=self.chat_id,
            arg=arg,
        )


def _callback_button(text: str, ctx: MenuContext, action: str, arg: str = "") -> Dict[str, str]:
    return {"text": truncate_button(text), "callback_data": ctx.callback(action, arg)}


def _web_app_row(ctx: MenuContext) -> List[Dict[str, Any]]:
    url = ctx.config.mini_app_url
    if not url:
        return []
    return [{"text": "Открыть NEDRA", "web_app": {"url": url}}]


def _menu_row(ctx: MenuContext) -> List[Dict[str, str]]:
    return [_callback_button("В меню", ctx, "menu")]


def main_menu_markup(ctx: MenuContext) -> Dict[str, Any]:
    rows: List[List[Dict[str, Any]]] = []
    web_app = _web_app_row(ctx)
    if web_app:
        rows.append(web_app)
    rows.append([
        _callback_button("Проверки", ctx, "checks"),
        _callback_button("Настройки", ctx, "settings"),
    ])
    rows.append([_callback_button("Помощь", ctx, "help")])
    return {"inline_keyboard": rows}


def binding_markup(ctx: MenuContext) -> Optional[Dict[str, Any]]:
    rows: List[List[Dict[str, Any]]] = []
    cabinet = ctx.config.cabinet_url
    if cabinet:
        rows.append([{"text": "Открыть кабинет", "url": cabinet}])
    rows.append([_callback_button("Помощь", ctx, "help")])
    return {"inline_keyboard": rows}


def help_markup(ctx: MenuContext) -> Dict[str, Any]:
    rows: List[List[Dict[str, Any]]] = []
    web_app = _web_app_row(ctx)
    if web_app:
        rows.append(web_app)
    rows.append(_menu_row(ctx))
    return {"inline_keyboard": rows}


def checks_markup(ctx: MenuContext) -> Dict[str, Any]:
    rows: List[List[Dict[str, Any]]] = []
    web_app = _web_app_row(ctx)
    if web_app:
        rows.append(web_app)
    rows.append([
        _callback_button("Обновить", ctx, "refresh"),
        _callback_button("Настройки", ctx, "settings"),
    ])
    rows.append(_menu_row(ctx))
    return {"inline_keyboard": rows}


def settings_markup(
    ctx: MenuContext,
    *,
    delivery_mode: str,
    scenarios: Sequence[Dict[str, str]],
    page: int = 0,
    page_size: int = SCENARIO_PAGE_SIZE,
) -> Dict[str, Any]:
    rows: List[List[Dict[str, Any]]] = []
    web_app = _web_app_row(ctx)
    if web_app:
        rows.append(web_app)

    mode_buttons = []
    for mode in MODE_ORDER:
        label = MODE_LABELS[mode]
        prefix = "✓ " if mode == delivery_mode else ""
        mode_buttons.append(_callback_button(
            f"{prefix}{label}", ctx, "mode", str(MODE_ORDER.index(mode)),
        ))
    rows.append(mode_buttons)

    total = len(scenarios)
    pages = max(1, (total + page_size - 1) // page_size)
    safe_page = max(0, min(int(page), pages - 1))
    start = safe_page * page_size
    for index in range(start, min(total, start + page_size)):
        item = scenarios[index]
        scenario_id = str(item.get("id") or "")
        label = str(item.get("name") or scenario_id)
        rows.append([
            _callback_button(label, ctx, "scen", scenario_arg(index, scenario_id)),
        ])

    if total > page_size:
        navigation: List[Dict[str, str]] = []
        if safe_page > 0:
            navigation.append(_callback_button("‹ Назад", ctx, "page", str(safe_page - 1)))
        navigation.append(_callback_button(f"{safe_page + 1}/{pages}", ctx, "page", str(safe_page)))
        if safe_page + 1 < pages:
            navigation.append(_callback_button("Ещё ›", ctx, "page", str(safe_page + 1)))
        rows.append(navigation)

    rows.append(_menu_row(ctx))
    return {"inline_keyboard": rows}


def menu_button(config: MenuConfig) -> Optional[Dict[str, Any]]:
    """Chat menu button payload, or ``None`` when the Mini App URL is unusable."""
    if not config.mini_app_url:
        return None
    return {
        "type": "web_app",
        "text": "NEDRA",
        "web_app": {"url": config.mini_app_url},
    }


def scenario_page_count(total: int, page_size: int = SCENARIO_PAGE_SIZE) -> int:
    return max(1, (max(0, int(total)) + page_size - 1) // page_size)
