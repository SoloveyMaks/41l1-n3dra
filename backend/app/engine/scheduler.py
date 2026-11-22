import asyncio
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlsplit
import uuid

from croniter import croniter

from backend.app.codefacts.identity import workspace_scope
from backend.app.contracts.schemas import WorkflowDraft
from backend.app.storage.database import ArtifactImmutableError, DatabaseRepository

logger = logging.getLogger("WorkflowScheduler")


async def _await_thread(function: Any, *args: Any, **kwargs: Any) -> Any:
    """Run one blocking call in a worker thread and survive cancellation.

    Unlike a bare ``to_thread`` await, cancelling the caller does not abandon
    the thread: the task waits for the real operation to finish, then raises
    ``CancelledError``. Repeated cancellation during that wait is ignored so it
    cannot interrupt the wait. The ContextVar workspace is captured by
    ``to_thread`` at call time.
    """
    task = asyncio.ensure_future(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if task.cancelled():
            raise
        error = task.exception()
        if error is not None:
            # The operation failed while shutdown was waiting for it. Keep the
            # failure as context, but cancellation remains the caller's result.
            raise asyncio.CancelledError() from error
        raise


def _discard_and_log(task: asyncio.Task, tasks: set[asyncio.Task], label: str) -> None:
    """Drop a finished task and surface an exception nobody awaited."""
    tasks.discard(task)
    if task.cancelled():
        return
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        logger.warning("%s failed: %s", label, type(exc).__name__)


MAX_INTERVAL_SECONDS = 365 * 24 * 60 * 60

MIN_WATCH_INTERVAL_SECONDS = 60
DEFAULT_WATCH_INTERVAL_SECONDS = 300
MAX_WATCH_INTERVAL_SECONDS = 30 * 24 * 60 * 60
WATCH_CLAIM_SECONDS = 30 * 60
WATCH_TICK_SECONDS = 5
MAX_ACTIVE_WATCH_CHECKS = 2
WATCH_RECOVERY_SECONDS = 60
WEBHOOK_LEASE_SECONDS = 10 * 60
WEBHOOK_BATCH = 10
MAX_ACTIVE_WEBHOOK_PROCESSING = 2
WEBHOOK_MAX_ATTEMPTS = 3
WEBHOOK_RETRY_SECONDS = 5
SCHEDULE_CLAIM_SECONDS = 60
SCHEDULE_BATCH = 20
MAX_ACTIVE_SCHEDULE_RUNS = 4
SCHEDULE_RECOVERY_SECONDS = 60

_STORAGE_MANIFEST_KEYS = (
    "id", "filename", "hash_sha256", "size_bytes", "uncompressed_size_bytes",
    "file_count", "kotlin_file_count", "gradle_file_count", "extracted_path",
    "created_at",
)
_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_URL_CREDENTIALS_RE = re.compile(r"(https?://)[^/\s@]+@", re.IGNORECASE)


class WatchValidationError(ValueError):
    """Некорректный watch: сценарий, URL, ветка или cadence."""


class WatchUnavailableError(RuntimeError):
    """Importer/резолвер недоступен; наружу отдаётся безопасный текст."""


class WatchNotFoundError(LookupError):
    """Watch не найден в workspace."""


def _interval_seconds(expression: str) -> int:
    raw = expression.split(":", 1)[1].strip()
    try:
        seconds = int(raw)
    except ValueError as exc:
        raise ValueError("interval должен быть целым числом секунд") from exc
    if not 1 <= seconds <= MAX_INTERVAL_SECONDS:
        raise ValueError(f"interval должен быть от 1 до {MAX_INTERVAL_SECONDS} секунд")
    return seconds


def next_fire_at(expression: str, now: datetime) -> datetime:
    if expression.startswith("interval:"):
        return now + timedelta(seconds=_interval_seconds(expression))
    return croniter(expression, now).get_next(datetime)


def validate_schedule_expression(value: Any) -> str:
    """Accept a bounded interval or a cron that really has a next fire."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("cronOrInterval должен быть interval:N или 5-полевым cron")
    expression = value.strip()
    if expression.startswith("interval:"):
        _interval_seconds(expression)
        return expression
    if len(expression.split()) != 5:
        raise ValueError("cron должен состоять из 5 полей")
    try:
        next_fire_at(expression, datetime.now(timezone.utc))
    except Exception as exc:
        raise ValueError("cron не имеет следующего срабатывания или некорректен") from exc
    return expression


def _watch_interval_seconds(expression: str) -> int:
    seconds = _interval_seconds(expression)
    if seconds < MIN_WATCH_INTERVAL_SECONDS:
        raise ValueError(
            f"Проверка репозитория не может быть чаще {MIN_WATCH_INTERVAL_SECONDS} секунд"
        )
    if seconds > MAX_WATCH_INTERVAL_SECONDS:
        raise ValueError(
            "Проверка репозитория не может быть реже 30 дней"
        )
    return seconds


def validate_watch_expression(value: Any) -> str:
    """Polling cadence: interval >= 60s or a cron that cannot fire more often."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("cronOrInterval должен быть interval:N или 5-полевым cron")
    expression = value.strip()
    if expression.startswith("interval:"):
        _watch_interval_seconds(expression)
        return expression
    if len(expression.split()) != 5:
        raise ValueError("cron должен состоять из 5 полей")
    try:
        first = next_fire_at(expression, datetime.now(timezone.utc))
        second = next_fire_at(expression, first)
    except Exception as exc:
        raise ValueError("cron не имеет следующего срабатывания или некорректен") from exc
    if (second - first).total_seconds() < MIN_WATCH_INTERVAL_SECONDS:
        raise ValueError(
            f"Проверка репозитория не может быть чаще {MIN_WATCH_INTERVAL_SECONDS} секунд"
        )
    return expression


def _next_watch_at(expression: str, now: datetime) -> datetime:
    try:
        return next_fire_at(expression, now)
    except Exception:  # повреждённую строку не превращаем в tight loop
        return now + timedelta(seconds=DEFAULT_WATCH_INTERVAL_SECONDS)


def _safe_error_text(value: Any, fallback: str, limit: int = 300) -> str:
    text = str(value or "").strip() or fallback
    text = _URL_CREDENTIALS_RE.sub(r"\1***@", text)
    return text[:limit]


def _repository_api():
    try:
        from backend.app.artifacts.repository import (
            RepositoryImportError,
            import_repository,
            resolve_repository,
        )
    except ImportError:
        raise WatchUnavailableError(
            "Импорт репозиториев недоступен: модуль importer-а ещё не подключён"
        ) from None
    return resolve_repository, import_repository, RepositoryImportError


def _storage_manifest(manifest: Dict[str, Any]) -> Dict[str, Any]:
    return {key: manifest[key] for key in _STORAGE_MANIFEST_KEYS if key in manifest}


def _validated_repository(data: Any) -> Dict[str, str]:
    if not isinstance(data, dict):
        raise WatchValidationError("Резолвер репозитория вернул некорректный ответ")
    url = data.get("url")
    provider = data.get("provider")
    branch = data.get("branch")
    commit_sha = data.get("commit_sha")
    if not isinstance(url, str) or len(url) > 2048:
        raise WatchValidationError("Резолвер вернул некорректный URL")
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
    ):
        raise WatchValidationError(
            "Разрешены только публичные HTTPS-репозитории без credentials в URL"
        )
    if provider not in ("github", "gitlab"):
        raise WatchValidationError("Поддерживаются только GitHub и GitLab")
    if not isinstance(branch, str) or not branch.strip() or len(branch) > 255:
        raise WatchValidationError("Не удалось определить ветку репозитория")
    if any(ch.isspace() for ch in branch):
        raise WatchValidationError("Некорректное имя ветки репозитория")
    if not isinstance(commit_sha, str) or not _SHA_RE.match(commit_sha):
        raise WatchValidationError("Резолвер вернул некорректный SHA коммита")
    return {
        "url": url,
        "provider": provider,
        "branch": branch.strip(),
        "commit_sha": commit_sha,
    }


class RepositoryWatchService:
    """Durable repository polling: claim rows, import exact SHA, queue one run."""

    def __init__(self, db: DatabaseRepository, executor: Any = None):
        self.db = db
        self.executor = executor
        self._sem = asyncio.Semaphore(MAX_ACTIVE_WATCH_CHECKS)
        self._hook_sem = asyncio.Semaphore(MAX_ACTIVE_WEBHOOK_PROCESSING)
        self._dispatch_tasks: set[asyncio.Task] = set()
        self._hook_tasks: set[asyncio.Task] = set()

    async def _db(self, name: str, *args: Any, **kwargs: Any) -> Any:
        return await _await_thread(getattr(self.db, name), *args, **kwargs)

    # --- admin operations -------------------------------------------------
    async def create_watch(self, workflow_id: Any, url: Any, branch: Any,
                           cron_or_interval: Any,
                           trigger_mode: Any = "poll") -> Dict[str, Any]:
        if trigger_mode not in ("poll", "webhook"):
            raise WatchValidationError("triggerMode должен быть poll или webhook")
        if not isinstance(workflow_id, str) or not workflow_id.strip():
            raise WatchValidationError("workflowId обязателен")
        if not isinstance(url, str) or not url.strip() or len(url) > 2048:
            raise WatchValidationError("url обязателен")
        if branch is not None and not isinstance(branch, str):
            raise WatchValidationError("branch должен быть строкой")
        if isinstance(branch, str) and len(branch.strip()) > 255:
            raise WatchValidationError("branch слишком длинный")
        try:
            parts = urlsplit(url.strip())
            host = (parts.hostname or "").lower()
            port = parts.port
        except ValueError as exc:
            raise WatchValidationError("Некорректный URL репозитория") from exc
        if (
            parts.scheme != "https"
            or not host
            or host not in ("github.com", "gitlab.com")
            or port is not None
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
        ):
            raise WatchValidationError(
                "Разрешены только публичные HTTPS-репозитории без credentials и порта в URL"
            )
        if trigger_mode == "webhook":
            # Push-режим не опрашивает провайдера: cadence остаётся дефолтной
            # для схемы, next_check_at пустой, due-запрос такие строки не берёт.
            expression = f"interval:{DEFAULT_WATCH_INTERVAL_SECONDS}"
        else:
            try:
                expression = validate_watch_expression(cron_or_interval)
            except ValueError as exc:
                raise WatchValidationError(str(exc)) from exc
        workflow = await self._db("get_workflow", workflow_id.strip())
        if not workflow:
            raise WatchValidationError("Сценарий не найден")
        if not workflow.get("latest_revision_id") or not workflow.get("draft"):
            raise WatchValidationError("У сценария нет сохранённой ревизии")

        resolve_repository, _, import_error = _repository_api()
        try:
            resolved = await resolve_repository(
                url.strip(), branch.strip() if isinstance(branch, str) and branch.strip() else None
            )
        except import_error as exc:
            raise WatchValidationError(
                _safe_error_text(str(exc), "Не удалось проверить репозиторий")
            ) from exc
        except WatchUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 - текст чужой ошибки не отдаём
            logger.warning("Repository resolve failed at watch create: %s", type(exc).__name__)
            raise WatchUnavailableError("Не удалось проверить репозиторий") from None

        data = _validated_repository(resolved)
        now = datetime.now(timezone.utc)
        next_at = None if trigger_mode == "webhook" else _next_watch_at(expression, now)
        watch_id = f"rw_{uuid.uuid4().hex[:10]}"
        await self._db(
            "create_repository_watch",
            watch_id,
            workflow_id.strip(),
            data["url"],
            data["provider"],
            data["branch"],
            expression,
            next_at.isoformat() if next_at else None,
            data["commit_sha"],
            now.isoformat(),
            trigger_mode,
        )
        row = await self._db("get_repository_watch", watch_id)
        if row is None:
            raise WatchUnavailableError("Watch создан, но не прочитан из хранилища")
        return row

    async def set_enabled(self, watch_id: str, enabled: bool) -> Dict[str, Any]:
        watch = await self._db("get_repository_watch", watch_id)
        if not watch:
            raise WatchNotFoundError("Расписание репозитория не найдено")
        next_at = None
        if enabled and watch.get("trigger_mode") != "webhook":
            next_at = _next_watch_at(
                watch.get("cron_or_interval", f"interval:{DEFAULT_WATCH_INTERVAL_SECONDS}"),
                datetime.now(timezone.utc),
            ).isoformat()
        row = await self._db("set_repository_watch_enabled", watch_id, enabled, next_at)
        if row is None:
            raise WatchNotFoundError("Расписание репозитория не найдено")
        return row

    async def check_now(self, watch_id: str) -> Dict[str, Any]:
        watch = await self._db("get_repository_watch", watch_id)
        if not watch:
            raise WatchNotFoundError("Расписание репозитория не найдено")
        token = f"wchk_{uuid.uuid4().hex}"
        claimed = await self._db("claim_repository_watch", watch_id, token, WATCH_CLAIM_SECONDS)
        if not claimed:
            return {
                "status": "busy",
                "mode": "branch_check",
                "detail": "Проверка уже выполняется другим процессом",
            }
        result = await self.check_claimed(claimed, token)
        result["mode"] = "branch_check"
        return result

    async def get_watch(self, watch_id: str) -> Dict[str, Any]:
        watch = await self._db("get_repository_watch", watch_id)
        if not watch:
            raise WatchNotFoundError("Расписание репозитория не найдено")
        return watch

    async def setup_webhook(self, watch_id: str,
                            rotate: bool = False) -> Dict[str, Any]:
        """Initialize or rotate the per-watch nonce; the secret is derived by API."""
        watch = await self.get_watch(watch_id)
        if watch.get("trigger_mode") != "webhook":
            raise WatchValidationError(
                "Вебхук доступен только для отслеживания по push"
            )
        nonce = watch.get("webhook_nonce")
        if rotate or not nonce:
            nonce = uuid.uuid4().hex + uuid.uuid4().hex
            row = await self._db(
                "set_repository_watch_webhook_nonce", watch_id, nonce
            )
            if row is None:
                raise WatchNotFoundError("Расписание репозитория не найдено")
            return row
        return watch

    async def check_claimed(self, watch: Dict[str, Any], token: str) -> Dict[str, Any]:
        async with self._sem:
            return await self._check_claimed(watch, token)

    # --- one watch check --------------------------------------------------
    async def _resolve_head(self, watch: Dict[str, Any]) -> Dict[str, str]:
        resolve_repository, _, import_error = _repository_api()
        try:
            resolved = await resolve_repository(
                watch["url"], watch.get("branch") or None
            )
        except import_error as exc:
            raise WatchValidationError(
                _safe_error_text(str(exc), "Не удалось проверить репозиторий")
            ) from exc
        except WatchUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("Repository resolve failed: %s", type(exc).__name__)
            raise WatchUnavailableError("Не удалось проверить репозиторий") from None
        return _validated_repository(resolved)

    async def _check_claimed(self, watch: Dict[str, Any], token: str) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        next_at = _next_watch_at(
            watch.get("cron_or_interval", f"interval:{DEFAULT_WATCH_INTERVAL_SECONDS}"), now
        )
        try:
            workflow = await self._db("get_workflow", watch["workflow_id"])
            if not workflow or not workflow.get("latest_revision_id") or not workflow.get("draft"):
                raise WatchValidationError("Сценарий или его ревизия недоступны")
            head = await self._resolve_head(watch)
        except (WatchValidationError, WatchUnavailableError) as exc:
            return await self._fail(watch, token, str(exc), now, next_at)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Repository watch check failed: %s", type(exc).__name__)
            return await self._fail(
                watch, token, "Не удалось проверить репозиторий", now, next_at
            )

        commit_sha = head["commit_sha"]
        if commit_sha == watch.get("last_seen_commit"):
            await self._db(
                "finish_repository_watch_unchanged",
                watch["id"], token, now.isoformat(), next_at.isoformat(),
            )
            return {"status": "unchanged", "commitSha": commit_sha}

        return await self._process_commit(
            watch, commit_sha, token, workflow["latest_revision_id"], now, next_at
        )

    async def _process_commit(self, watch: Dict[str, Any], commit_sha: str,
                              token: str, revision_id: str, now: datetime,
                              next_at: Optional[datetime]) -> Dict[str, Any]:
        """Import the exact SHA and open at most one run for this watch+SHA.

        Polling, manual branch checks and webhook deliveries share this gate.
        The ledger row is claimed atomically, so a concurrent caller neither
        imports a second time nor enqueues a second run.
        """
        existing = await self._db("get_repository_watch_commit", watch["id"], commit_sha)
        if existing and existing.get("state") in ("queued", "running", "done"):
            if next_at is not None:
                await self._db(
                    "finish_repository_watch_unchanged",
                    watch["id"], token, now.isoformat(), next_at.isoformat(), commit_sha,
                )
            return {
                "status": "unchanged",
                "commitSha": commit_sha,
                "runId": existing.get("run_id"),
            }

        claim = await self._db(
            "repository_watch_claim_commit", watch["id"], commit_sha, token, WATCH_CLAIM_SECONDS
        )
        if not claim:
            fresh = await self._db("get_repository_watch_commit", watch["id"], commit_sha)
            if fresh and fresh.get("state") == "processing":
                if next_at is not None:
                    await self._db(
                        "finish_repository_watch_unchanged",
                        watch["id"], token, now.isoformat(), next_at.isoformat(),
                    )
                return {
                    "status": "busy",
                    "commitSha": commit_sha,
                    "detail": "Тот же коммит уже проверяется",
                    "leaseUntil": fresh.get("claimed_until"),
                }
            if fresh and fresh.get("state") in ("queued", "running", "done"):
                if next_at is not None:
                    await self._db(
                        "finish_repository_watch_unchanged",
                        watch["id"], token, now.isoformat(), next_at.isoformat(), commit_sha,
                    )
                return {
                    "status": "unchanged",
                    "commitSha": commit_sha,
                    "runId": fresh.get("run_id"),
                }
            return await self._fail(
                watch, token, "Не удалось зафиксировать проверку коммита", now, next_at
            )

        try:
            import_repository = _repository_api()[1]
            manifest = await import_repository(
                watch["url"], watch.get("branch") or None, commit_sha=commit_sha
            )
            stored = _storage_manifest(manifest)
            await self._db("save_artifact", stored)
        except WatchValidationError as exc:
            return await self._fail_commit(watch, token, commit_sha, str(exc), now, next_at)
        except ArtifactImmutableError:
            return await self._fail_commit(
                watch, token, commit_sha,
                "Артефакт этого коммита уже сохранён с другими данными", now, next_at,
            )
        except WatchUnavailableError as exc:
            return await self._fail_commit(watch, token, commit_sha, str(exc), now, next_at)
        except Exception as exc:  # noqa: BLE001 - provider body/URL не уходят
            if type(exc).__name__ == "RepositoryImportError":
                reason = _safe_error_text(
                    str(exc), "Не удалось скачать изменения репозитория"
                )
            else:
                logger.warning("Repository import failed: %s", type(exc).__name__)
                reason = "Не удалось скачать изменения репозитория"
            return await self._fail_commit(watch, token, commit_sha, reason, now, next_at)

        run_id = f"run_{uuid.uuid4().hex[:12]}"
        committed = await self._db(
            "commit_repository_watch_run",
            watch["id"], commit_sha, token, run_id,
            watch["workflow_id"], revision_id,
            stored["id"], now.isoformat(),
            next_at.isoformat() if next_at else None,
        )
        if not committed:
            fresh = await self._db("get_repository_watch_commit", watch["id"], commit_sha)
            return {
                "status": "busy",
                "commitSha": commit_sha,
                "detail": "Проверку перехватил другой процесс",
                "leaseUntil": (fresh or {}).get("claimed_until"),
            }
        self._schedule_dispatch(run_id)
        return {"status": "started", "runId": run_id, "commitSha": commit_sha}

    async def _fail(self, watch: Dict[str, Any], token: str, message: str,
                    now: datetime, next_at: Optional[datetime]) -> Dict[str, Any]:
        detail = _safe_error_text(message, "Проверка не завершилась")
        try:
            await self._db(
                "fail_repository_watch", watch["id"], token, detail,
                now.isoformat(), next_at.isoformat() if next_at else None,
            )
        except Exception as exc:  # noqa: BLE001 - claim всё равно истечёт
            logger.warning("Failed to persist watch error: %s", type(exc).__name__)
        return {
            "status": "error",
            "commitSha": watch.get("last_seen_commit"),
            "detail": detail,
        }

    async def _fail_commit(self, watch: Dict[str, Any], token: str, commit_sha: str,
                           message: str, now: datetime,
                           next_at: Optional[datetime]) -> Dict[str, Any]:
        detail = _safe_error_text(message, "Проверка не завершилась")
        try:
            await self._db(
                "fail_repository_watch_commit",
                watch["id"], commit_sha, token, detail,
                now.isoformat(), next_at.isoformat() if next_at else None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to persist watch commit error: %s", type(exc).__name__)
        return {"status": "error", "commitSha": commit_sha, "detail": detail}

    # --- durable webhook inbox -------------------------------------------
    async def drain_hook_inbox(self) -> None:
        """Lease and process accepted deliveries, regardless of poll schedules."""
        free = MAX_ACTIVE_WEBHOOK_PROCESSING - len(self._hook_tasks)
        if free <= 0:
            return
        token = f"whb_{uuid.uuid4().hex}"
        rows = await self._db(
            "claim_repository_hook_deliveries_server",
            min(free, WEBHOOK_BATCH), token, WEBHOOK_LEASE_SECONDS,
        )
        for row in rows:
            workspace_id = row.get("workspace_id")
            task = asyncio.create_task(
                self.process_delivery(row, token, workspace_id)
            )
            self._hook_tasks.add(task)
            task.add_done_callback(self._hook_done)

    async def process_delivery(self, row: Dict[str, Any], token: str,
                               workspace_id: Optional[str] = None) -> None:
        async with self._hook_sem:
            if workspace_id:
                with workspace_scope(workspace_id):
                    await self._process_delivery(row, token)
            else:
                await self._process_delivery(row, token)

    async def _process_delivery(self, row: Dict[str, Any], token: str) -> None:
        delivery_row_id = row.get("id")
        watch_id = row.get("watch_id")
        commit_sha = row.get("commit_sha")
        now = datetime.now(timezone.utc)
        try:
            watch = await self._db("get_repository_watch", watch_id)
            if not watch:
                return await self._finish_delivery(
                    delivery_row_id, token, "ignored", None,
                    "Расписание удалено до обработки", None,
                )
            if not watch.get("enabled"):
                return await self._finish_delivery(
                    delivery_row_id, token, "ignored", None,
                    "Отслеживание приостановлено", None,
                )
            if watch.get("trigger_mode") != "webhook":
                return await self._finish_delivery(
                    delivery_row_id, token, "ignored", None,
                    "Расписание не в режиме push", None,
                )
            if not isinstance(commit_sha, str) or not _SHA_RE.match(commit_sha):
                return await self._finish_delivery(
                    delivery_row_id, token, "failed", None,
                    "В доставке нет корректного SHA", "Некорректный commit SHA",
                )
            revision_id = row.get("workflow_revision_id")
            if not revision_id:
                workflow = await self._db("get_workflow", watch["workflow_id"])
                revision_id = (workflow or {}).get("latest_revision_id")
            if not revision_id:
                return await self._finish_delivery(
                    delivery_row_id, token, "failed", None,
                    "Сохранённая ревизия сценария недоступна",
                    "Ревизия сценария недоступна",
                )
            result = await self._process_commit(
                watch, commit_sha, f"whc_{uuid.uuid4().hex}",
                revision_id, now, None,
            )
        except asyncio.CancelledError:
            raise  # lease истечёт, доставку заберёт следующий тик
        except Exception as exc:  # noqa: BLE001
            logger.warning("Repository hook delivery failed: %s", type(exc).__name__)
            return await self._retry_delivery(
                row, token, "Не удалось обработать push-событие",
            )

        status = result.get("status")
        if status == "started":
            await self._finish_delivery(
                delivery_row_id, token, "done", result.get("runId"),
                "Прогон создан", None,
            )
            await self._watch_delivery(watch_id, "started", None)
            return
        if status == "unchanged":
            await self._finish_delivery(
                delivery_row_id, token, "ignored", result.get("runId"),
                "Этот коммит уже обработан", None,
            )
            await self._watch_delivery(watch_id, "unchanged", None)
            return
        if status == "error":
            return await self._retry_delivery(
                row, token, result.get("detail") or "Импорт коммита не удался",
            )
        return await self._defer_delivery(
            row, token, result.get("detail") or "Коммит уже проверяется",
            result.get("leaseUntil"),
        )

    async def _finish_delivery(self, delivery_row_id: str, token: str,
                               state: str, run_id: Optional[str],
                               detail: str, error: Optional[str],
                               attempt: Optional[int] = None) -> None:
        try:
            await self._db(
                "finish_repository_hook_delivery",
                delivery_row_id, token, state, run_id, detail, error, attempt,
            )
        except Exception as exc:  # noqa: BLE001 - lease всё равно истечёт
            logger.warning("Failed to finish hook delivery: %s", type(exc).__name__)

    async def _defer_delivery(self, row: Dict[str, Any], token: str,
                              detail: str, lease_until: Any) -> None:
        """Busy ledger is not a failure: wait for its lease, keep the retry budget."""
        safe_detail = _safe_error_text(detail, "Коммит уже проверяется")
        next_at = datetime.now(timezone.utc) + timedelta(seconds=WEBHOOK_RETRY_SECONDS)
        if isinstance(lease_until, str) and lease_until:
            try:
                lease_at = datetime.fromisoformat(lease_until)
                if lease_at.tzinfo is None:
                    lease_at = lease_at.replace(tzinfo=timezone.utc)
                if lease_at > next_at:
                    next_at = lease_at + timedelta(seconds=1)
            except ValueError:
                pass
        try:
            await self._db(
                "defer_repository_hook_delivery",
                row.get("id"), token, safe_detail, next_at.isoformat(),
            )
        except Exception as exc:  # noqa: BLE001 - истёкший lease вернёт доставку
            logger.warning("Failed to defer hook delivery: %s", type(exc).__name__)
        await self._watch_delivery(row.get("watch_id"), "processing", None)

    async def _retry_delivery(self, row: Dict[str, Any], token: str,
                              detail: str) -> None:
        safe_detail = _safe_error_text(detail, "Push-событие не обработано")
        failures = int(row.get("attempt") or 0) + 1
        if failures >= WEBHOOK_MAX_ATTEMPTS:
            await self._finish_delivery(
                row.get("id"), token, "failed", None,
                "Повторы исчерпаны", safe_detail, failures,
            )
            await self._watch_delivery(row.get("watch_id"), "failed", safe_detail)
            return
        try:
            next_at = (
                datetime.now(timezone.utc)
                + timedelta(seconds=WEBHOOK_RETRY_SECONDS * max(1, failures))
            ).isoformat()
            await self._db(
                "retry_repository_hook_delivery",
                row.get("id"), token, safe_detail, next_at, failures,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to retry hook delivery: %s", type(exc).__name__)
        await self._watch_delivery(row.get("watch_id"), "retrying", safe_detail)

    async def _watch_delivery(self, watch_id: Optional[str], status: str,
                              error: Optional[str]) -> None:
        if not watch_id:
            return
        try:
            await self._db("mark_repository_watch_delivery", watch_id, status, error)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to mark watch delivery: %s", type(exc).__name__)

    # --- run dispatch and recovery ---------------------------------------
    def _schedule_dispatch(self, run_id: str, workspace_id: Optional[str] = None) -> None:
        async def runner() -> None:
            if workspace_id:
                with workspace_scope(workspace_id):
                    await self._dispatch_run(run_id)
            else:
                await self._dispatch_run(run_id)

        try:
            task = asyncio.create_task(runner())
        except RuntimeError:
            return  # нет running loop: recovery подхватит queued-строку
        self._dispatch_tasks.add(task)
        task.add_done_callback(self._dispatch_done)

    async def _dispatch_run(self, run_id: str) -> None:
        token = f"wrun_{uuid.uuid4().hex}"
        claimed = await self._db(
            "claim_repository_watch_dispatch", run_id, token, WATCH_CLAIM_SECONDS
        )
        if not claimed:
            return
        error: Optional[str] = None
        try:
            run = await self._db("get_run", run_id)
            if not run or run.get("execution_status") != "queued":
                return
            revision = await self._db("get_workflow_revision", run["revision_id"])
            if not revision or not revision.get("draft"):
                await self._db(
                    "update_run_status", run_id, "failed", "inconclusive",
                    None, datetime.now(timezone.utc).isoformat(),
                )
                error = "Ревизия сценария недоступна"
                if self.executor is not None:
                    await self.executor.notify_run_completed(run_id)
                return
            if not self.executor:
                error = "Прогон ещё не запущен"
                return
            draft = WorkflowDraft(**revision["draft"])
            await self.executor.execute_run(
                run_id=run_id,
                draft=draft,
                artifact_id=run.get("artifact_id"),
                trigger_type="repository_update",
                workflow_id=run["workflow_id"],
                revision_id=run["revision_id"],
            )
        except asyncio.CancelledError:
            # Не помечаем done: queued run должен остаться recoverable.
            error = "Прогон прерван"
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("Repository run dispatch failed: %s", type(exc).__name__)
            error = "Не удалось запустить прогон"
        finally:
            # Settlement looks at the actual run status: only a terminal run may
            # close the ledger as done. A still-queued run goes back to queued;
            # a started-but-not-terminal run stays running (no fake done).
            try:
                await self._settle_dispatch(run_id, token, error)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to finish repository dispatch: %s", type(exc).__name__
                )

    async def _settle_dispatch(self, run_id: str, token: str,
                               error: Optional[str]) -> None:
        run = await self._db("get_run", run_id)
        status = (run or {}).get("execution_status")
        if status == "queued":
            await self._db(
                "release_repository_watch_dispatch", run_id, token,
                error or "Прогон не запустился",
            )
            return
        if status == "running":
            await self._db(
                "interrupt_repository_watch_dispatch", run_id, token,
                error or "Прогон прерван до завершения",
            )
            return
        final_error = error
        if status == "failed" and not final_error:
            final_error = "Прогон завершился с ошибкой"
        await self._db("finish_repository_watch_dispatch", run_id, token, final_error)

    async def recover_queued_runs(self) -> None:
        await self._db("release_stale_repository_claims_server")
        rows = await self._db("recover_repository_watch_dispatches_server", 20)
        for row in rows:
            run_id = row.get("run_id")
            if run_id:
                self._schedule_dispatch(run_id, row.get("workspace_id"))

    def _dispatch_done(self, task: asyncio.Task) -> None:
        _discard_and_log(task, self._dispatch_tasks, "Repository dispatch")

    def _hook_done(self, task: asyncio.Task) -> None:
        _discard_and_log(task, self._hook_tasks, "Repository hook delivery")

    def pending_tasks(self) -> list[asyncio.Task]:
        """Snapshot tasks so shutdown can await them after cancelling."""
        return [*self._dispatch_tasks, *self._hook_tasks]

    def close(self) -> None:
        for task in list(self._dispatch_tasks):
            task.cancel()
        self._dispatch_tasks.clear()
        for task in list(self._hook_tasks):
            task.cancel()
        self._hook_tasks.clear()

    async def aclose(self) -> None:
        tasks = self.pending_tasks()
        self.close()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


class WorkflowScheduler:
    def __init__(self, db: DatabaseRepository, executor: Any = None):
        self.db = db
        self.executor = executor
        self.watches = RepositoryWatchService(db, executor)
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._watch_task: Optional[asyncio.Task] = None
        self._active_schedules: set[tuple[str, str]] = set()
        self._active_watch_checks: set[asyncio.Task] = set()
        self._schedule_tasks: set[asyncio.Task] = set()
        self._active_runs: set[str] = set()

    def _watch_check_done(self, task: asyncio.Task) -> None:
        _discard_and_log(task, self._active_watch_checks, "Repository watch check")

    def _schedule_done(self, task: asyncio.Task) -> None:
        _discard_and_log(task, self._schedule_tasks, "Scheduled run")

    def start(self) -> None:
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._scheduler_loop())
            self._watch_task = asyncio.create_task(self._watch_loop())

    def stop(self) -> None:
        """Signal shutdown without awaiting; ``aclose`` performs the wait."""
        self._running = False
        if self._task:
            self._task.cancel()
        if self._watch_task:
            self._watch_task.cancel()
        for task in list(self._active_watch_checks):
            task.cancel()
        self._active_watch_checks.clear()
        for task in list(self._schedule_tasks):
            task.cancel()
        self._schedule_tasks.clear()
        self.watches.close()

    async def aclose(self) -> None:
        """Cancel loops and wait for their tasks before the event loop closes."""
        tasks = [task for task in (self._task, self._watch_task) if task is not None]
        tasks.extend(self._active_watch_checks)
        tasks.extend(self._schedule_tasks)
        tasks.extend(self.watches.pending_tasks())
        self.stop()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._task = None
        self._watch_task = None
        self._active_watch_checks.clear()
        self._schedule_tasks.clear()
        self._active_schedules.clear()
        self._active_runs.clear()

    async def _scheduler_loop(self) -> None:
        while self._running:
            try:
                await self._check_and_trigger_schedules()
                await self._recover_schedule_runs()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in scheduler tick: {e}")
            await asyncio.sleep(5)  # Tick every 5 seconds

    async def _run_claimed_watch(self, watch: Dict[str, Any], token: str,
                                 workspace_id: Optional[str]) -> None:
        if workspace_id:
            with workspace_scope(workspace_id):
                await self.watches.check_claimed(watch, token)
        else:
            await self.watches.check_claimed(watch, token)

    async def _watch_loop(self) -> None:
        try:
            await self.watches.recover_queued_runs()
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            logger.error("Repository watch recovery failed: %s", type(exc).__name__)
        last_recovery = time.monotonic()
        while self._running:
            try:
                # Push-доставки не зависят от due-времени polling: тик сначала
                # добирает durable inbox, затем (если есть слоты) опрашивает.
                await self.watches.drain_hook_inbox()
                free = MAX_ACTIVE_WATCH_CHECKS - len(self._active_watch_checks)
                if free > 0:
                    token = f"wtick_{uuid.uuid4().hex}"
                    due = await _await_thread(
                        self.db.claim_due_repository_watches_server,
                        free, token, WATCH_CLAIM_SECONDS,
                    )
                    for watch in due:
                        workspace_id = watch.get("workspace_id")
                        task = asyncio.create_task(
                            self._run_claimed_watch(watch, token, workspace_id)
                        )
                        self._active_watch_checks.add(task)
                        task.add_done_callback(self._watch_check_done)
                if time.monotonic() - last_recovery >= WATCH_RECOVERY_SECONDS:
                    await self.watches.recover_queued_runs()
                    last_recovery = time.monotonic()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.error("Error in repository watch tick: %s", type(exc).__name__)
            await asyncio.sleep(WATCH_TICK_SECONDS)

    async def _check_and_trigger_schedules(self) -> None:
        """Claim due schedules from every tenant; one scope per schedule."""
        try:
            claimed = await _await_thread(
                self.db.claim_due_schedules_server,
                SCHEDULE_BATCH, SCHEDULE_CLAIM_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 - один тик не должен убивать loop
            logger.error("Schedule claim failed: %s", type(exc).__name__)
            return
        for sched in claimed:
            workspace_id = sched.get("workspace_id")
            if not workspace_id:
                continue
            key = (str(workspace_id), str(sched.get("id")))
            if (
                key in self._active_schedules
                or len(self._active_schedules) >= MAX_ACTIVE_SCHEDULE_RUNS
                or len(self._active_runs) >= MAX_ACTIVE_SCHEDULE_RUNS
            ):
                await self._advance_schedule(sched)
                continue
            self._active_schedules.add(key)
            task = asyncio.create_task(self._run_schedule_claim(sched, key))
            self._schedule_tasks.add(task)
            task.add_done_callback(self._schedule_done)

    async def _run_schedule_claim(self, sched: Dict[str, Any],
                                  key: tuple[str, str]) -> None:
        try:
            with workspace_scope(sched["workspace_id"]):
                await self._trigger_claimed_schedule(sched)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("Schedule execution failed: %s", type(exc).__name__)
            await self._advance_schedule(sched)
        finally:
            self._active_schedules.discard(key)

    async def _trigger_claimed_schedule(self, sched: Dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        try:
            workflow = await _await_thread(
                self.db.get_workflow, sched["workflow_id"]
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Schedule workflow read failed: %s", type(exc).__name__)
            await self._advance_schedule(sched)
            return
        if (
            not workflow
            or not workflow.get("draft")
            or not workflow.get("latest_revision_id")
        ):
            await self._advance_schedule(sched)
            return
        try:
            next_dt = next_fire_at(
                sched.get("cron_or_interval", "interval:60"), now
            )
        except Exception as exc:  # noqa: BLE001 - битое выражение не сеть
            logger.warning("Schedule expression rejected for %s: %s",
                           sched.get("id"), type(exc).__name__)
            await _await_thread(
                self.db.set_schedule_enabled, sched["id"], False
            )
            return
        run_id = str(uuid.uuid4())
        created = await _await_thread(
            self.db.create_scheduled_run,
            run_id,
            sched["id"],
            sched["workflow_id"],
            workflow["latest_revision_id"],
            sched["artifact_id"],
            now.isoformat(),
            next_dt.isoformat(),
        )
        if not created:
            return
        task = self._dispatch_scheduled_run(
            run_id,
            workflow["draft"],
            sched["artifact_id"],
            str(sched["workspace_id"]),
        )
        if task is not None:
            await task

    async def _advance_schedule(self, sched: Dict[str, Any]) -> None:
        """Release a lease without creating a run (overlap, busy, bad workflow)."""
        try:
            with workspace_scope(sched["workspace_id"]):
                now = datetime.now(timezone.utc)
                next_dt = next_fire_at(
                    sched.get("cron_or_interval", "interval:60"), now
                )
                await _await_thread(
                    self.db.update_schedule_run_time,
                    sched["id"],
                    sched.get("last_run_at") or "",
                    next_dt.isoformat(),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Schedule advance failed: %s", type(exc).__name__)
            try:
                with workspace_scope(sched["workspace_id"]):
                    await _await_thread(
                        self.db.set_schedule_enabled, sched["id"], False
                    )
            except Exception:
                pass

    def _dispatch_scheduled_run(self, run_id: str, draft_payload: Dict[str, Any],
                                artifact_id: str, workspace_id: str) -> Optional[asyncio.Task]:
        if run_id in self._active_runs or not self.executor:
            return
        self._active_runs.add(run_id)

        async def runner() -> None:
            try:
                with workspace_scope(workspace_id):
                    await self.executor.execute_run(
                        run_id=run_id,
                        draft=WorkflowDraft(**draft_payload),
                        artifact_id=artifact_id,
                        trigger_type="schedule",
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("Scheduled run failed: %s", type(exc).__name__)
            finally:
                self._active_runs.discard(run_id)

        task = asyncio.create_task(runner())
        self._schedule_tasks.add(task)
        task.add_done_callback(self._schedule_done)
        return task

    async def _recover_schedule_runs(self) -> None:
        """Re-dispatch queued schedule runs after a crash; claim is in executor."""
        try:
            rows = await _await_thread(
                self.db.list_queued_schedule_runs_server, 20, 5
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Schedule recovery query failed: %s", type(exc).__name__)
            return
        for row in rows:
            if len(self._active_runs) >= MAX_ACTIVE_SCHEDULE_RUNS:
                break
            run_id = row.get("id")
            workspace_id = row.get("workspace_id")
            if not run_id or not workspace_id or run_id in self._active_runs:
                continue
            self._active_runs.add(run_id)
            task = asyncio.create_task(
                self._recover_schedule_run(run_id, str(workspace_id))
            )
            self._schedule_tasks.add(task)
            task.add_done_callback(self._schedule_done)

    async def _recover_schedule_run(self, run_id: str, workspace_id: str) -> None:
        try:
            with workspace_scope(workspace_id):
                run = await _await_thread(self.db.get_run, run_id)
                if not run or run.get("execution_status") != "queued":
                    return
                revision = await _await_thread(
                    self.db.get_workflow_revision, run["revision_id"]
                )
                if not revision or not revision.get("draft"):
                    await _await_thread(
                        self.db.update_run_status, run_id, "failed",
                        "inconclusive", None,
                        datetime.now(timezone.utc).isoformat(),
                    )
                    if self.executor is not None:
                        await self.executor.notify_run_completed(run_id)
                    return
                if not self.executor:
                    return
                await self.executor.execute_run(
                    run_id=run_id,
                    draft=WorkflowDraft(**revision["draft"]),
                    artifact_id=run.get("artifact_id"),
                    trigger_type="schedule",
                    workflow_id=run["workflow_id"],
                    revision_id=run["revision_id"],
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("Schedule recovery run failed: %s", type(exc).__name__)
        finally:
            self._active_runs.discard(run_id)
