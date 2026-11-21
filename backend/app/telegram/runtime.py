"""Telegram runtime: polling, binding commands, repo jobs and delivery outbox.

Dependency injection is explicit: DatabaseRepository, WorkflowExecutor, the
repository importer callable and the Bot API. The runtime never reads the bot
token itself; TelegramBotAPI owns it and redacts every error.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set
from urllib.parse import urlsplit

from backend.app.contracts.schemas import WorkflowDraft
from backend.app.storage.database import DatabaseRepository
from backend.app.storage.telegram import (
    JOB_LEASE_SECONDS,
    MAX_JOB_RECOVERY_ATTEMPTS,
    JobOwnershipLost,
    OutboxItem,
    SettingsOwnershipLost,
    TelegramRepository,
)
from backend.app.telegram.bot_api import (
    TelegramBotAPI,
    TelegramConfigError,
    TelegramUnavailable,
    redact_telegram_text,
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

HELP_TEXT = """NEDRA Telegram

/start <код> — привязать аккаунт по одноразовому коду из веб-кабинета
/status — привязка и последние запуски
/settings — режим доставки и сценарий
/settings alerts|jsonc|both — что присылать
/settings scenario <id> — выбрать workflow
/help — эта справка

Отправьте HTTPS-ссылку GitHub/GitLab, чтобы импортировать репозиторий и запустить выбранный сценарий."""


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
    ):
        self.db = db
        self.executor = executor
        self.importer = importer
        self.repo = repository or TelegramRepository()
        self.api = api
        self.poll_timeout = max(1, min(int(poll_timeout), 50))
        self.outbox_interval = max(0.5, min(float(outbox_interval), 30.0))

        self._stop = asyncio.Event()
        self._started = False
        self._poll_task: Optional[asyncio.Task] = None
        self._outbox_task: Optional[asyncio.Task] = None
        self._job_tasks: Set[asyncio.Task] = set()
        self._active_job_ids: Set[str] = set()
        self._recovery_task: Optional[asyncio.Task] = None
        self._external_status = "not_started"
        self._last_error: Optional[str] = None
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
            self._last_error = "TELEGRAM_BOT_TOKEN не настроен"
            logger.warning("telegram runtime disabled: token not configured")
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

        self._poll_task = asyncio.create_task(self._poll_loop(), name="telegram-poll")
        self._outbox_task = asyncio.create_task(self._outbox_loop(), name="telegram-outbox")
        return self.status()

    async def stop(self) -> None:
        self._stop.set()
        self._started = False
        tasks = [
            task for task in (self._poll_task, self._outbox_task, self._recovery_task)
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
            "lastError": self._last_error,
        }

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
            try:
                await self.recover_jobs_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - recovery must not kill runtime
                logger.warning("telegram job recovery failed: %s", self._safe_error(exc))
            await self._wait(JOB_RECOVERY_INTERVAL_SECONDS)

    async def recover_jobs_once(self) -> int:
        """Bounded pass: settle orphaned runs and spawn claimable jobs."""
        ids = await asyncio.to_thread(
            self.repo.recover_jobs, JOB_RECOVERY_BATCH, MAX_JOB_RECOVERY_ATTEMPTS
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
        if not isinstance(message, dict):
            return
        chat = message.get("chat")
        sender = message.get("from")
        if not isinstance(chat, dict) or not isinstance(sender, dict):
            return
        if chat.get("type") != "private" or sender.get("is_bot") is True:
            return
        chat_id = chat.get("id")
        update_id = update.get("update_id")
        if not isinstance(chat_id, int) or not isinstance(update_id, int):
            return
        text = message.get("text")
        if not isinstance(text, str):
            text = ""
        username = sender.get("username") if isinstance(sender.get("username"), str) else None

        if not await self._repo("claim_update", update_id, chat_id):
            return
        try:
            await self._dispatch_text(chat_id, username, text, update_id)
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

    async def _dispatch_text(
        self,
        chat_id: int,
        username: Optional[str],
        text: str,
        update_id: int,
    ) -> None:
        value = text.strip()
        if not value:
            await self._reply(chat_id, HELP_TEXT)
            return
        parts = value.split(maxsplit=1)
        command = parts[0].split("@", 1)[0].lower()
        argument = parts[1].strip() if len(parts) > 1 else ""

        if command == "/start":
            await self._handle_start(chat_id, username, argument)
            return
        if command == "/help":
            await self._reply(chat_id, HELP_TEXT)
            return
        if command == "/settings":
            await self._handle_settings(chat_id, argument)
            return
        if command == "/status":
            await self._handle_status(chat_id)
            return
        if self._looks_like_repo_url(value):
            await self._start_repo_job(chat_id, value, update_id)
            return
        await self._reply(chat_id, HELP_TEXT)

    async def _handle_start(
        self,
        chat_id: int,
        username: Optional[str],
        argument: str,
    ) -> None:
        binding = await self._repo("get_binding", chat_id)
        if argument:
            bound = await self._repo("consume_binding_code", argument, chat_id, username)
            if bound is None:
                await self._reply(
                    chat_id,
                    "Код не найден, истёк или уже использован. Создайте новый код в веб-кабинете.",
                )
                return
            await self._reply(
                chat_id,
                "Аккаунт привязан. Дальше: /settings scenario <id>, затем отправьте HTTPS-ссылку репозитория.",
            )
            return
        if binding is None:
            await self._reply(
                chat_id,
                "Аккаунт не привязан. Войдите в веб-кабинет, создайте одноразовый код "
                "и отправьте /start <код>.",
            )
            return
        await self._reply(chat_id, "Аккаунт уже привязан.\n\n" + HELP_TEXT)

    async def _handle_settings(self, chat_id: int, argument: str) -> None:
        binding = await self._repo("get_binding", chat_id)
        if binding is None:
            await self._reply(chat_id, "Сначала привяжите аккаунт: /start <код>.")
            return
        settings = await self._repo("get_settings", chat_id)
        if not argument:
            scenarios = await self._scenarios(binding.workspace_id)
            lines = [
                f"Режим: {settings.delivery_mode}",
                f"Сценарий: {settings.scenario or 'не выбран'}",
            ]
            if scenarios:
                lines.append("Доступные сценарии:")
                lines.extend(f"  {item['id']} — {item['name']}" for item in scenarios[:8])
            lines.append("Изменить: /settings alerts|jsonc|both")
            lines.append("Сценарий: /settings scenario <id>")
            await self._reply(chat_id, "\n".join(lines))
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
                return
            await self._reply(chat_id, f"Режим доставки: {mode}")
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
                return
            await self._reply(chat_id, f"Сценарий выбран: {scenario}")
            return
        await self._reply(chat_id, "Использование: /settings alerts|jsonc|both или /settings scenario <id>")

    async def _handle_status(self, chat_id: int) -> None:
        binding = await self._repo("get_binding", chat_id)
        if binding is None:
            await self._reply(chat_id, "Аккаунт не привязан. /start <код>.")
            return
        settings = await self._repo("get_settings", chat_id)
        runs = await self._recent_runs(binding.workspace_id)
        jobs = await self._repo("list_jobs",
            binding.chat_id, binding.user_id, binding.workspace_id,
            binding.binding_epoch, limit=3,
        )
        lines = [
            "Статус аккаунта",
            "Привязка: активна",
            f"Режим: {settings.delivery_mode}",
            f"Сценарий: {settings.scenario or 'не выбран'}",
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
        await self._reply(chat_id, "\n".join(lines))

    # --- repo job ------------------------------------------------------
    async def _start_repo_job(
        self,
        chat_id: int,
        repo_url: str,
        update_id: int,
    ) -> None:
        binding = await self._repo("get_binding", chat_id)
        if binding is None:
            await self._reply(chat_id, "Сначала привяжите аккаунт: /start <код>.")
            return
        settings = await self._repo("get_settings", chat_id)
        if not settings.scenario:
            scenarios = await self._scenarios(binding.workspace_id)
            lines = ["Сначала выберите сценарий: /settings scenario <id>"]
            if scenarios:
                lines.extend(f"  {item['id']} — {item['name']}" for item in scenarios[:8])
            await self._reply(chat_id, "\n".join(lines))
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
        if job_id in self._active_job_ids:
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

    async def _heartbeat_job(self, job: Any) -> None:
        interval = max(5.0, JOB_LEASE_SECONDS / 3)
        while True:
            await asyncio.sleep(interval)
            try:
                alive = await asyncio.to_thread(
                    self.repo.heartbeat_job, job, JOB_LEASE_SECONDS
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("telegram job heartbeat failed: %s", self._safe_error(exc))
                return
            if not alive:
                return

    async def _run_job(self, job_id: str) -> None:
        job = await self._repo("claim_job",
            job_id, JOB_LEASE_SECONDS, MAX_JOB_RECOVERY_ATTEMPTS
        )
        if job is None:
            return
        heartbeat = asyncio.create_task(
            self._heartbeat_job(job), name=f"telegram-heartbeat-{job_id}"
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
                    await self._enqueue_notifications(job, run_id)
        except asyncio.CancelledError:
            try:
                await self._repo("set_job_status",
                    job_id, "cancelled", error="runtime stopped",
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

    async def _enqueue_notifications(self, job: Any, run_id: str) -> None:
        if not await self._repo("job_is_current", job):
            return
        settings = await self._repo("get_settings", job.chat_id)
        if settings.delivery_mode in ("alerts", "both"):
            await self._repo("enqueue_outbox",
                job.chat_id, run_id, "alert",
                user_id=job.user_id, workspace_id=job.workspace_id,
                binding_epoch=job.binding_epoch,
            )
        if settings.delivery_mode in ("jsonc", "both"):
            await self._repo("enqueue_outbox",
                job.chat_id, run_id, "jsonc",
                user_id=job.user_id, workspace_id=job.workspace_id,
                binding_epoch=job.binding_epoch,
            )

    # --- delivery ------------------------------------------------------
    async def process_outbox_once(self, limit: int = 5) -> int:
        items = await self._repo("claim_outbox", limit=limit)
        for item in items:
            await self._deliver_outbox(item)
        return len(items)

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
            guard_conn, current = await asyncio.to_thread(
                self.repo.acquire_delivery_guard, item
            )
            try:
                if not current:
                    await complete("cancelled", error="owner changed")
                    return
                if item.kind == "alert":
                    await self.api.send_message(item.chat_id, text or "")
                else:
                    await self.api.send_document(
                        item.chat_id, report.filename, report.content,
                        caption=caption or "",
                    )
                if not await complete("sent"):
                    logger.warning("telegram outbox %s finished after cancellation", item.id)
            finally:
                await asyncio.to_thread(
                    self.repo.release_delivery_guard, guard_conn
                )
                guard_conn = None
        except asyncio.CancelledError:
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
    async def _reply(self, chat_id: int, text: str) -> None:
        if self.api is None:
            raise TelegramUnavailable("telegram is not configured")
        await self.api.send_message(chat_id, text)

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
    if api is None:
        try:
            api = TelegramBotAPI.from_runtime()
        except TelegramConfigError:
            api = None
    return TelegramRuntime(
        db, executor, importer,
        repository=repository or TelegramRepository(),
        api=api,
    )