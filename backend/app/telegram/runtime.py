"""Telegram runtime: polling, binding commands, repo jobs and delivery outbox.

Dependency injection is explicit: DatabaseRepository, WorkflowExecutor, the
repository importer callable and the Bot API. The runtime never reads the bot
token itself; TelegramBotAPI owns it and redacts every error.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set
from urllib.parse import urlsplit

from backend.app.codefacts.identity import workspace_id
from backend.app.codefacts.store import CodeFactsStore
from backend.app.contracts.schemas import WorkflowDraft
from backend.app.storage.database import DatabaseRepository
from backend.app.storage.telegram import (
    BRANCH_FILTER_LIMIT,
    DeliveryIntent,
    JOB_LEASE_SECONDS,
    MAX_JOB_RECOVERY_ATTEMPTS,
    PROJECT_FILTER_LIMIT,
    JobOwnershipLost,
    OutboxItem,
    SettingsOwnershipLost,
    TelegramRepository,
)
from backend.app.telegram.bot_api import (
    TelegramAPIError,
    TelegramBotAPI,
    TelegramConfigError,
    TelegramUnavailable,
    redact_telegram_text,
)
from backend.app.telegram.delivery_policy import (
    DeliveryDecision,
    DeliveryFacts,
    DeliverySettingsError,
    evaluate_delivery,
    format_hhmm,
    normalize_filter,
    normalize_severity,
    normalize_timezone,
    parse_quiet_window,
)
from backend.app.telegram.menus import (
    BOT_COMMANDS,
    CALLBACK_MAX_BYTES,
    MODE_LABELS,
    MODE_ORDER,
    SCENARIO_PAGE_SIZE,
    MenuCallback,
    MenuConfig,
    MenuContext,
    binding_markup,
    checks_markup,
    decode_callback,
    help_markup,
    load_menu_config,
    main_menu_markup,
    menu_button,
    parse_scenario_arg,
    scenario_fingerprint,
    scenario_page_count,
    settings_markup,
)
from backend.app.telegram.report import build_jsonc_report, write_jsonc_report
from backend.app.telegram.workspace import telegram_workspace_scope

logger = logging.getLogger("TelegramBot")

MAX_OUTBOX_ATTEMPTS = 5
OUTBOX_RETRY_BASE_SECONDS = 5
OUTBOX_RETRY_MAX_SECONDS = 300
JOB_STOP_GRACE_SECONDS = 15
JOB_RECOVERY_INTERVAL_SECONDS = 5
JOB_RECOVERY_BATCH = 20
MAX_ACTIVE_JOB_TASKS = 20
DELIVERY_TIMEOUT_SECONDS = 75
# A user-cancelled run is deliberate silence; every honest terminal outcome of
# a manual/scheduled/repository run still produces one delivery per binding.
DELIVERY_TRIGGER_STATUSES = ("succeeded", "failed", "interrupted")
# The completion marker is written first and retried briefly; anything the
# database still refuses goes to the in-memory list the sweep replays next.
DELIVERY_INTENT_RECORD_ATTEMPTS = 3
DELIVERY_INTENT_RECORD_DELAY_SECONDS = 1.0
DELIVERY_INTENT_SWEEP_LIMIT = 20
# Startup safety net: a completion whose marker write was refused lived only
# in memory; the first recovery pass re-adopts terminal runs without an intent
# inside this bounded window (and only while a transport is configured).
DELIVERY_BOOTSTRAP_WINDOW_HOURS = 24
DELIVERY_BOOTSTRAP_LIMIT = 50


class DeliveryFactsUnavailable(RuntimeError):
    """Filter metadata cannot be evaluated yet; retried, never a mismatch."""


HELP_TEXT = """NEDRA Telegram

/start — меню и привязка аккаунта
/status — статус и последние проверки
/settings — режим доставки и сценарий кнопками
/help — эта справка

Привязка: пришлите код из кабинета обычным сообщением — команда не нужна.
Режим и сценарий меняются кнопками в /settings.
Фильтры доставки: /settings severity|quiet|project|branch — текстом.
Пришлите HTTPS-ссылку GitHub/GitLab — бот импортирует репозиторий и запустит выбранный сценарий."""

BINDING_SUCCESS_TEXT = (
    "Аккаунт привязан.\n"
    "Меню ниже, настройки — следующим сообщением."
)
BINDING_CODE_HINT = (
    "Код не подошёл, истёк или уже использован. Код действует 10 минут; "
    "создайте новый в кабинете и пришлите его сюда следующим сообщением."
)
BARE_CODE_MISS_TEXT = (
    "Код не подошёл, истёк или уже использован. "
    "Создайте новый код в кабинете и пришлите его сюда следующим сообщением."
)
UNSUPPORTED_FILE_TEXT = (
    "Файлы, архивы (ZIP) и изображения пока не поддерживаются. "
    "Пришлите HTTPS-ссылку на репозиторий GitHub или GitLab — бот импортирует его сам."
)
STALE_CALLBACK_TEXT = "Кнопка устарела — откройте меню заново."
CALLBACK_FAILED_TEXT = "Не удалось выполнить действие. Откройте меню и попробуйте ещё раз."
BARE_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{6,128}$")
SEVERITY_USAGE = "/settings severity <info|low|medium|high|critical>"
QUIET_USAGE = (
    "/settings quiet <HH:MM-HH:MM> <IANA-timezone> — например "
    "quiet 22:00-07:00 Europe/Moscow; quiet off — выключить"
)
PROJECT_USAGE = "/settings project <подстрока URL/имени файла> или project off"
BRANCH_USAGE = "/settings branch <имя ветки> (можно main*) или branch off"


def _load_artifact_meta(workspace: str, artifact_id: str) -> Optional[Dict[str, Any]]:
    meta = CodeFactsStore().load_artifact_meta(workspace, artifact_id)
    return meta if isinstance(meta, dict) else None


class TelegramRuntime:
    def __init__(
        self,
        db: DatabaseRepository,
        executor: Any,
        importer: Callable[[str], Awaitable[Dict[str, Any]]],
        *,
        repository: Optional[TelegramRepository] = None,
        api: Optional[TelegramBotAPI] = None,
        poll_timeout: int = 25,
        outbox_interval: float = 2.0,
        config_error: Optional[str] = None,
        menu_config: Optional[MenuConfig] = None,
        menu_secret: Optional[str] = None,
        artifact_meta_loader: Optional[Callable[[str, str], Optional[Dict[str, Any]]]] = None,
    ):
        self.db = db
        self.executor = executor
        self.importer = importer
        self.repo = repository or TelegramRepository()
        self.api = api
        # Project/branch filters read artifact metadata (repository URL, branch)
        # through this explicit seam; production uses CodeFactsStore, tests inject.
        self._artifact_meta_loader = artifact_meta_loader or _load_artifact_meta
        self.poll_timeout = max(1, min(int(poll_timeout), 50))
        self.outbox_interval = max(0.5, min(float(outbox_interval), 30.0))
        self.menu_config = menu_config if menu_config is not None else load_menu_config()
        if menu_secret is not None:
            self._menu_secret = menu_secret
        elif api is not None:
            self._menu_secret = api.callback_secret
        else:
            self._menu_secret = ""
        self._menu_published = False
        self._menu_publish_error: Optional[str] = None

        self._stop = asyncio.Event()
        self._started = False
        self._poll_task: Optional[asyncio.Task] = None
        self._outbox_task: Optional[asyncio.Task] = None
        self._menu_task: Optional[asyncio.Task] = None
        self._job_tasks: Set[asyncio.Task] = set()
        self._active_job_ids: Set[str] = set()
        self._recovery_task: Optional[asyncio.Task] = None
        # run_id -> workspace for completions whose durable marker write was
        # refused by the database; the recovery sweep replays them in-process.
        # A restart loses this list — the first recovery pass re-adopts such
        # runs from the database (see _bootstrap_delivery_intents).
        self._pending_delivery_intents: Dict[str, str] = {}
        self._delivery_bootstrapped = False
        self._external_status = "not_started"
        self._last_error: Optional[str] = None
        self._config_error = config_error
        self._bot_username: Optional[str] = None

    # --- lifecycle -----------------------------------------------------
    async def start(self) -> Dict[str, Any]:
        if self._started:
            return self.status()
        self._started = True
        # Recovery must run even without a bot token: queued/importing jobs are
        # database work, not Telegram transport work.
        self._recovery_task = asyncio.create_task(
            self._recovery_loop(), name="telegram-job-recovery"
        )
        if self.api is None:
            self._external_status = "not_configured"
            self._last_error = self._config_error or "TELEGRAM_BOT_TOKEN не настроен"
            logger.warning("telegram runtime disabled: %s", self._last_error)
            return self.status()

        self._external_status = "connecting"
        try:
            await asyncio.wait_for(self.api.get_me(), timeout=20)
            self._bot_username = self.api.bot_username
            self._external_status = "ready"
            self._last_error = None
            logger.info("telegram runtime started as @%s", self._bot_username or "unknown")
        except asyncio.TimeoutError:
            self._external_status = "blocked"
            self._last_error = "telegram getMe timeout"
            logger.warning("telegram getMe unavailable: timeout")
        except TelegramUnavailable as exc:
            self._external_status = "blocked"
            self._last_error = self._safe_error(exc)
            logger.warning("telegram getMe unavailable: %s", self._last_error)

        if self._external_status == "ready":
            # Publishing commands/menu is global bot state, not a broadcast.
            # It runs in the background so a slow Bot API never delays polling.
            self._menu_task = asyncio.create_task(
                self._publish_menu_once(), name="telegram-menu",
            )

        self._poll_task = asyncio.create_task(self._poll_loop(), name="telegram-poll")
        self._outbox_task = asyncio.create_task(self._outbox_loop(), name="telegram-outbox")
        return self.status()

    async def stop(self) -> None:
        self._stop.set()
        self._started = False
        tasks = [
            task for task in (
                self._poll_task, self._outbox_task, self._recovery_task,
                self._menu_task,
            )
            if task is not None
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._job_tasks:
            done, pending = await asyncio.wait(
                list(self._job_tasks), timeout=JOB_STOP_GRACE_SECONDS
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        self._active_job_ids.clear()
        if self.api is not None:
            await self.api.close()
        self._external_status = "stopped"
        logger.info("telegram runtime stopped")

    def status(self) -> Dict[str, Any]:
        return {
            "configured": self.api is not None,
            "started": self._started,
            "externalStatus": self._external_status,
            "botUsername": self._bot_username,
            "proxyConfigured": bool(self.api is not None and self.api.proxy_configured),
            "proxyMode": self.api.proxy_mode if self.api is not None else None,
            "lastError": self._last_error or self._config_error,
            "miniAppConfigured": bool(self.menu_config.mini_app_url),
            "menuError": self.menu_config.error or self._menu_publish_error,
        }

    async def _publish_menu_once(self) -> None:
        """Best-effort global bot menu; failures never stop polling."""
        if self.api is None:
            return
        if self._menu_published:
            return
        self._menu_published = True
        if not self._menu_secret:
            self._menu_publish_error = "callback signing secret is unavailable"
            logger.warning("telegram menu skipped: %s", self._menu_publish_error)
            return
        try:
            await self.api.set_my_commands(BOT_COMMANDS)
        except TelegramUnavailable as exc:
            self._menu_publish_error = self._safe_error(exc)
            logger.warning("telegram setMyCommands failed: %s", self._menu_publish_error)
        except Exception as exc:  # noqa: BLE001 - menu is cosmetic, polling is not
            self._menu_publish_error = self._safe_error(exc)
            logger.warning("telegram setMyCommands error: %s", self._menu_publish_error)

        button = menu_button(self.menu_config)
        if button is None:
            if self.menu_config.error:
                self._menu_publish_error = self.menu_config.error
                logger.warning("telegram menu button skipped: %s", self.menu_config.error)
            return
        try:
            await self.api.set_chat_menu_button(button)
        except TelegramUnavailable as exc:
            self._menu_publish_error = self._safe_error(exc)
            logger.warning("telegram setChatMenuButton failed: %s", self._menu_publish_error)
        except Exception as exc:  # noqa: BLE001
            self._menu_publish_error = self._safe_error(exc)
            logger.warning("telegram setChatMenuButton error: %s", self._menu_publish_error)

    async def _repo(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """Run a sync storage call off the event loop.

        Binding row locks are held by delivery/rebind transactions; any sync
        psycopg call waiting on them must never block polling, heartbeat, or
        another HTTP request.
        """
        return await asyncio.to_thread(getattr(self.repo, name), *args, **kwargs)

    async def _db(self, name: str, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(getattr(self.db, name), *args, **kwargs)

    # --- polling / outbox ---------------------------------------------
    async def _poll_loop(self) -> None:
        if self.api is None:
            return
        key = self.api.bot_key
        try:
            offset = await self._repo("get_cursor", key, 0)
        except Exception as exc:  # noqa: BLE001 - DB hiccup must not kill polling forever
            logger.warning("telegram cursor read failed: %s", self._safe_error(exc))
            offset = 0
        backoff = 1
        while not self._stop.is_set():
            try:
                updates = await self.api.get_updates(offset, timeout=self.poll_timeout)
            except asyncio.CancelledError:
                raise
            except TelegramUnavailable as exc:
                self._external_status = "blocked"
                self._last_error = self._safe_error(exc)
                logger.warning("telegram polling unavailable: %s", self._last_error)
                await self._wait(backoff)
                backoff = min(backoff * 2, 30)
                continue
            except Exception as exc:  # noqa: BLE001 - polling must not kill process
                self._external_status = "blocked"
                self._last_error = self._safe_error(exc)
                logger.warning("telegram polling error: %s", self._last_error)
                await self._wait(backoff)
                backoff = min(backoff * 2, 30)
                continue

            backoff = 1
            self._external_status = "ready"
            self._last_error = None
            for update in updates:
                try:
                    await self.handle_update(update)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.warning("telegram update failed: %s", self._safe_error(exc))
                update_id = update.get("update_id")
                if isinstance(update_id, int) and not isinstance(update_id, bool):
                    offset = max(offset, update_id + 1)
            if updates:
                try:
                    await self._repo("set_cursor", key, offset)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("telegram cursor write failed: %s", self._safe_error(exc))

    async def _outbox_loop(self) -> None:
        while not self._stop.is_set():
            try:
                delivered = await self.process_outbox_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("telegram outbox error: %s", self._safe_error(exc))
                delivered = 0
            if delivered:
                continue
            await self._wait(self.outbox_interval)

    async def _recovery_loop(self) -> None:
        while not self._stop.is_set():
            if not self._delivery_bootstrapped:
                try:
                    # Durable safety net for a completion whose marker write
                    # was refused before a restart (runs live only in
                    # runtime.runs then, without an intent row).
                    await self._bootstrap_delivery_intents()
                    self._delivery_bootstrapped = True
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - retried next cycle
                    logger.warning(
                        "telegram delivery bootstrap failed: %s", self._safe_error(exc)
                    )
            try:
                await self.recover_jobs_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - recovery must not kill runtime
                logger.warning("telegram job recovery failed: %s", self._safe_error(exc))
            try:
                # Replay delivery intents whose outbox rows are still missing
                # (transient DB failure at completion, crash between marker
                # and enqueue).
                await self._sweep_delivery_intents()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - sweep is retried next cycle
                logger.warning("telegram delivery sweep failed: %s", self._safe_error(exc))
            await self._wait(JOB_RECOVERY_INTERVAL_SECONDS)

    async def _bootstrap_delivery_intents(self) -> int:
        """Adopt recent terminal runs that have no delivery marker yet.

        Called once per process (retried on failure) while a transport is
        configured. The window bounds both the replay: a restart re-adopts a
        completion whose marker write was refused, while runs finished long
        before delivery existed are never picked up.
        """
        if self.api is None:
            return 0
        candidates = await self._repo(
            "recent_runs_without_intent",
            DELIVERY_BOOTSTRAP_WINDOW_HOURS, DELIVERY_BOOTSTRAP_LIMIT,
        )
        adopted = 0
        for intent in candidates:
            await self._repo(
                "record_delivery_intent", intent.run_id, intent.workspace_id
            )
            if await self._process_delivery_intent(intent.run_id, intent.workspace_id):
                adopted += 1
        if adopted:
            logger.info("telegram delivery adopted %d run(s) without marker", adopted)
        return adopted

    async def recover_jobs_once(self) -> int:
        """Bounded pass: settle orphaned runs and spawn claimable jobs."""
        capacity = MAX_ACTIVE_JOB_TASKS - len(self._active_job_ids)
        if capacity <= 0:
            return 0
        ids = await asyncio.to_thread(
            self.repo.recover_jobs, min(JOB_RECOVERY_BATCH, capacity), MAX_JOB_RECOVERY_ATTEMPTS
        )
        for job_id in ids:
            self._spawn_job(job_id)
        return len(ids)

    async def _wait(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=max(0.1, seconds))
        except asyncio.TimeoutError:
            pass

    # --- updates -------------------------------------------------------
    async def handle_update(self, update: Dict[str, Any]) -> None:
        message = update.get("message")
        if isinstance(message, dict):
            await self._handle_message_update(update, message)
            return
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            await self._handle_callback_update(update, callback)

    async def _handle_message_update(
        self, update: Dict[str, Any], message: Dict[str, Any],
    ) -> None:
        chat = message.get("chat")
        sender = message.get("from")
        if not isinstance(chat, dict) or not isinstance(sender, dict):
            return
        if chat.get("type") != "private" or sender.get("is_bot") is True:
            return
        chat_id = chat.get("id")
        sender_id = sender.get("id")
        update_id = update.get("update_id")
        if not isinstance(chat_id, int) or isinstance(chat_id, bool):
            return
        if not isinstance(sender_id, int) or isinstance(sender_id, bool):
            return
        if not isinstance(update_id, int) or isinstance(update_id, bool):
            return
        # Telegram's private chat is one human; anything else is not our menu.
        if sender_id != chat_id:
            return
        text = message.get("text")
        if not isinstance(text, str):
            text = ""
        username = sender.get("username") if isinstance(sender.get("username"), str) else None

        if not await self._repo("claim_update", update_id, chat_id):
            return
        attachment = any(
            message.get(kind)
            for kind in ("document", "photo", "audio", "video", "voice",
                         "video_note", "animation", "sticker")
        )
        try:
            await self._dispatch_text(
                chat_id, sender_id, username, text, update_id,
                has_attachment=attachment,
            )
            await self._repo("finish_update", update_id, "done")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            safe = self._safe_error(exc)
            try:
                await self._repo("finish_update", update_id, "failed", safe)
            except Exception:
                logger.warning("could not record failed telegram update")
            logger.warning("telegram update %s failed: %s", update_id, safe)

    async def _handle_callback_update(
        self, update: Dict[str, Any], callback: Dict[str, Any],
    ) -> None:
        """Handle an inline button press from the sender's own private chat.

        Group chats, bots and foreign senders are dropped before the update is
        claimed, so a copied message can never rewrite another chat's settings.
        """
        query_id = callback.get("id")
        sender = callback.get("from")
        message = callback.get("message")
        if not isinstance(query_id, str) or not query_id or len(query_id) > 128:
            return
        if not isinstance(sender, dict) or sender.get("is_bot") is True:
            return
        if not isinstance(message, dict):
            return
        chat = message.get("chat")
        if not isinstance(chat, dict):
            return
        if chat.get("type") != "private":
            return
        chat_id = chat.get("id")
        sender_id = sender.get("id")
        message_id = message.get("message_id")
        update_id = update.get("update_id")
        for value in (chat_id, sender_id, message_id, update_id):
            if not isinstance(value, int) or isinstance(value, bool):
                return
        if sender_id != chat_id:
            return
        if not await self._repo("claim_update", update_id, chat_id):
            return

        ack_sent = False

        async def ack(text: Optional[str] = None, *, alert: bool = False) -> None:
            nonlocal ack_sent
            if ack_sent:
                return
            ack_sent = True
            if self.api is None:
                return
            try:
                await self.api.answer_callback_query(
                    query_id, text=text, show_alert=alert,
                )
            except Exception as exc:  # noqa: BLE001 - spinner must not block work
                logger.info("telegram callback ack failed: %s", self._safe_error(exc))

        try:
            data = callback.get("data")
            encoded = b""
            if isinstance(data, str):
                try:
                    encoded = data.encode("utf-8")
                except UnicodeEncodeError:
                    encoded = b""
            if not self._menu_secret:
                await ack(STALE_CALLBACK_TEXT, alert=True)
                await self._refresh_callback_view(chat_id, message_id, sender_id)
                await self._repo("finish_update", update_id, "done")
                return
            if not encoded or len(encoded) > CALLBACK_MAX_BYTES:
                await ack(STALE_CALLBACK_TEXT, alert=True)
                await self._refresh_callback_view(chat_id, message_id, sender_id)
                await self._repo("finish_update", update_id, "done")
                return
            parsed = decode_callback(
                data, self._menu_secret, user_id=sender_id, chat_id=chat_id,
            )
            if parsed is None:
                await ack(STALE_CALLBACK_TEXT, alert=True)
                await self._refresh_callback_view(chat_id, message_id, sender_id)
                await self._repo("finish_update", update_id, "done")
                return
            # Answer before DB work so Telegram never leaves a spinning button.
            await ack()
            try:
                await self._dispatch_callback(
                    chat_id, sender_id, message_id, parsed,
                )
                await self._repo("finish_update", update_id, "done")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - one stale button is not fatal
                safe = self._safe_error(exc)
                logger.warning("telegram callback %s failed: %s", parsed.action, safe)
                try:
                    await self._repo("finish_update", update_id, "failed", safe)
                except Exception:
                    logger.warning("could not record failed telegram callback")
                await self._best_effort_message(chat_id, CALLBACK_FAILED_TEXT)
        finally:
            # An exception before ack() must not leave the client spinner alive.
            await ack()

    async def _dispatch_text(
        self,
        chat_id: int,
        sender_id: int,
        username: Optional[str],
        text: str,
        update_id: int,
        *,
        has_attachment: bool = False,
    ) -> None:
        value = text.strip()
        if not value:
            if has_attachment:
                await self._reply(chat_id, UNSUPPORTED_FILE_TEXT)
            else:
                await self._send_help(chat_id, sender_id)
            return
        parts = value.split(maxsplit=1)
        command = parts[0].split("@", 1)[0].lower()
        argument = parts[1].strip() if len(parts) > 1 else ""

        if command == "/start":
            await self._handle_start(chat_id, sender_id, username, argument)
            return
        if command == "/help":
            await self._send_help(chat_id, sender_id)
            return
        if command == "/settings":
            await self._handle_settings(chat_id, sender_id, argument)
            return
        if command == "/status":
            await self._handle_status(chat_id, sender_id)
            return
        if self._looks_like_repo_url(value):
            await self._start_repo_job(chat_id, value, update_id)
            return
        if BARE_CODE_RE.fullmatch(value):
            await self._handle_bare_code(chat_id, sender_id, username, value)
            return
        await self._send_help(chat_id, sender_id)

    # --- menu rendering ------------------------------------------------
    def _menu_ctx(
        self, chat_id: int, sender_id: int, epoch: int,
    ) -> Optional[MenuContext]:
        if not self._menu_secret:
            return None
        return MenuContext(
            secret=self._menu_secret,
            user_id=int(sender_id),
            chat_id=int(chat_id),
            epoch=int(epoch),
            config=self.menu_config,
        )

    def _binding_text(self) -> str:
        cabinet = self.menu_config.cabinet_url
        return (
            "Привет! Я NEDRA — проверяю репозитории на уязвимости.\n\n"
            "Откройте кабинет, создайте код привязки и пришлите его сюда "
            "следующим сообщением — без команд.\n"
            f"Кабинет: {cabinet}\n\n"
            "Код действует 10 минут и срабатывает один раз. "
            "Если код не подошёл — создайте новый в кабинете."
        )

    @staticmethod
    def _scenario_label(scenarios: List[Dict[str, str]], scenario_id: str) -> str:
        if not scenario_id:
            return "не выбран"
        for item in scenarios:
            if item.get("id") == scenario_id:
                return item.get("name") or scenario_id
        return f"{scenario_id} (недоступен)"

    async def _binding_settings(self, binding: Any):
        return await self._repo(
            "get_settings", binding.chat_id, user_id=binding.user_id,
            workspace_id=binding.workspace_id, binding_epoch=binding.binding_epoch,
        )

    async def _current_view(self, binding: Any, text: str, markup: Any) -> tuple:
        fresh = await self._repo("get_binding", binding.chat_id)
        if fresh is None or (
            fresh.user_id != binding.user_id
            or fresh.workspace_id != binding.workspace_id
            or fresh.binding_epoch != binding.binding_epoch
        ):
            ctx = self._menu_ctx(binding.chat_id, binding.chat_id, 0)
            return self._binding_text(), binding_markup(ctx) if ctx else None
        return text, markup

    async def _main_view(
        self, binding: Any, sender_id: int, notice: Optional[str] = None,
    ) -> tuple:
        settings = await self._binding_settings(binding)
        scenarios = await self._scenarios(binding.workspace_id)
        lines = []
        if notice:
            lines.extend([notice, ""])
        lines.extend([
            "NEDRA на связи.",
            f"Режим: {MODE_LABELS.get(settings.delivery_mode, settings.delivery_mode)}"
            f" · Сценарий: {self._scenario_label(scenarios, settings.scenario)}",
            "",
            "Пришлите HTTPS-ссылку GitHub/GitLab — импортирую репозиторий "
            "и запущу выбранный сценарий.",
        ])
        ctx = self._menu_ctx(binding.chat_id, sender_id, binding.binding_epoch)
        return await self._current_view(
            binding, "\n".join(lines), main_menu_markup(ctx) if ctx else None,
        )

    async def _settings_view(
        self,
        binding: Any,
        sender_id: int,
        *,
        page: int = 0,
        notice: Optional[str] = None,
    ) -> tuple:
        settings = await self._binding_settings(binding)
        scenarios = await self._scenarios(binding.workspace_id)
        pages = scenario_page_count(len(scenarios), SCENARIO_PAGE_SIZE)
        safe_page = max(0, min(int(page), pages - 1))
        label = self._scenario_label(scenarios, settings.scenario)
        lines = ["Настройки NEDRA"]
        if notice:
            lines.extend(["", notice])
        lines.extend([
            "",
            f"Доставка: {MODE_LABELS.get(settings.delivery_mode, settings.delivery_mode)}",
            f"Сценарий: {label}",
            f"Порог: {settings.min_severity}",
            f"Тихие часы: {self._quiet_label(settings)}",
            f"Проект: {settings.project_filter or '—'}",
            f"Ветка: {settings.branch_filter or '—'}",
            "",
            "Режим — кнопками «Кратко», «JSON», «Оба». Сценарий — из списка ниже.",
            "Фильтры: /settings severity, quiet, project, branch.",
        ])
        if scenarios:
            lines.append(f"Сценарии: {len(scenarios)} · стр. {safe_page + 1}/{pages}")
        else:
            lines.append("В вашем workspace пока нет сценариев.")
        ctx = self._menu_ctx(binding.chat_id, sender_id, binding.binding_epoch)
        markup = settings_markup(
            ctx,
            delivery_mode=settings.delivery_mode,
            scenarios=scenarios,
            page=safe_page,
        ) if ctx else None
        return await self._current_view(binding, "\n".join(lines), markup)

    @staticmethod
    def _quiet_label(settings: Any) -> str:
        if settings.quiet_start is None or settings.quiet_end is None:
            return "—"
        return (
            f"{format_hhmm(settings.quiet_start)}–{format_hhmm(settings.quiet_end)} "
            f"{settings.quiet_timezone}"
        )

    async def _status_view(self, binding: Any, sender_id: int) -> tuple:
        settings = await self._binding_settings(binding)
        scenarios = await self._scenarios(binding.workspace_id)
        runs = await self._recent_runs(binding.workspace_id)
        jobs = await self._repo("list_jobs",
            binding.chat_id, binding.user_id, binding.workspace_id,
            binding.binding_epoch, limit=3,
        )
        lines = [
            "Проверки NEDRA",
            "Привязка: активна",
            f"Доставка: {MODE_LABELS.get(settings.delivery_mode, settings.delivery_mode)}",
            f"Сценарий: {self._scenario_label(scenarios, settings.scenario)}",
            f"Telegram: {self._external_status}",
        ]
        if jobs:
            lines.append("Последние задачи:")
            for job in jobs:
                suffix = f" run={job.run_id}" if job.run_id else ""
                lines.append(f"  {job.status}{suffix}")
        if runs:
            lines.append("Последние запуски:")
            for run in runs:
                lines.append(
                    f"  {run.get('id')} — {run.get('execution_status')} / "
                    f"{run.get('security_verdict')} / findings={run.get('total_findings', 0)}"
                )
        else:
            lines.append("Запусков пока нет. Пришлите ссылку на репозиторий.")
        ctx = self._menu_ctx(binding.chat_id, sender_id, binding.binding_epoch)
        return await self._current_view(
            binding, "\n".join(lines), checks_markup(ctx) if ctx else None,
        )

    async def _edit_or_send(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        markup: Optional[Dict[str, Any]],
    ) -> None:
        if self.api is None:
            raise TelegramUnavailable("telegram is not configured")
        try:
            await self.api.edit_message_text(
                chat_id, message_id, text, reply_markup=markup,
            )
        except TelegramAPIError as exc:
            if "message is not modified" in (exc.description or "").lower():
                return
            await self._reply(chat_id, text, reply_markup=markup)

    async def _render_main(
        self,
        chat_id: int,
        sender_id: int,
        message_id: Optional[int],
        binding: Any,
        *,
        notice: Optional[str] = None,
    ) -> None:
        text, markup = await self._main_view(binding, sender_id, notice)
        if message_id is None:
            await self._reply(chat_id, text, reply_markup=markup)
        else:
            await self._edit_or_send(chat_id, message_id, text, markup)

    async def _render_binding(
        self,
        chat_id: int,
        sender_id: int,
        message_id: Optional[int],
        *,
        notice: Optional[str] = None,
    ) -> None:
        ctx = self._menu_ctx(chat_id, sender_id, 0)
        text = self._binding_text()
        if notice:
            text = f"{notice}\n\n{text}"
        markup = binding_markup(ctx) if ctx else None
        if message_id is None:
            await self._reply(chat_id, text, reply_markup=markup)
        else:
            await self._edit_or_send(chat_id, message_id, text, markup)

    async def _render_help(
        self, chat_id: int, sender_id: int, message_id: Optional[int],
        binding: Optional[Any],
    ) -> None:
        if binding is None:
            text = self._binding_text() + "\n\n" + HELP_TEXT
            ctx = self._menu_ctx(chat_id, sender_id, 0)
        else:
            text = HELP_TEXT
            ctx = self._menu_ctx(chat_id, sender_id, binding.binding_epoch)
        markup = help_markup(ctx) if ctx else None
        if message_id is None:
            await self._reply(chat_id, text, reply_markup=markup)
        else:
            await self._edit_or_send(chat_id, message_id, text, markup)

    async def _render_settings(
        self,
        chat_id: int,
        sender_id: int,
        message_id: int,
        binding: Any,
        *,
        page: int = 0,
        notice: Optional[str] = None,
    ) -> None:
        text, markup = await self._settings_view(
            binding, sender_id, page=page, notice=notice,
        )
        await self._edit_or_send(chat_id, message_id, text, markup)

    async def _send_settings(
        self,
        chat_id: int,
        sender_id: int,
        binding: Any,
        *,
        notice: Optional[str] = None,
    ) -> None:
        text, markup = await self._settings_view(
            binding, sender_id, notice=notice,
        )
        await self._reply(chat_id, text, reply_markup=markup)

    async def _after_bind(
        self, chat_id: int, sender_id: int, binding: Any,
    ) -> None:
        """Binding is done: show the menu and then the shared settings."""
        await self._render_main(
            chat_id, sender_id, None, binding, notice=BINDING_SUCCESS_TEXT,
        )
        await self._send_settings(
            chat_id, sender_id, binding,
            notice="Выберите сценарий кнопкой — печатать id не нужно.",
        )

    async def _render_checks(
        self, chat_id: int, sender_id: int, message_id: int, binding: Any,
    ) -> None:
        text, markup = await self._status_view(binding, sender_id)
        await self._edit_or_send(chat_id, message_id, text, markup)

    async def _refresh_callback_view(
        self, chat_id: int, message_id: int, sender_id: int,
    ) -> None:
        binding = await self._repo("get_binding", chat_id)
        if binding is None:
            await self._render_binding(
                chat_id, sender_id, message_id, notice=STALE_CALLBACK_TEXT,
            )
            return
        await self._render_main(
            chat_id, sender_id, message_id, binding, notice=STALE_CALLBACK_TEXT,
        )

    async def _dispatch_callback(
        self,
        chat_id: int,
        sender_id: int,
        message_id: int,
        callback: MenuCallback,
    ) -> None:
        binding = await self._repo("get_binding", chat_id)
        if callback.action == "help":
            await self._render_help(chat_id, sender_id, message_id, binding)
            return
        if binding is None:
            await self._render_binding(chat_id, sender_id, message_id)
            return
        if int(binding.binding_epoch) != int(callback.epoch):
            await self._render_main(
                chat_id, sender_id, message_id, binding,
                notice="Меню обновилось после перепривязки.",
            )
            return

        if callback.action in {"menu", "back"}:
            await self._render_main(chat_id, sender_id, message_id, binding)
            return
        if callback.action in {"checks", "refresh"}:
            await self._render_checks(chat_id, sender_id, message_id, binding)
            return
        if callback.action == "settings":
            await self._render_settings(chat_id, sender_id, message_id, binding)
            return
        if callback.action == "page":
            if not callback.arg.isdigit():
                await self._render_settings(
                    chat_id, sender_id, message_id, binding,
                    notice="Страница устарела — вот актуальные настройки.",
                )
                return
            await self._render_settings(
                chat_id, sender_id, message_id, binding, page=int(callback.arg),
            )
            return
        if callback.action == "mode":
            await self._apply_mode_callback(
                chat_id, sender_id, message_id, binding, callback.arg,
            )
            return
        if callback.action == "scen":
            await self._apply_scenario_callback(
                chat_id, sender_id, message_id, binding, callback.arg,
            )
            return
        await self._refresh_callback_view(chat_id, message_id, sender_id)

    async def _apply_mode_callback(
        self,
        chat_id: int,
        sender_id: int,
        message_id: int,
        binding: Any,
        arg: str,
    ) -> None:
        if not arg.isdigit() or int(arg) >= len(MODE_ORDER):
            await self._render_settings(
                chat_id, sender_id, message_id, binding,
                notice="Кнопка устарела — вот актуальные настройки.",
            )
            return
        mode = MODE_ORDER[int(arg)]
        try:
            await self._repo("set_settings",
                chat_id, user_id=binding.user_id,
                workspace_id=binding.workspace_id,
                binding_epoch=binding.binding_epoch,
                delivery_mode=mode,
            )
        except SettingsOwnershipLost:
            logger.info("telegram settings write ignored after rebind")
            await self._refresh_callback_view(chat_id, message_id, sender_id)
            return
        await self._render_settings(
            chat_id, sender_id, message_id, binding,
            notice=f"Доставка: {MODE_LABELS[mode]}.",
        )

    async def _apply_scenario_callback(
        self,
        chat_id: int,
        sender_id: int,
        message_id: int,
        binding: Any,
        arg: str,
    ) -> None:
        parsed = parse_scenario_arg(arg)
        scenarios = await self._scenarios(binding.workspace_id) if parsed else []
        if parsed is None:
            await self._render_settings(
                chat_id, sender_id, message_id, binding,
                notice="Кнопка устарела — вот актуальные настройки.",
            )
            return
        index, fingerprint = parsed
        if index >= len(scenarios):
            await self._render_settings(
                chat_id, sender_id, message_id, binding,
                notice="Список сценариев изменился — выберите заново.",
            )
            return
        scenario_id = str(scenarios[index].get("id") or "")
        if not scenario_id or scenario_fingerprint(scenario_id) != fingerprint:
            await self._render_settings(
                chat_id, sender_id, message_id, binding,
                notice="Список сценариев изменился — выберите заново.",
            )
            return
        # Ownership is rechecked against this binding's workspace, never the
        # workspace or scenario text supplied by the client.
        workflow = await self._workflow(binding.workspace_id, scenario_id)
        if workflow is None:
            await self._render_settings(
                chat_id, sender_id, message_id, binding,
                notice="Такой сценарий не найден в вашем workspace.",
            )
            return
        try:
            await self._repo("set_settings",
                chat_id, user_id=binding.user_id,
                workspace_id=binding.workspace_id,
                binding_epoch=binding.binding_epoch,
                scenario=scenario_id,
            )
        except SettingsOwnershipLost:
            logger.info("telegram scenario write ignored after rebind")
            await self._refresh_callback_view(chat_id, message_id, sender_id)
            return
        await self._render_settings(
            chat_id, sender_id, message_id, binding,
            notice=f"Сценарий выбран: {self._scenario_label(scenarios, scenario_id)}.",
        )

    async def _send_help(self, chat_id: int, sender_id: int) -> None:
        binding = await self._repo("get_binding", chat_id)
        await self._render_help(chat_id, sender_id, None, binding)

    async def _handle_bare_code(
        self,
        chat_id: int,
        sender_id: int,
        username: Optional[str],
        code: str,
    ) -> None:
        """A binding code sent as plain text is consumed instead of ignored."""
        binding = await self._repo("get_binding", chat_id)
        if binding is not None:
            await self._render_main(
                chat_id, sender_id, None, binding,
                notice="Аккаунт уже привязан.",
            )
            return
        bound = await self._repo("consume_binding_code", code, chat_id, username)
        if bound is None:
            await self._render_binding(
                chat_id, sender_id, None, notice=BARE_CODE_MISS_TEXT,
            )
            return
        await self._after_bind(chat_id, sender_id, bound)

    async def _handle_start(
        self,
        chat_id: int,
        sender_id: int,
        username: Optional[str],
        argument: str,
    ) -> None:
        if argument:
            bound = await self._repo("consume_binding_code", argument, chat_id, username)
            if bound is None:
                await self._render_binding(
                    chat_id, sender_id, None, notice=BINDING_CODE_HINT,
                )
                return
            await self._after_bind(chat_id, sender_id, bound)
            return
        binding = await self._repo("get_binding", chat_id)
        if binding is None:
            await self._render_binding(chat_id, sender_id, None)
            return
        await self._render_main(chat_id, sender_id, None, binding)

    async def _handle_settings(
        self, chat_id: int, sender_id: int, argument: str,
    ) -> None:
        binding = await self._repo("get_binding", chat_id)
        if binding is None:
            await self._render_binding(chat_id, sender_id, None)
            return
        if not argument:
            text, markup = await self._settings_view(binding, sender_id)
            await self._reply(chat_id, text, reply_markup=markup)
            return

        mode = argument.strip().lower()
        if mode in {"alerts", "jsonc", "both"}:
            try:
                await self._repo("set_settings",
                    chat_id, user_id=binding.user_id,
                    workspace_id=binding.workspace_id,
                    binding_epoch=binding.binding_epoch,
                    delivery_mode=mode,
                )
            except SettingsOwnershipLost:
                logger.info("telegram settings write ignored after rebind")
                await self._render_binding(
                    chat_id, sender_id, None,
                    notice="Привязка обновилась — настройки не изменены.",
                )
                return
            text, markup = await self._settings_view(
                binding, sender_id, notice=f"Доставка: {MODE_LABELS[mode]}.",
            )
            await self._reply(chat_id, text, reply_markup=markup)
            return
        if mode.startswith("scenario"):
            scenario = argument[len("scenario"):].strip()
            if not scenario:
                await self._reply(chat_id, "Использование: /settings scenario <workflow_id>")
                return
            workflow = await self._workflow(binding.workspace_id, scenario)
            if workflow is None:
                await self._reply(chat_id, "Такой сценарий не найден в вашем workspace.")
                return
            try:
                await self._repo("set_settings",
                    chat_id, user_id=binding.user_id,
                    workspace_id=binding.workspace_id,
                    binding_epoch=binding.binding_epoch,
                    scenario=scenario,
                )
            except SettingsOwnershipLost:
                logger.info("telegram scenario write ignored after rebind")
                await self._render_binding(
                    chat_id, sender_id, None,
                    notice="Привязка обновилась — настройки не изменены.",
                )
                return
            scenarios = await self._scenarios(binding.workspace_id)
            text, markup = await self._settings_view(
                binding, sender_id,
                notice=f"Сценарий выбран: {self._scenario_label(scenarios, scenario)}.",
            )
            await self._reply(chat_id, text, reply_markup=markup)
            return
        if await self._apply_delivery_filter(chat_id, sender_id, binding, argument):
            return
        text, markup = await self._settings_view(
            binding, sender_id,
            notice=(
                "Неизвестная команда. Выберите режим или сценарий кнопками, "
                "фильтры — /settings severity, quiet, project, branch."
            ),
        )
        await self._reply(chat_id, text, reply_markup=markup)

    async def _apply_delivery_filter(
        self, chat_id: int, sender_id: int, binding: Any, argument: str,
    ) -> bool:
        """Handle ``/settings severity|quiet|project|branch ...`` text filters."""
        head, _, value = argument.strip().partition(" ")
        name = head.strip().lower()
        value = value.strip()
        if name == "severity":
            usage = SEVERITY_USAGE
        elif name == "quiet":
            usage = QUIET_USAGE
        elif name == "project":
            usage = PROJECT_USAGE
        elif name == "branch":
            usage = BRANCH_USAGE
        else:
            return False

        kwargs: Dict[str, Any] = {}
        if name == "severity":
            try:
                kwargs = {"min_severity": normalize_severity(value)}
            except DeliverySettingsError as exc:
                await self._reply(chat_id, f"{exc}\n{usage}")
                return True
            notice = f"Порог доставки: {kwargs['min_severity']}."
        elif name == "quiet":
            if value.lower() in {"off", "clear", "выкл"}:
                kwargs = {"clear_quiet_hours": True}
                notice = "Тихие часы выключены."
            else:
                tokens = value.split()
                if not tokens or len(tokens) > 2:
                    await self._reply(chat_id, usage)
                    return True
                try:
                    start, end = parse_quiet_window(tokens[0])
                    tz = normalize_timezone(tokens[1]) if len(tokens) == 2 else "UTC"
                except DeliverySettingsError as exc:
                    await self._reply(chat_id, f"{exc}\n{usage}")
                    return True
                kwargs = {"quiet_start": start, "quiet_end": end, "quiet_timezone": tz}
                notice = f"Тихие часы: {format_hhmm(start)}–{format_hhmm(end)} {tz}."
        elif name == "project":
            if value.lower() in {"off", "clear"}:
                kwargs = {"project_filter": ""}
                notice = "Фильтр проекта выключен."
            else:
                try:
                    cleaned = normalize_filter(
                        value, field="project", limit=PROJECT_FILTER_LIMIT,
                    )
                except DeliverySettingsError as exc:
                    await self._reply(chat_id, f"{exc}\n{usage}")
                    return True
                if not cleaned:
                    await self._reply(chat_id, usage)
                    return True
                kwargs = {"project_filter": cleaned}
                notice = f"Фильтр проекта: {cleaned}."
        else:  # branch
            if value.lower() in {"off", "clear"}:
                kwargs = {"branch_filter": ""}
                notice = "Фильтр ветки выключен."
            else:
                try:
                    cleaned = normalize_filter(
                        value, field="branch", limit=BRANCH_FILTER_LIMIT,
                    )
                except DeliverySettingsError as exc:
                    await self._reply(chat_id, f"{exc}\n{usage}")
                    return True
                if not cleaned:
                    await self._reply(chat_id, usage)
                    return True
                kwargs = {"branch_filter": cleaned}
                notice = f"Фильтр ветки: {cleaned}."

        try:
            await self._repo(
                "set_settings", chat_id,
                user_id=binding.user_id,
                workspace_id=binding.workspace_id,
                binding_epoch=binding.binding_epoch,
                **kwargs,
            )
        except SettingsOwnershipLost:
            logger.info("telegram filter write ignored after rebind")
            await self._render_binding(
                chat_id, sender_id, None,
                notice="Привязка обновилась — настройки не изменены.",
            )
            return True
        except ValueError as exc:
            await self._reply(chat_id, f"{exc}\n{usage}")
            return True
        text, markup = await self._settings_view(binding, sender_id, notice=notice)
        await self._reply(chat_id, text, reply_markup=markup)
        return True

    async def _handle_status(self, chat_id: int, sender_id: int) -> None:
        binding = await self._repo("get_binding", chat_id)
        if binding is None:
            await self._render_binding(chat_id, sender_id, None)
            return
        text, markup = await self._status_view(binding, sender_id)
        await self._reply(chat_id, text, reply_markup=markup)

    # --- repo job ------------------------------------------------------
    async def _start_repo_job(
        self,
        chat_id: int,
        repo_url: str,
        update_id: int,
    ) -> None:
        binding = await self._repo("get_binding", chat_id)
        if binding is None:
            await self._render_binding(chat_id, chat_id, None)
            return
        settings = await self._binding_settings(binding)
        if not settings.scenario:
            text, markup = await self._settings_view(
                binding, chat_id,
                notice="Сначала выберите сценарий — без него проверку не запустить.",
            )
            await self._reply(chat_id, text, reply_markup=markup)
            return
        workflow = await self._workflow(binding.workspace_id, settings.scenario)
        if workflow is None:
            await self._reply(chat_id, "Выбранный сценарий больше не существует в вашем workspace.")
            return
        try:
            job = await self._repo("create_job",
                source_update_id=update_id,
                chat_id=chat_id,
                user_id=binding.user_id,
                workspace_id=binding.workspace_id,
                repo_url=repo_url,
                workflow_id=settings.scenario,
                binding_epoch=binding.binding_epoch,
            )
        except JobOwnershipLost:
            logger.info("telegram job creation ignored after rebind")
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("telegram job creation failed: %s", self._safe_error(exc))
            await self._reply(chat_id, "Не удалось создать задачу импорта.")
            return
        if job.status != "queued":
            await self._reply(chat_id, f"Эта ссылка уже обрабатывается или обработана: {job.status}.")
            return
        self._spawn_job(job.job_id)
        await self._reply(chat_id, f"Задача принята: {job.job_id}\nИмпорт и запуск сценария идут в фоне.")

    def _spawn_job(self, job_id: str) -> None:
        if job_id in self._active_job_ids or len(self._active_job_ids) >= MAX_ACTIVE_JOB_TASKS:
            return
        self._active_job_ids.add(job_id)
        task = asyncio.create_task(self._run_job(job_id), name=f"telegram-job-{job_id}")
        self._job_tasks.add(task)

        def _done(completed: asyncio.Task) -> None:
            self._job_tasks.discard(completed)
            self._active_job_ids.discard(job_id)
            if completed.cancelled():
                return
            exc = completed.exception()
            if exc is not None:
                logger.warning("telegram job task failed: %s", self._safe_error(exc))

        task.add_done_callback(_done)

    async def _heartbeat_job(self, job: Any, owner: asyncio.Task) -> None:
        interval = max(5.0, JOB_LEASE_SECONDS / 3)
        while True:
            await asyncio.sleep(interval)
            try:
                alive = await asyncio.to_thread(
                    self.repo.heartbeat_job, job, JOB_LEASE_SECONDS
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a DB hiccup is not auth loss
                logger.warning("telegram job heartbeat failed: %s", self._safe_error(exc))
                owner.cancel()
                return
            if not alive:
                owner.cancel()
                return

    async def _run_job(self, job_id: str) -> None:
        job = await self._repo("claim_job",
            job_id, JOB_LEASE_SECONDS, MAX_JOB_RECOVERY_ATTEMPTS
        )
        if job is None:
            return
        heartbeat = asyncio.create_task(
            self._heartbeat_job(job, asyncio.current_task()), name=f"telegram-heartbeat-{job_id}"
        )
        try:
            if not await self._repo("job_lease_valid", job):
                return
            with telegram_workspace_scope(job.workspace_id):
                manifest = await self.importer(job.repo_url)
                # A stale/restarted worker must not create a second run.
                if not await self._repo("job_lease_valid", job):
                    return
                await self._db("save_artifact", manifest)
                workflow = await self._db("get_workflow", job.workflow_id)
                if not workflow or not workflow.get("draft"):
                    raise ValueError("workflow недоступен")
                revision_id = workflow.get("latest_revision_id")
                if not revision_id:
                    raise ValueError("workflow revision недоступна")
                if not await self._repo("job_lease_valid", job):
                    return
                run_id = str(uuid.uuid4())
                await self._db("create_run",
                    run_id=run_id,
                    workflow_id=job.workflow_id,
                    revision_id=str(revision_id),
                    artifact_id=str(manifest["id"]),
                    trigger_type="telegram",
                )
                if not await self._repo("job_lease_valid", job):
                    return
                updated = await self._repo("set_job_status",
                    job_id, "running", run_id=run_id,
                    binding_epoch=job.binding_epoch,
                    lease_token=job.lease_token,
                )
                if updated is None:
                    return
                if not await self._repo("job_lease_valid", job):
                    return
                await self.executor.execute_run(
                    run_id=run_id,
                    draft=WorkflowDraft(**workflow["draft"]),
                    artifact_id=str(manifest["id"]),
                    trigger_type="telegram",
                    workflow_id=job.workflow_id,
                    revision_id=str(revision_id),
                )
                if not await self._repo("job_lease_valid", job):
                    return
                run = await self._db("get_run", run_id)
                if not run:
                    raise ValueError("run не найден после выполнения")
                terminal = str(run.get("execution_status") or "failed")
                finished = await self._repo("set_job_status",
                    job_id,
                    "succeeded" if terminal == "succeeded" else "failed",
                    run_id=run_id,
                    error=None if terminal == "succeeded" else terminal,
                    binding_epoch=job.binding_epoch,
                    lease_token=job.lease_token,
                )
                if finished is not None:
                    # Delivery is enqueued by the executor completion hook
                    # (see on_run_completed): one path for bot, manual and
                    # scheduled runs, idempotent on the outbox unique key.
                    logger.info("telegram job %s finished for run %s", job_id, run_id)
        except asyncio.CancelledError:
            try:
                await self._repo("set_job_status",
                    job_id, "cancelled", error="runtime stopped or authorization lost",
                    binding_epoch=job.binding_epoch,
                    lease_token=job.lease_token,
                    allow_cancelled=True,
                )
            except Exception:
                pass
            raise
        except Exception as exc:  # noqa: BLE001 - one repo/job must not kill polling
            safe = self._safe_error(exc)
            logger.warning("telegram job %s failed: %s", job_id, safe)
            try:
                updated = await self._repo("set_job_status",
                    job_id, "failed", error=safe,
                    binding_epoch=job.binding_epoch,
                    lease_token=job.lease_token,
                )
            except Exception:
                updated = None
                logger.warning("could not record failed telegram job")
            if updated is not None and await self._repo("job_is_current", job):
                await self._best_effort_message(
                    job.chat_id,
                    "Импорт или запуск не удался. Детерминированный анализ не затронут.",
                )
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def on_run_completed(self, run_id: str) -> None:
        """Executor completion hook: durable intent first, then outbox rows.

        Manual, scheduled and repository-triggered runs all end inside
        ``WorkflowExecutor.execute_run``, so this single hook closes the gap
        where only bot jobs produced a delivery. The completion fact is
        persisted as a ``telegram.run_delivery_intents`` row *before* the
        outbox is touched; if settings/enqueue fail (transient DB error) or
        the process dies in between, the recovery sweep replays the intent and
        the outbox unique key ``(chat_id, run_id, kind)`` creates exactly the
        missing rows. Writing is independent of transport state: a completion
        that lands while the bot is stopping still produces its rows.
        """
        if self.api is None:
            return
        workspace = await self._record_delivery_intent(run_id)
        if not workspace:
            return
        if await self._process_delivery_intent(run_id, workspace):
            self._pending_delivery_intents.pop(run_id, None)

    async def _record_delivery_intent(self, run_id: str) -> Optional[str]:
        """Persist the completion fact (bounded retries); return its workspace.

        None means "nothing to deliver" (run not terminal) or "kept for the
        sweep in memory after the marker write kept failing".
        """
        workspace = ""
        status_checked = False
        last_error = ""
        for attempt in range(1, DELIVERY_INTENT_RECORD_ATTEMPTS + 1):
            try:
                if not status_checked:
                    run = await self._db("get_run", run_id)
                    if (
                        not run
                        or str(run.get("execution_status") or "") not in DELIVERY_TRIGGER_STATUSES
                    ):
                        return None
                    status_checked = True
                    workspace = str(run.get("workspace_id") or "") or workspace_id()
                await self._repo("record_delivery_intent", run_id, workspace)
                return workspace
            except Exception as exc:  # noqa: BLE001 - transient DB failure
                last_error = self._safe_error(exc)
                logger.warning(
                    "telegram delivery intent write failed for %s (try %d/%d): %s",
                    run_id, attempt, DELIVERY_INTENT_RECORD_ATTEMPTS, last_error,
                )
                if attempt < DELIVERY_INTENT_RECORD_ATTEMPTS:
                    await asyncio.sleep(DELIVERY_INTENT_RECORD_DELAY_SECONDS)
        # No durable marker possible right now: keep it in memory for the
        # sweep. Should the process die before the replay, the next start's
        # _bootstrap_delivery_intents re-adopts the run from runtime.runs.
        self._pending_delivery_intents[run_id] = workspace or workspace_id()
        logger.error("telegram delivery intent not durable for %s: %s", run_id, last_error)
        return None

    async def _process_delivery_intent(self, run_id: str, workspace: str) -> bool:
        """Idempotently create the missing outbox rows and close the marker."""
        try:
            with telegram_workspace_scope(workspace):
                run = await self._db("get_run", run_id)
                if run and str(run.get("execution_status") or "") in DELIVERY_TRIGGER_STATUSES:
                    bindings = await self._repo("list_workspace_bindings", workspace)
                    for binding in bindings:
                        settings = await self._repo(
                            "get_settings", binding.chat_id,
                            user_id=binding.user_id,
                            workspace_id=binding.workspace_id,
                            binding_epoch=binding.binding_epoch,
                        )
                        kinds = [
                            kind for kind, modes in (
                                ("alert", ("alerts", "both")),
                                ("jsonc", ("jsonc", "both")),
                            )
                            if settings.delivery_mode in modes
                        ]
                        for kind in kinds:
                            await self._repo(
                                "enqueue_outbox", binding.chat_id, run_id, kind,
                                user_id=binding.user_id,
                                workspace_id=binding.workspace_id,
                                binding_epoch=binding.binding_epoch,
                            )
                    if bindings:
                        logger.info(
                            "telegram delivery enqueued for run %s (%d binding(s))",
                            run_id, len(bindings),
                        )
                # A run that is no longer terminal (or vanished) has nothing to
                # deliver: closing the marker must not loop forever.
                await self._repo("finish_delivery_intent", run_id)
            return True
        except Exception as exc:  # noqa: BLE001 - delivery never breaks the run
            safe = self._safe_error(exc)
            logger.warning("telegram delivery enqueue failed for %s: %s", run_id, safe)
            try:
                await self._repo("touch_delivery_intent", run_id, error=safe)
            except Exception:  # noqa: BLE001 - marker write failing again is fine
                logger.warning(
                    "telegram delivery intent touch failed for %s", run_id
                )
            return False

    async def _sweep_delivery_intents(self) -> int:
        """Replay unfinished intents: durable rows plus the in-memory list."""
        if self.api is None:
            return 0
        intents: List[DeliveryIntent] = []
        try:
            intents = await self._repo(
                "pending_delivery_intents", DELIVERY_INTENT_SWEEP_LIMIT
            )
        except Exception as exc:  # noqa: BLE001 - sweep is retried next cycle
            logger.warning("telegram delivery intent sweep failed: %s", self._safe_error(exc))
        handled = 0
        for run_id, workspace in list(self._pending_delivery_intents.items()):
            if await self._process_delivery_intent(run_id, workspace):
                self._pending_delivery_intents.pop(run_id, None)
                handled += 1
        for intent in intents:
            if await self._process_delivery_intent(intent.run_id, intent.workspace_id):
                handled += 1
        if not intents and not self._pending_delivery_intents:
            try:
                await self._repo("purge_delivery_intents")
            except Exception as exc:  # noqa: BLE001 - retention is optional
                logger.warning("telegram delivery intent purge failed: %s", self._safe_error(exc))
        return handled

    # --- delivery ------------------------------------------------------
    async def process_outbox_once(self, limit: int = 5) -> int:
        items = await self._repo("claim_outbox", limit=limit)
        for item in items:
            await self._deliver_outbox(item)
        return len(items)

    async def _acquire_delivery_guard(self, item: OutboxItem):
        """Own the worker-thread result even if this task is cancelled.

        ``asyncio.to_thread`` cannot be stopped. If cancellation arrives while
        the worker is waiting for the binding row lock, the completed result
        still owns a connection + row lock, so this coroutine waits for the
        worker and releases that result explicitly.
        """
        task = asyncio.create_task(
            asyncio.to_thread(self.repo.acquire_delivery_guard, item),
            name=f"telegram-guard-acquire-{item.id}",
        )
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception as exc:  # noqa: BLE001 - cleanup below
                    logger.warning(
                        "delivery guard acquire failed after cancel: %s",
                        type(exc).__name__,
                    )
                    break
            if task.cancelled():
                raise
            exc = task.exception()
            if exc is not None:
                logger.warning("delivery guard acquire failed after cancel: %s", type(exc).__name__)
            else:
                conn, _ = task.result()
                await self._release_delivery_guard_safe(conn)
            raise

    async def _release_delivery_guard_safe(self, conn: Any) -> None:
        """Release must also survive repeated cancellation of the caller."""
        if conn is None:
            return
        task = asyncio.create_task(
            asyncio.to_thread(self.repo.release_delivery_guard, conn),
            name="telegram-guard-release",
        )
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception as exc:  # noqa: BLE001 - release failure is logged
                logger.warning("delivery guard release failed: %s", type(exc).__name__)
                return
        try:
            task.result()
        except Exception as exc:  # noqa: BLE001 - connection is already lost/logged
            logger.warning("delivery guard release failed: %s", type(exc).__name__)

    async def _deliver_outbox(self, item: OutboxItem) -> None:
        async def complete(status: str, **kwargs: Any) -> bool:
            return await self._repo(
                "finish_outbox",
                item.id, status,
                user_id=item.user_id or None,
                workspace_id=item.workspace_id or None,
                binding_epoch=item.binding_epoch,
                **kwargs,
            )

        guard_conn = None
        guard_ready = False
        try:
            if not item.user_id or not item.workspace_id:
                await complete("cancelled", error="owner missing")
                return
            if not await self._repo("delivery_is_current", item):
                await complete("cancelled", error="owner changed")
                return
            if self.api is None:
                raise TelegramUnavailable("telegram is not configured")
            # Payload is built from the delivery's own owner/workspace, never
            # from the chat's current binding.
            with telegram_workspace_scope(item.workspace_id):
                # One decision point per claim: delivery mode, severity
                # threshold, project/branch scope, then quiet hours.
                decision = await self._delivery_decision(item)
                if not decision.deliver:
                    logger.info("telegram outbox %s filtered: %s", item.id, decision.reason)
                    await complete("cancelled", error=f"отфильтровано: {decision.reason}")
                    return
                if decision.defer_until is not None:
                    deferred = await self._repo(
                        "defer_outbox", item.id, decision.defer_until,
                        reason=f"тихие часы до {decision.defer_until.isoformat()}",
                        user_id=item.user_id,
                        workspace_id=item.workspace_id,
                        binding_epoch=item.binding_epoch,
                    )
                    if not deferred:
                        logger.warning(
                            "telegram outbox %s could not defer after cancel", item.id
                        )
                    return
                if item.kind == "alert":
                    text = await self._alert_text(item.run_id)
                    caption = None
                elif item.kind == "jsonc":
                    report = await asyncio.to_thread(
                        build_jsonc_report, self.db, item.run_id, item.workspace_id
                    )
                    await asyncio.to_thread(
                        write_jsonc_report, report, item.workspace_id, item.run_id
                    )
                    text = None
                    caption = (
                        f"NEDRA JSONC: run {item.run_id}\n"
                        f"deterministic findings: {report.finding_count}; "
                        f"AI hypotheses: {report.ai_hypothesis_count} (not verified)"
                    )
                else:
                    raise ValueError("unknown outbox kind")
            # Acquire the binding row lock in a worker thread: waiting for a
            # concurrent re-bind must not freeze the event loop/heartbeat.
            guard_conn, current = await self._acquire_delivery_guard(item)
            guard_ready = True
            if not current:
                await complete("cancelled", error="owner changed")
                return
            if item.kind == "alert":
                await asyncio.wait_for(
                    self.api.send_message(item.chat_id, text or ""),
                    timeout=DELIVERY_TIMEOUT_SECONDS,
                )
            else:
                await asyncio.wait_for(
                    self.api.send_document(
                        item.chat_id, report.filename, report.content,
                        caption=caption or "",
                    ),
                    timeout=DELIVERY_TIMEOUT_SECONDS,
                )
            if not await complete("sent"):
                logger.warning("telegram outbox %s finished after cancellation", item.id)
        except asyncio.CancelledError:
            # If cancellation happened before guard acquisition, the acquire
            # helper already released any completed result. A claimed row stays
            # 'sending' and is retried by the stale lease.
            if guard_ready:
                try:
                    await complete("pending", error="runtime stopped", retry_delay_seconds=5)
                except Exception:
                    pass
            raise
        except Exception as exc:  # noqa: BLE001 - Telegram failure stays in outbox
            safe = self._safe_error(exc)
            attempts = int(item.attempts)
            if attempts >= MAX_OUTBOX_ATTEMPTS:
                await complete("failed", error=safe)
                logger.warning("telegram outbox %s exhausted: %s", item.id, safe)
            else:
                delay = min(
                    OUTBOX_RETRY_MAX_SECONDS,
                    OUTBOX_RETRY_BASE_SECONDS * (2 ** max(0, attempts - 1)),
                )
                await complete("pending", error=safe, retry_delay_seconds=delay)
                logger.warning("telegram outbox %s retry in %ss: %s", item.id, delay, safe)
        finally:
            if guard_conn is not None:
                await self._release_delivery_guard_safe(guard_conn)

    async def _delivery_decision(self, item: OutboxItem) -> DeliveryDecision:
        """Evaluate delivery policy for one claimed row inside its workspace."""
        settings = await self._repo(
            "get_settings", item.chat_id,
            user_id=item.user_id,
            workspace_id=item.workspace_id,
            binding_epoch=item.binding_epoch,
        )
        run = await self._db("get_run", item.run_id)
        if not run:
            return DeliveryDecision(False, reason="run не найден")
        facts = await self._delivery_facts(item.workspace_id, run, settings)
        return evaluate_delivery(
            kind=item.kind,
            delivery_mode=settings.delivery_mode,
            run=run,
            min_severity=settings.min_severity,
            project_filter=settings.project_filter,
            branch_filter=settings.branch_filter,
            quiet_start=settings.quiet_start,
            quiet_end=settings.quiet_end,
            quiet_timezone=settings.quiet_timezone,
            facts=facts,
            now_utc=datetime.now(timezone.utc),
        )

    async def _delivery_facts(
        self, workspace: str, run: Dict[str, Any], settings: Any,
    ) -> DeliveryFacts:
        """Repository URL/branch for the filters; read only when a filter is set.

        Unavailable metadata (missing artifact, refused/failed read) is raised
        into the caller's bounded retry path — it must never collapse into an
        empty facts object that a filter would score as a successful
        non-match and cancel the delivery for good.
        """
        if not settings.project_filter and not settings.branch_filter:
            return DeliveryFacts()
        artifact_id = str(run.get("artifact_id") or "")
        if not artifact_id:
            raise DeliveryFactsUnavailable(
                "у run нет артефакта — фильтр проекта/ветки не оценивается"
            )
        try:
            meta = await asyncio.to_thread(
                self._artifact_meta_loader, workspace, artifact_id
            )
        except Exception as exc:  # noqa: BLE001 - transient read failure
            raise DeliveryFactsUnavailable(
                f"метаданные артефакта недоступны: {self._safe_error(exc)}"
            ) from exc
        if not isinstance(meta, dict):
            raise DeliveryFactsUnavailable(
                "метаданные артефакта не найдены — повтор чтения запланирован"
            )
        repository = meta.get("repository")
        repository = repository if isinstance(repository, dict) else {}
        parts = [str(repository.get("url") or ""), str(meta.get("filename") or "")]
        branch = str(repository.get("branch") or "") or None
        return DeliveryFacts(
            project_text=" ".join(part for part in parts if part),
            branch=branch,
        )

    async def _alert_text(self, run_id: str) -> str:
        run = await self._db("get_run", run_id)
        if not run:
            raise ValueError("run missing")
        ai_count = await self._ai_hypothesis_count(run_id)
        return "\n".join([
            "NEDRA scan",
            f"Run: {run.get('id')}",
            f"Статус: {run.get('execution_status')}",
            f"Вердикт: {run.get('security_verdict')}",
            (
                f"Находки: {run.get('total_findings', 0)} "
                f"(critical={run.get('critical_count', 0)}, high={run.get('high_count', 0)}, "
                f"medium={run.get('medium_count', 0)})"
            ),
            f"AI hypotheses: {ai_count} (не verified)",
            "JSONC-файл отправляется отдельно, если включён режим jsonc/both.",
        ])

    async def _ai_hypothesis_count(self, run_id: str) -> int:
        try:
            for node_run in await self._db("get_node_runs", run_id):
                if node_run.get("kind") != "ai_scan":
                    continue
                output = node_run.get("output_data_masked") or {}
                result = output.get("ai_scan_result") if isinstance(output, dict) else {}
                if isinstance(result, dict):
                    return len(result.get("observations") or [])
        except Exception:
            return 0
        return 0

    # --- helpers -------------------------------------------------------
    async def _reply(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> None:
        if self.api is None:
            raise TelegramUnavailable("telegram is not configured")
        await self.api.send_message(chat_id, text, reply_markup=reply_markup)

    async def _best_effort_message(self, chat_id: int, text: str) -> None:
        if self.api is None:
            return
        for _ in range(2):
            try:
                await self.api.send_message(chat_id, text)
                return
            except TelegramUnavailable:
                await asyncio.sleep(1)
            except Exception:
                return

    async def _scenarios(self, workspace: str) -> List[Dict[str, str]]:
        try:
            with telegram_workspace_scope(workspace):
                workflows = await self._db("list_workflows")
        except Exception:
            return []
        return [
            {"id": str(item.get("id")), "name": str(item.get("name") or "")}
            for item in workflows
            if item.get("id")
        ]

    async def _workflow(self, workspace: str, workflow_id: str) -> Optional[Dict[str, Any]]:
        try:
            with telegram_workspace_scope(workspace):
                return await self._db("get_workflow", workflow_id)
        except Exception:
            return None

    async def _recent_runs(self, workspace: str) -> List[Dict[str, Any]]:
        try:
            with telegram_workspace_scope(workspace):
                return await self._db("list_runs", limit=5)
        except Exception:
            return []

    @staticmethod
    def _looks_like_repo_url(value: str) -> bool:
        try:
            parts = urlsplit(value)
        except ValueError:
            return False
        return (
            parts.scheme.lower() == "https"
            and (parts.hostname or "").lower() in {"github.com", "gitlab.com"}
            and not parts.username
            and not parts.password
        )

    def _safe_error(self, exc: Any) -> str:
        if self.api is not None:
            return self.api.redact(exc)
        return redact_telegram_text(exc)


def create_telegram_runtime(
    db: DatabaseRepository,
    executor: Any,
    importer: Callable[[str], Awaitable[Dict[str, Any]]],
    *,
    repository: Optional[TelegramRepository] = None,
    api: Optional[TelegramBotAPI] = None,
) -> TelegramRuntime:
    """Factory: token absence disables polling but keeps the HTTP binding API usable."""
    config_error = None
    if api is None:
        try:
            api = TelegramBotAPI.from_runtime()
        except TelegramConfigError as exc:
            api = None
            config_error = redact_telegram_text(exc)
    menu_config = load_menu_config()
    if menu_config.error:
        logger.warning("telegram menu configuration: %s", menu_config.error)
    return TelegramRuntime(
        db, executor, importer,
        repository=repository or TelegramRepository(),
        api=api,
        config_error=config_error,
        menu_config=menu_config,
    )