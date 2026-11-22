"""Regression checks for Telegram buttons, callbacks and settings guards.

Run without a database (menus, callbacks, transport payloads and command
compatibility use an in-memory repository):

    /tmp/nedra-venv/bin/python backend/tests/test_telegram_buttons.py

Add a disposable PostgreSQL probe for the epoch-guarded settings write:

    TELEGRAM_TEST_DATABASE_URL=postgresql://postgres:x@127.0.0.1:55432/nedra_tg_buttons_probe \
        /tmp/nedra-venv/bin/python backend/tests/test_telegram_buttons.py

The probe refuses a database whose name does not start with
``nedra_tg_buttons_`` so it cannot be pointed at production by accident. No
Telegram network call is made: the API object is a recording fake.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.app.storage.telegram import (  # noqa: E402
    Binding,
    Job,
    Settings,
    SettingsOwnershipLost,
)
from backend.app.telegram.bot_api import (  # noqa: E402
    TelegramAPIError,
    TelegramBotAPI,
    TelegramBotSettings,
)
from backend.app.telegram.menus import (  # noqa: E402
    DEFAULT_CABINET_URL,
    DEFAULT_MINI_APP_URL,
    MenuConfig,
    MenuConfigError,
    SCENARIO_PAGE_SIZE,
    encode_callback,
    load_menu_config,
    normalize_https_url,
    scenario_arg,
)
from backend.app.telegram.runtime import TelegramRuntime  # noqa: E402

USER_ID = "11111111-1111-1111-1111-111111111111"
WORKSPACE_ID = "22222222-2222-2222-2222-222222222222"
SECRET = "probe-secret"
CHAT_ID = 4242
SCENARIOS = [
    {"id": "wf-alpha", "name": "Alpha"},
    {"id": "wf-beta", "name": "Beta"},
]
for _index in range(SCENARIO_PAGE_SIZE):
    SCENARIOS.append({"id": f"wf-extra-{_index}", "name": f"Extra {_index}"})

RESULTS: List[tuple] = []


def check(name: str, condition: Any, detail: str = "") -> None:
    RESULTS.append((name, bool(condition), detail))
    if not condition:
        print(f"FAIL {name}: {detail}")


class FakeRepo:
    """Synchronous storage surface used by TelegramRuntime via to_thread."""

    def __init__(self, *, binding: Optional[Binding] = None) -> None:
        self.binding = binding
        self.settings = Settings(chat_id=CHAT_ID, delivery_mode="both", scenario="wf-alpha")
        self.writes: List[Dict[str, Any]] = []
        self.claimed: List[int] = []
        self.finished: List[tuple] = []
        self._claimed_ids = set()

    def claim_update(self, update_id: int, chat_id: Optional[int]) -> bool:
        if update_id in self._claimed_ids:
            return False
        self._claimed_ids.add(update_id)
        self.claimed.append(update_id)
        return True

    def finish_update(self, update_id: int, status: str, error: Optional[str] = None) -> None:
        self.finished.append((update_id, status, error))

    def get_binding(self, chat_id: int) -> Optional[Binding]:
        return self.binding

    def get_settings(self, chat_id: int, **kwargs: Any) -> Settings:
        epoch = kwargs.get("binding_epoch")
        if epoch is not None:
            if self.binding is None or int(epoch) != int(self.binding.binding_epoch):
                return Settings(chat_id=CHAT_ID)
        return self.settings

    def set_settings(self, chat_id: int, **kwargs: Any) -> Settings:
        self.writes.append(dict(kwargs))
        if self.binding is None or int(kwargs["binding_epoch"]) != int(self.binding.binding_epoch):
            raise SettingsOwnershipLost("binding changed")
        if kwargs.get("delivery_mode") is not None:
            self.settings = Settings(
                chat_id=CHAT_ID,
                delivery_mode=str(kwargs["delivery_mode"]),
                scenario=self.settings.scenario,
            )
        if kwargs.get("scenario") is not None:
            self.settings = Settings(
                chat_id=CHAT_ID,
                delivery_mode=self.settings.delivery_mode,
                scenario=str(kwargs["scenario"]),
            )
        return self.settings

    def list_jobs(self, *args: Any, **kwargs: Any) -> List[Job]:
        return []

    def consume_binding_code(self, code: str, chat_id: int, username: Optional[str]) -> Optional[Binding]:
        if code != "ValidCode1234":
            return None
        self.binding = Binding(
            chat_id=CHAT_ID,
            user_id=USER_ID,
            workspace_id=WORKSPACE_ID,
            telegram_username=username,
            binding_epoch=1,
        )
        return self.binding


class FakeDB:
    def list_runs(self, limit: int = 5) -> List[Dict[str, Any]]:
        return []

    def list_workflows(self) -> List[Dict[str, Any]]:
        return [dict(item) for item in SCENARIOS]

    def get_workflow(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        if any(item["id"] == workflow_id for item in SCENARIOS):
            return {"id": workflow_id, "draft": {}}
        return None


class FakeAPI:
    def __init__(self, secret: str = SECRET) -> None:
        self.bot_key = "probe"
        self.bot_username = "probe_bot"
        self.proxy_configured = False
        self.proxy_mode = None
        self.callback_secret = secret
        self.calls: List[tuple] = []
        self.edit_error: Optional[Exception] = None
        self.menu_error: Optional[Exception] = None
        self.commands_error: Optional[Exception] = None

    def redact(self, text: Any) -> str:
        return str(text)[:500]

    async def answer_callback_query(self, query_id: str, **kwargs: Any) -> bool:
        self.calls.append(("ack", query_id, kwargs))
        return True

    async def edit_message_text(self, chat_id: int, message_id: int, text: str, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(("edit", chat_id, message_id, text, kwargs))
        if self.edit_error is not None:
            raise self.edit_error
        return {"message_id": message_id}

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(("send", chat_id, text, kwargs))
        return {"message_id": 1}

    async def set_my_commands(self, commands: List[Dict[str, str]]) -> bool:
        self.calls.append(("commands", commands))
        if self.commands_error is not None:
            raise self.commands_error
        return True

    async def set_chat_menu_button(self, button: Dict[str, Any]) -> bool:
        self.calls.append(("menu", button))
        if self.menu_error is not None:
            raise self.menu_error
        return True

    def kinds(self, kind: str) -> List[tuple]:
        return [call for call in self.calls if call[0] == kind]


def bound_repo(epoch: int = 2) -> FakeRepo:
    return FakeRepo(binding=Binding(
        chat_id=CHAT_ID,
        user_id=USER_ID,
        workspace_id=WORKSPACE_ID,
        telegram_username="probe",
        binding_epoch=epoch,
    ))


def make_runtime(repo: FakeRepo, api: FakeAPI, *, mini_app_url: str = DEFAULT_MINI_APP_URL) -> TelegramRuntime:
    return TelegramRuntime(
        FakeDB(), None, None,
        repository=repo,
        api=api,
        menu_config=MenuConfig(mini_app_url=mini_app_url, cabinet_url=DEFAULT_CABINET_URL),
        menu_secret=SECRET,
    )


def callback_update(
    data: str,
    *,
    update_id: int,
    user_id: int = CHAT_ID,
    chat_id: int = CHAT_ID,
    chat_type: str = "private",
    message_id: int = 77,
) -> Dict[str, Any]:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"query-{update_id}",
            "from": {"id": user_id, "is_bot": False},
            "message": {
                "message_id": message_id,
                "chat": {"id": chat_id, "type": chat_type},
            },
            "data": data,
        },
    }


def message_update(
    text: str,
    *,
    update_id: int,
    chat_id: int = CHAT_ID,
    chat_type: str = "private",
    sender_id: Optional[int] = None,
) -> Dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "chat": {"id": chat_id, "type": chat_type},
            "from": {
                "id": chat_id if sender_id is None else sender_id,
                "is_bot": False,
                "username": "probe",
            },
            "text": text,
        },
    }


def check_url_policy() -> None:
    check("default mini app URL", load_menu_config({}).mini_app_url == DEFAULT_MINI_APP_URL)
    check(
        "valid override",
        load_menu_config({"TELEGRAM_MINI_APP_URL": "https://example.org/app"}).mini_app_url
        == "https://example.org/app",
    )
    for value, name in [
        ("http://example.org/app", "http URL rejected"),
        ("https://user:pass@example.org/app", "credentials rejected"),
        ("https://example.org/app?token=1", "query rejected"),
        ("https://example.org/app#frag", "fragment rejected"),
        ("https:///missing-host", "missing host rejected"),
        ("https://example.org/a b", "space rejected"),
    ]:
        try:
            normalize_https_url(value, env_name="TEST_URL")
        except MenuConfigError:
            check(name, True)
        else:
            check(name, False, value)
    invalid = load_menu_config({"TELEGRAM_MINI_APP_URL": "javascript:alert(1)"})
    check("invalid override disables mini app", invalid.mini_app_url is None)
    check("invalid override keeps cabinet", invalid.cabinet_url == DEFAULT_CABINET_URL)
    check("invalid override is reported", bool(invalid.error))


async def check_callbacks() -> None:
    # Foreign sender and group chats are rejected before claiming the update.
    repo, api = bound_repo(), FakeAPI()
    runtime = make_runtime(repo, api)
    signed = encode_callback(
        SECRET, "mode", epoch=2, user_id=CHAT_ID, chat_id=CHAT_ID, arg="0",
    )
    await runtime.handle_update(callback_update(signed, update_id=1, user_id=999))
    check("foreign sender rejected", not repo.claimed and not api.calls)
    await runtime.handle_update(callback_update(signed, update_id=2, chat_type="group"))
    check("group callback rejected", not repo.claimed and not api.calls)
    await runtime.handle_update(callback_update(signed, update_id=3, chat_id=CHAT_ID + 1))
    check("foreign chat rejected", not repo.claimed and not api.calls)

    # Invalid and oversized data: polite alert, refreshed menu, no write.
    # The n1 callback is signed exactly like the old release, so only the
    # version check can reject it.
    legacy_body = "n1.mode.0.2"
    legacy_signature = hmac.new(
        SECRET.encode("utf-8"),
        f"{legacy_body}|{CHAT_ID}|{CHAT_ID}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:12]
    legacy = f"{legacy_body}.{legacy_signature}"
    for index, payload in enumerate(["garbage", "x" * 65, legacy], start=10):
        repo, api = bound_repo(), FakeAPI()
        runtime = make_runtime(repo, api)
        await runtime.handle_update(callback_update(payload, update_id=index))
        acks = api.kinds("ack")
        check(f"invalid callback {index} answered once", len(acks) == 1)
        check(f"invalid callback {index} shows alert", acks and acks[0][2].get("show_alert") is True)
        check(f"invalid callback {index} rewrites no settings", not repo.writes)
        check(f"invalid callback {index} refreshes view", bool(api.kinds("edit") or api.kinds("send")))

    # Stale epoch: button signed before a rebind must not write.
    repo, api = bound_repo(epoch=3), FakeAPI()
    runtime = make_runtime(repo, api)
    stale = encode_callback(
        SECRET, "mode", epoch=2, user_id=CHAT_ID, chat_id=CHAT_ID, arg="0",
    )
    await runtime.handle_update(callback_update(stale, update_id=20))
    check("stale epoch writes nothing", not repo.writes)
    check("stale epoch answered", len(api.kinds("ack")) == 1)
    edits = api.kinds("edit")
    check("stale epoch refreshes menu", bool(edits) and "перепривяз" in edits[-1][3])
    markup = edits[-1][4].get("reply_markup") if edits else {}
    stale_buttons = [
        button.get("callback_data", "")
        for row in (markup or {}).get("inline_keyboard", [])
        for button in row
    ]
    check(
        "refreshed buttons use current epoch",
        all(f".{3}." in value for value in stale_buttons if value),
    )

    # Happy delivery-mode selection, including selected checkmark.
    repo, api = bound_repo(epoch=2), FakeAPI()
    runtime = make_runtime(repo, api)
    good = encode_callback(
        SECRET, "mode", epoch=2, user_id=CHAT_ID, chat_id=CHAT_ID, arg="1",
    )
    await runtime.handle_update(callback_update(good, update_id=30))
    check(
        "mode selection writes current binding",
        repo.writes
        and repo.writes[-1]["delivery_mode"] == "jsonc"
        and repo.writes[-1]["binding_epoch"] == 2
        and repo.writes[-1]["workspace_id"] == WORKSPACE_ID,
    )
    check("mode selection answers once", len(api.kinds("ack")) == 1)
    edit = api.kinds("edit")[-1]
    labels = [
        button["text"]
        for row in edit[4]["reply_markup"]["inline_keyboard"]
        for button in row
        if button.get("callback_data", "").startswith("n2.mode.")
    ]
    check("selected mode has check", "✓ JSON" in labels and "Кратко" in labels, labels)
    check("mode edit mentions delivery", "JSON" in edit[3])

    # Happy scenario selection uses a fresh ownership-validated workflow.
    repo, api = bound_repo(epoch=2), FakeAPI()
    runtime = make_runtime(repo, api)
    scen = encode_callback(
        SECRET, "scen", epoch=2, user_id=CHAT_ID, chat_id=CHAT_ID,
        arg=scenario_arg(1, "wf-beta"),
    )
    await runtime.handle_update(callback_update(scen, update_id=40))
    check(
        "scenario selection writes workspace scenario",
        repo.writes
        and repo.writes[-1]["scenario"] == "wf-beta"
        and repo.writes[-1]["binding_epoch"] == 2,
        repo.writes,
    )
    check("scenario selection answers once", len(api.kinds("ack")) == 1)
    check("scenario selection confirms label", "Beta" in api.kinds("edit")[-1][3])

    # A shifted scenario list fails the fingerprint check instead of guessing.
    repo, api = bound_repo(epoch=2), FakeAPI()
    runtime = make_runtime(repo, api)
    shifted = encode_callback(
        SECRET, "scen", epoch=2, user_id=CHAT_ID, chat_id=CHAT_ID,
        arg=scenario_arg(1, "wf-alpha"),
    )
    await runtime.handle_update(callback_update(shifted, update_id=41))
    check("shifted scenario list writes nothing", not repo.writes)
    check("shifted scenario list explains", "изменился" in api.kinds("edit")[-1][3])

    # Duplicate update_id is claimed once and never writes twice.
    repo, api = bound_repo(epoch=2), FakeAPI()
    runtime = make_runtime(repo, api)
    duplicate = encode_callback(
        SECRET, "mode", epoch=2, user_id=CHAT_ID, chat_id=CHAT_ID, arg="2",
    )
    await runtime.handle_update(callback_update(duplicate, update_id=50))
    await runtime.handle_update(callback_update(duplicate, update_id=50))
    check("duplicate callback claimed once", repo.claimed.count(50) == 1)
    check("duplicate callback writes once", len(repo.writes) == 1)
    check("duplicate callback answered once", len(api.kinds("ack")) == 1)

    # Ack happens before DB-bound rendering, and edit failure falls back to send.
    repo, api = bound_repo(epoch=2), FakeAPI()
    api.edit_error = TelegramAPIError(400, "message can't be edited")
    runtime = make_runtime(repo, api)
    checked = encode_callback(
        SECRET, "checks", epoch=2, user_id=CHAT_ID, chat_id=CHAT_ID,
    )
    await runtime.handle_update(callback_update(checked, update_id=60))
    order = [call[0] for call in api.calls]
    check("ack precedes render", order.index("ack") < order.index("edit"), order)
    check("edit failure falls back to send", bool(api.kinds("send")), order)
    check("checks view shows menu entry", any(
        button.get("web_app", {}).get("url") == DEFAULT_MINI_APP_URL
        for call in api.kinds("send")
        for row in call[3]["reply_markup"]["inline_keyboard"]
        for button in row
    ))


async def check_start_and_commands() -> None:
    # Unbound /start: short primary flow, code comes as the next plain message.
    repo, api = FakeRepo(), FakeAPI()
    runtime = make_runtime(repo, api)
    await runtime.handle_update(message_update("/start", update_id=100))
    sent = api.kinds("send")
    check("unbound /start replies", len(sent) == 1)
    text = sent[0][2]
    check("unbound /start explains cabinet", DEFAULT_CABINET_URL in text, text)
    check("unbound /start asks for next message", "следующим сообщением" in text, text)
    check("unbound /start explains code", "10 минут" in text and "один раз" in text, text)
    check(
        "unbound /start avoids command syntax",
        "/start <код>" not in text and "/settings" not in text,
        text,
    )
    check("unbound /start is not fake-bound", "Аккаунт привязан" not in text, text)
    markup = sent[0][3]["reply_markup"]
    buttons = [button for row in markup["inline_keyboard"] for button in row]
    check(
        "unbound /start has cabinet button",
        any(button.get("url") == DEFAULT_CABINET_URL for button in buttons),
        markup,
    )
    check(
        "unbound /start has mini app button",
        any(button.get("web_app", {}).get("url") == DEFAULT_MINI_APP_URL for button in buttons),
        markup,
    )
    check("unbound /start does not claim a binding", repo.binding is None)

    # A plain wrong code is answered, not silently turned into generic help.
    repo, api = FakeRepo(), FakeAPI()
    runtime = make_runtime(repo, api)
    await runtime.handle_update(message_update("abcdef", update_id=105))
    sent = api.kinds("send")
    check("invalid plain code replies", len(sent) == 1)
    check("invalid plain code explains retry", "Код не подошёл" in sent[0][2], sent[0][2])
    check("invalid plain code does not bind", repo.binding is None)

    # /start <code> and bare code remain compatible.
    for index, command in enumerate(("/start ValidCode1234", "ValidCode1234"), start=101):
        repo, api = FakeRepo(), FakeAPI()
        runtime = make_runtime(repo, api)
        await runtime.handle_update(message_update(command, update_id=index))
        check(f"{command} binds", repo.binding is not None)
        sends = api.kinds("send")
        check(f"{command} sends menu and settings", len(sends) == 2, len(sends))
        check(f"{command} confirms", "привязан" in sends[0][2].lower(), sends[0][2])
        labels = [
            button.get("text")
            for row in sends[0][3]["reply_markup"]["inline_keyboard"]
            for button in row
        ]
        check(f"{command} shows menu", "Проверки" in labels and "Настройки" in labels, labels)
        check(f"{command} shows settings", "Настройки NEDRA" in sends[1][2], sends[1][2])
        settings_labels = [
            str(button.get("text")).lstrip("✓ ")
            for row in sends[1][3]["reply_markup"]["inline_keyboard"]
            for button in row
        ]
        check(
            f"{command} settings use same labels",
            "Кратко" in settings_labels and "JSON" in settings_labels
            and "Оба" in settings_labels and "Alpha" in settings_labels,
            settings_labels,
        )

    # Bound /start greeting with all four menu entries.
    repo, api = bound_repo(epoch=2), FakeAPI()
    runtime = make_runtime(repo, api)
    await runtime.handle_update(message_update("/start", update_id=110))
    markup = api.kinds("send")[-1][3]["reply_markup"]
    labels = [button.get("text") for row in markup["inline_keyboard"] for button in row]
    check(
        "bound /start has web_app/checks/settings/help",
        "Открыть NEDRA" in labels and "Проверки" in labels
        and "Настройки" in labels and "Помощь" in labels,
        labels,
    )
    check(
        "bound /start shows status",
        "NEDRA на связи" in api.kinds("send")[-1][2]
        and "Alpha" in api.kinds("send")[-1][2],
    )

    # Legacy text commands still write and render settings.
    repo, api = bound_repo(epoch=2), FakeAPI()
    runtime = make_runtime(repo, api)
    await runtime.handle_update(message_update("/settings alerts", update_id=111))
    check("legacy /settings alerts writes", repo.writes and repo.writes[-1]["delivery_mode"] == "alerts")
    repo, api = bound_repo(epoch=2), FakeAPI()
    runtime = make_runtime(repo, api)
    await runtime.handle_update(message_update("/settings", update_id=112))
    markup = api.kinds("send")[-1][3]["reply_markup"]
    labels = [button.get("text") for row in markup["inline_keyboard"] for button in row]
    stripped = [str(label).lstrip("✓ ") for label in labels]
    check(
        "bare /settings renders buttons",
        "Кратко" in stripped and "JSON" in stripped and "Оба" in stripped, labels,
    )
    await runtime.handle_update(message_update("/status", update_id=113))
    check("legacy /status still works", "Проверки NEDRA" in api.kinds("send")[-1][2])
    await runtime.handle_update(message_update("/help", update_id=114))
    help_text = api.kinds("send")[-1][2]
    check("legacy /help still works", "/settings" in help_text)
    check(
        "help does not primary-advertise legacy syntax",
        "/settings alerts" not in help_text and "scenario <id>" not in help_text,
        help_text,
    )
    await runtime.handle_update(message_update("просто текст", update_id=115))
    check("unknown text falls back to help", "/help" in api.kinds("send")[-1][2])

    # Listener regression: group/foreign messages still never claim or reply.
    repo, api = bound_repo(epoch=2), FakeAPI()
    runtime = make_runtime(repo, api)
    await runtime.handle_update(message_update("/start", update_id=118, chat_type="group"))
    await runtime.handle_update(message_update("/start", update_id=119, sender_id=999))
    check("group/foreign messages ignored", not repo.claimed and not api.calls)

    # A rebound user pressing an old /settings message gets honest refresh.
    repo, api = bound_repo(epoch=2), FakeAPI()
    runtime = make_runtime(repo, api)
    await runtime.handle_update(message_update("/settings", update_id=116))
    old_button = next(
        button["callback_data"]
        for row in api.kinds("send")[-1][3]["reply_markup"]["inline_keyboard"]
        for button in row
        if button.get("callback_data", "").startswith("n2.mode.")
    )
    repo.binding = Binding(
        chat_id=CHAT_ID, user_id=USER_ID, workspace_id=WORKSPACE_ID,
        telegram_username="probe", binding_epoch=5,
    )
    writes_before = len(repo.writes)
    await runtime.handle_update(callback_update(old_button, update_id=117))
    check("rebind old message writes nothing", len(repo.writes) == writes_before)
    check("rebind old message refreshes", "перепривяз" in api.kinds("edit")[-1][3])


async def check_menu_payloads() -> None:
    api = TelegramBotAPI(TelegramBotSettings(token="123456789:" + "A" * 30))
    payloads: List[tuple] = []

    async def fake_call(method: str, payload: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Any:
        payloads.append((method, payload or {}))
        if method == "getUpdates":
            return []
        if method == "setMyCommands":
            return True
        if method == "setChatMenuButton":
            return True
        return True

    api._call = fake_call  # type: ignore[method-assign]
    await api.get_updates(0)
    check(
        "getUpdates asks for callbacks",
        payloads[-1][1].get("allowed_updates") == ["message", "callback_query"],
        payloads[-1][1],
    )
    await api.send_message(1, "hi", reply_markup={"inline_keyboard": []})
    check("sendMessage carries reply_markup", "reply_markup" in payloads[-1][1])
    await api.edit_message_text(1, 2, "hi", reply_markup={"inline_keyboard": []})
    check(
        "editMessageText carries ids and markup",
        payloads[-1][1]["message_id"] == 2 and "reply_markup" in payloads[-1][1],
    )
    await api.answer_callback_query("query-id", text="ok", show_alert=True)
    check("answerCallbackQuery sends id", payloads[-1][1]["callback_query_id"] == "query-id")
    await api.set_my_commands([
        {"command": "start", "description": "ok"},
        {"command": "Bad Command", "description": "no"},
    ])
    check("setMyCommands filters invalid names", payloads[-1][1]["commands"] == [
        {"command": "start", "description": "ok"},
    ])
    await api.set_chat_menu_button({"type": "web_app", "text": "x", "web_app": {"url": "http://bad"}})
    check("setChatMenuButton rejects non-HTTPS", payloads[-1][0] != "setChatMenuButton")
    check(
        "callback secret is derived",
        isinstance(api.callback_secret, str) and len(api.callback_secret) >= 32,
    )

    # Largest expected payload stays under Telegram's 64-byte limit.
    biggest = encode_callback(
        SECRET, "scen", epoch=9_999_999_999, user_id=9_999_999_999,
        chat_id=9_999_999_999, arg=scenario_arg(9999, "wf-" + "x" * 32),
    )
    check("callback data fits 64 bytes", len(biggest.encode()) <= 64, len(biggest.encode()))

    # Startup publishing is best-effort: commands and web_app menu button once,
    # and a Bot API failure must not escape into polling startup.
    repo, fake = bound_repo(), FakeAPI()
    runtime = make_runtime(repo, fake)
    await runtime._publish_menu_once()
    check("startup sets commands", bool(fake.kinds("commands")))
    check(
        "startup sets web_app menu",
        fake.kinds("menu")
        and fake.kinds("menu")[0][1]["web_app"]["url"] == DEFAULT_MINI_APP_URL,
    )
    fake.calls.clear()
    await runtime._publish_menu_once()
    check("menu is published once", not fake.kinds("commands") and not fake.kinds("menu"))

    repo, fake = bound_repo(), FakeAPI()
    fake.menu_error = TelegramAPIError(400, "menu rejected")
    runtime = make_runtime(repo, fake)
    try:
        await runtime._publish_menu_once()
    except Exception as exc:  # noqa: BLE001 - assertion below reports it
        check("menu failure does not escape", False, repr(exc))
    else:
        check("menu failure does not escape", True)
    check("menu failure is recorded", bool(runtime.status()["menuError"]))

    repo, fake = bound_repo(), FakeAPI()
    runtime = make_runtime(repo, fake, mini_app_url="")
    runtime.menu_config = MenuConfig(mini_app_url=None, cabinet_url=DEFAULT_CABINET_URL, error="bad url")
    await runtime._publish_menu_once()
    check("bad mini app still sets commands", bool(fake.kinds("commands")))
    check("bad mini app publishes no menu", not fake.kinds("menu"))


def check_db_probe() -> None:
    dsn = os.environ.get("TELEGRAM_TEST_DATABASE_URL", "").strip()
    if not dsn:
        print("SKIP DB probe: TELEGRAM_TEST_DATABASE_URL is not set")
        return
    try:
        import psycopg
    except ImportError:
        check("DB probe driver", False, "psycopg is not installed")
        return
    with psycopg.connect(dsn, connect_timeout=5, autocommit=True) as conn:
        db_name = conn.execute("SELECT current_database()").fetchone()[0]
        if not str(db_name).startswith("nedra_tg_buttons_"):
            check(
                "DB probe uses disposable database", False,
                f"refusing database {db_name!r}; name must start with nedra_tg_buttons_",
            )
            return
        conn.execute("CREATE SCHEMA IF NOT EXISTS data_contract")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS data_contract.workspaces ("
            "workspace_id uuid PRIMARY KEY, name text NOT NULL DEFAULT '')"
        )
        for migration_name in (
            "010_telegram.up.sql",
            "011_telegram_rebind.up.sql",
            "012_telegram_job_recovery.up.sql",
        ):
            migration = ROOT / "db" / "migrations" / migration_name
            conn.execute(migration.read_text(encoding="utf-8"))

    os.environ["DATABASE_URL"] = dsn
    from backend.app.storage.telegram import TelegramRepository
    from backend.app.storage.database import _close_pool, get_pool

    try:
        repo = TelegramRepository()
        with get_pool().connection() as conn:
            conn.execute(
                "INSERT INTO data_contract.workspaces (workspace_id, name) VALUES (%s, 'probe') "
                "ON CONFLICT (workspace_id) DO NOTHING",
                (WORKSPACE_ID,),
            )
            conn.execute("DELETE FROM telegram.bindings WHERE chat_id = %s", (CHAT_ID,))
            conn.execute(
                "INSERT INTO telegram.bindings "
                "(chat_id, user_id, workspace_id, telegram_username, binding_epoch) "
                "VALUES (%s, %s, %s, 'probe', 2)",
                (CHAT_ID, USER_ID, WORKSPACE_ID),
            )
            conn.execute(
                "INSERT INTO telegram.settings (chat_id, delivery_mode, scenario) "
                "VALUES (%s, 'both', '') ON CONFLICT (chat_id) DO NOTHING",
                (CHAT_ID,),
            )
        fresh = repo.set_settings(
            CHAT_ID, user_id=USER_ID, workspace_id=WORKSPACE_ID,
            binding_epoch=2, delivery_mode="jsonc",
        )
        check("DB accepts current epoch", fresh.delivery_mode == "jsonc")
        try:
            repo.set_settings(
                CHAT_ID, user_id=USER_ID, workspace_id=WORKSPACE_ID,
                binding_epoch=1, delivery_mode="alerts",
            )
        except SettingsOwnershipLost:
            check("DB rejects stale epoch", True)
        else:
            check("DB rejects stale epoch", False)
        with get_pool().connection() as conn:
            stored = conn.execute(
                "SELECT delivery_mode FROM telegram.settings WHERE chat_id = %s",
                (CHAT_ID,),
            ).fetchone()
        check("DB keeps the accepted value", stored["delivery_mode"] == "jsonc", stored)
        with get_pool().connection() as conn:
            conn.execute("UPDATE telegram.bindings SET binding_epoch = 3 WHERE chat_id = %s", (CHAT_ID,))
        try:
            repo.set_settings(
                CHAT_ID, user_id=USER_ID, workspace_id=WORKSPACE_ID,
                binding_epoch=2, delivery_mode="alerts",
            )
        except SettingsOwnershipLost:
            check("DB rejects pre-rebind epoch", True)
        else:
            check("DB rejects pre-rebind epoch", False)
    finally:
        _close_pool()


def main() -> int:
    check_url_policy()
    asyncio.run(check_menu_payloads())
    asyncio.run(check_callbacks())
    asyncio.run(check_start_and_commands())
    check_db_probe()
    failed = [row for row in RESULTS if not row[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        for name, _, detail in failed:
            print(f"  FAILED: {name} {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
