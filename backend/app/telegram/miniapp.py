"""HTTP API for the Telegram Mini App (``/api/telegram/mini``).

This is a separate, narrow surface: it does not use the same-origin cookie
session and never mints one. Authentication is always a scoped bearer token
issued by ``POST /auth`` after server-side initData verification; every route
re-checks the live Telegram binding and account.

Contract (shared with the frontend):

    POST /auth                       {initData} -> {token, expiresAt}
    POST /bind                       {initData, code} -> {token, expiresAt}
    GET  /bootstrap                  -> {user, settings, scenarios, runs}
    PUT  /settings                   -> {deliveryMode, scenario}
    POST /runs                       {repositoryUrl, scenarioId?} -> {runId}
    GET  /runs/{run_id}              -> {run, findings}
    GET  /runs/{run_id}/report.jsonc -> saved report bytes

The factory receives ``db``, ``executor`` and ``importer`` from the app root so
this module does not import the API router (no circular import). All DB reads
happen inside the binding workspace scope, which makes another workspace
indistinguishable from a missing row.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from backend.app.api.session import allow_attempt
from backend.app.artifacts.repository import RepositoryImportError
from backend.app.artifacts.intake import SafeIntakeError
from backend.app.contracts.schemas import RunSummary, WorkflowDraft
from backend.app.engine.executor import WorkflowExecutor
from backend.app.engine.validator import WorkflowValidator
from backend.app.reports.exports import (
    ReportNotFound,
    build_jsonc,
    load_export_bundle,
)
from backend.app.storage.database import DatabaseRepository
from backend.app.storage.telegram import (
    DELIVERY_MODES,
    Settings,
    SettingsOwnershipLost,
    TelegramRepository,
)
from backend.app.telegram.miniapp_auth import (
    INIT_DATA_MAX_BYTES,
    InitDataError,
    MiniAppAuth,
    MiniAppAuthError,
    MiniBindError,
    MiniConfigError,
    MiniIdentity,
    MiniNotBound,
    MiniTokenError,
    MiniTokenRevoked,
)
from backend.app.telegram.workspace import telegram_workspace_scope

logger = logging.getLogger("TelegramMiniApp")

MINI_PREFIX = "/api/telegram/mini"
MAX_AUTH_BODY_BYTES = 16 * 1024
MAX_BIND_CODE_LENGTH = 128
MIN_BIND_CODE_LENGTH = 8
MAX_REPOSITORY_URL = 2048
MAX_SCENARIO_ID = 128
RECENT_RUNS_LIMIT = 20
MAX_FINDINGS = 200
DEFAULT_MAX_CONCURRENT_RUNS = 2

Importer = Callable[[str], Awaitable[Dict[str, Any]]]


def _env_int(name: str, default: int, *, low: int, high: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(low, min(int(raw), high))
    except ValueError:
        return default


_RUN_CAPACITY = _env_int(
    "CG_MINIAPP_MAX_CONCURRENT_RUNS", DEFAULT_MAX_CONCURRENT_RUNS, low=1, high=8
)
_BIND_RATE_LIMIT = _env_int("CG_RATE_MINI_BIND_USER", 10, low=1, high=100)
_BIND_RATE_WINDOW = _env_int("CG_RATE_WINDOW_SECONDS", 60, low=1, high=3600)


def _fail(status_code: int, code: str, message: str) -> None:
    raise HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _auth_error(exc: Exception) -> None:
    if isinstance(exc, MiniConfigError):
        _fail(503, "TELEGRAM_MINIAPP_UNAVAILABLE", "Mini App временно недоступен: нет конфигурации бота")
    if isinstance(exc, MiniTokenRevoked):
        _fail(401, "TOKEN_REVOKED", "Привязка Telegram изменилась; откройте Mini App заново")
    if isinstance(exc, (MiniTokenError, MiniNotBound)):
        _fail(401, "INVALID_TOKEN", "Токен недействителен; откройте Mini App заново")
    _fail(401, "INVALID_TOKEN", "Токен недействителен; откройте Mini App заново")


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization")
    if not header:
        _fail(401, "AUTH_REQUIRED", "Требуется Authorization: Bearer <token>")
    scheme, separator, value = header.partition(" ")
    if not separator or scheme.lower() != "bearer" or not value or " " in value:
        _fail(401, "AUTH_REQUIRED", "Требуется Authorization: Bearer <token>")
    return value


def _run_summary(row: Dict[str, Any], *, last_event_seq: int = 0) -> Dict[str, Any]:
    """Project a runtime row onto the backend and frontend summary contracts.

    DB columns ``critical_count``/``high_count`` are not the model field names;
    the projection is explicit so a schema addition cannot leak by accident.
    """
    try:
        summary = RunSummary(
            id=str(row["id"]),
            workflow_id=str(row.get("workflow_id") or ""),
            revision_id=str(row.get("revision_id") or ""),
            artifact_id=(str(row["artifact_id"]) if row.get("artifact_id") else None),
            trigger_type=str(row.get("trigger_type") or "manual"),
            execution_status=str(row.get("execution_status") or "queued"),
            security_verdict=str(row.get("security_verdict") or "pending"),
            created_at=str(row.get("created_at") or ""),
            started_at=(str(row["started_at"]) if row.get("started_at") else None),
            finished_at=(str(row["finished_at"]) if row.get("finished_at") else None),
            total_findings=int(row.get("total_findings") or 0),
            critical_findings=int(row.get("critical_count") or 0),
            high_findings=int(row.get("high_count") or 0),
            last_event_seq=int(last_event_seq or 0),
        )
        counts = {
            f"{severity}_count": int(row.get(f"{severity}_count") or 0)
            for severity in ("critical", "high", "medium", "low", "info")
        }
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("mini run summary projection failed: %s", type(exc).__name__)
        _fail(500, "RUN_SUMMARY_UNAVAILABLE", "Не удалось прочитать состояние запуска")
    return {**summary.model_dump(), **counts}


def _settings_view(settings: Settings) -> Dict[str, Any]:
    return {
        "deliveryMode": settings.delivery_mode,
        "scenario": settings.scenario or None,
    }


def _validate_repository_url(value: Any) -> str:
    if not isinstance(value, str):
        _fail(400, "INVALID_REPOSITORY_URL", "repositoryUrl обязателен и должен быть строкой")
    url = value.strip()
    if not url:
        _fail(400, "INVALID_REPOSITORY_URL", "repositoryUrl обязателен")
    if len(url) > MAX_REPOSITORY_URL:
        _fail(400, "INVALID_REPOSITORY_URL", "Слишком длинный URL репозитория")
    if "\\" in url or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        _fail(400, "INVALID_REPOSITORY_URL", "URL репозитория содержит недопустимые символы")
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        _fail(400, "INVALID_REPOSITORY_URL", "Некорректный URL репозитория")
    if parts.scheme.lower() != "https":
        _fail(400, "INVALID_REPOSITORY_URL", "Разрешены только HTTPS-адреса репозиториев")
    if not host or parts.username is not None or parts.password is not None or port is not None:
        _fail(400, "INVALID_REPOSITORY_URL", "URL с логином, паролем или портом запрещён")
    if parts.query or parts.fragment:
        _fail(400, "INVALID_REPOSITORY_URL", "URL репозитория не должен содержать query или fragment")
    if not parts.path or parts.path == "/":
        _fail(400, "INVALID_REPOSITORY_URL", "Нужен корневой URL репозитория")
    return url


def _scenario_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(400, "INVALID_SCENARIO", "scenarioId должен быть непустой строкой")
    scenario = value.strip()
    if len(scenario) > MAX_SCENARIO_ID:
        _fail(400, "INVALID_SCENARIO", "scenarioId слишком длинный")
    return scenario


async def _json_body(request: Request, *, max_bytes: int) -> Dict[str, Any]:
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    if declared > max_bytes:
        _fail(413, "REQUEST_TOO_LARGE", "Тело запроса слишком большое")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_bytes:
            _fail(413, "REQUEST_TOO_LARGE", "Тело запроса слишком большое")
        body.extend(chunk)
    if not body:
        _fail(400, "INVALID_JSON", "Тело запроса должно быть JSON-объектом")
    try:
        payload = json.loads(body)
    except (TypeError, ValueError, RecursionError):
        _fail(400, "INVALID_JSON", "Тело запроса должно быть JSON-объектом")
    if not isinstance(payload, dict):
        _fail(400, "INVALID_JSON", "Тело запроса должно быть JSON-объектом")
    return payload


def create_miniapp_router(
    db: DatabaseRepository,
    executor: WorkflowExecutor,
    importer: Importer,
    *,
    repository: Optional[TelegramRepository] = None,
    auth: Optional[MiniAppAuth] = None,
) -> APIRouter:
    """Build the Mini App router. Root mounts it directly under ``/api``."""
    tasks: set[asyncio.Task] = set()
    run_slots: asyncio.Queue[None] = asyncio.Queue(maxsize=_RUN_CAPACITY)
    for _ in range(_RUN_CAPACITY):
        run_slots.put_nowait(None)
    stopping = False

    @asynccontextmanager
    async def lifespan(_app):
        nonlocal stopping
        stopping = False
        try:
            yield
        finally:
            stopping = True
            pending = tuple(tasks)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    router = APIRouter(prefix=MINI_PREFIX, tags=["telegram-mini"], lifespan=lifespan)
    repo = repository or TelegramRepository()
    auth_service = auth or MiniAppAuth(repo)
    validator = WorkflowValidator()

    async def settle_before_cancel(work: Awaitable[Any]) -> Any:
        pending = asyncio.ensure_future(work)
        cancelled = False
        # Import/intake and DB threads can outlive cancellation of their caller.
        while not pending.done():
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                cancelled = True
        result = pending.result()
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def finish_aborted_run(run_id: str, status: str) -> None:
        nonlocal stopping
        try:
            row = await asyncio.to_thread(db.get_run, run_id)
            if row and row.get("execution_status") in {"queued", "running"}:
                await asyncio.to_thread(
                    db.update_run_status, run_id, execution_status=status,
                    security_verdict="inconclusive",
                    finished_at=datetime.now(timezone.utc).isoformat(),
                )
        except Exception as exc:  # noqa: BLE001 - cleanup must preserve the original error
            stopping = True
            logger.error("mini run cleanup failed: %s", type(exc).__name__)

    def task_finished(task: asyncio.Task) -> None:
        tasks.discard(task)
        run_slots.put_nowait(None)
        if not task.cancelled():
            error = task.exception()
            if error is not None and not isinstance(error, HTTPException):
                logger.error("mini run task failed: %s", type(error).__name__)

    async def require_identity(request: Request) -> Tuple[MiniIdentity, str]:
        token = _bearer_token(request)
        try:
            identity = await asyncio.to_thread(auth_service.authorize, token)
        except asyncio.CancelledError:
            raise
        except MiniAppAuthError as exc:
            _auth_error(exc)
        except Exception as exc:  # noqa: BLE001 - no raw driver text in the response
            logger.warning("mini token check failed: %s", type(exc).__name__)
            _fail(503, "AUTH_UNAVAILABLE", "Не удалось проверить привязку; попробуйте позже")
        return identity, token

    async def assert_live(identity: MiniIdentity) -> MiniIdentity:
        try:
            return await asyncio.to_thread(auth_service.assert_live, identity)
        except asyncio.CancelledError:
            raise
        except MiniAppAuthError as exc:
            _auth_error(exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("mini live binding check failed: %s", type(exc).__name__)
            _fail(503, "AUTH_UNAVAILABLE", "Не удалось проверить привязку; попробуйте позже")

    def _workflow(scenario: str) -> Tuple[Dict[str, Any], WorkflowDraft, str]:
        """Load a scenario from the scoped workspace and validate its draft."""
        workflow = db.get_workflow(scenario)
        if not workflow or not isinstance(workflow.get("draft"), dict):
            _fail(400, "SCENARIO_NOT_FOUND", "Сценарий не найден в workspace")
        try:
            draft = WorkflowDraft(**workflow["draft"])
        except Exception:  # noqa: BLE001 - malformed stored draft is not trusted
            _fail(409, "SCENARIO_INVALID", "Сохранённый сценарий повреждён; выберите другой")
        result = validator.validate(draft)
        if not result.valid:
            message = next(
                (str(item.message) for item in result.issues if str(item.level) == "error"),
                "Сценарий не прошёл проверку",
            )
            _fail(400, "SCENARIO_INVALID", message[:300])
        revision_id = str(workflow.get("latest_revision_id") or "")
        if not revision_id:
            _fail(409, "SCENARIO_INVALID", "У сценария нет сохранённой ревизии")
        return workflow, draft, revision_id

    @router.post("/auth")
    async def exchange_init_data(request: Request) -> Dict[str, Any]:
        payload = await _json_body(request, max_bytes=MAX_AUTH_BODY_BYTES)
        init_data = payload.get("initData")
        if not isinstance(init_data, str) or not init_data:
            _fail(400, "INVALID_INIT_DATA", "initData обязателен")
        try:
            init_data_size = len(init_data.encode("utf-8"))
        except UnicodeEncodeError:
            _fail(400, "INVALID_INIT_DATA", "initData содержит некорректный Unicode")
        if init_data_size > INIT_DATA_MAX_BYTES:
            _fail(400, "INVALID_INIT_DATA", "initData слишком большой")
        try:
            _identity, expires_at, token = await asyncio.to_thread(
                auth_service.authenticate_init_data, init_data
            )
        except asyncio.CancelledError:
            raise
        except MiniConfigError:
            _fail(503, "TELEGRAM_MINIAPP_UNAVAILABLE", "Mini App временно недоступен: нет конфигурации бота")
        except MiniNotBound:
            _fail(
                409,
                "TELEGRAM_NOT_BOUND",
                "Telegram ещё не привязан к аккаунту. Откройте веб-кабинет, раздел Telegram, "
                "создайте код привязки, отправьте боту /start, а затем код обычным сообщением. "
                "В Mini App этот же код можно ввести здесь.",
            )
        except InitDataError:
            _fail(401, "INVALID_INIT_DATA", "initData не прошёл проверку; откройте Mini App заново")
        except Exception as exc:  # noqa: BLE001 - no raw initData/token in errors
            logger.warning("mini auth failed: %s", type(exc).__name__)
            _fail(503, "AUTH_UNAVAILABLE", "Не удалось проверить привязку; попробуйте позже")
        return {"token": token, "expiresAt": int(expires_at)}

    @router.post("/bind")
    async def bind_account(request: Request) -> Dict[str, Any]:
        """Bind this verified private chat with a cabinet code, then issue token.

        The code is accepted only in the JSON body. Signature/freshness and the
        private-chat/user checks are identical to /auth; no token is minted
        until ``consume_binding_code`` succeeded and the bound account is
        active in that workspace.
        """
        payload = await _json_body(request, max_bytes=MAX_AUTH_BODY_BYTES)
        init_data = payload.get("initData")
        if not isinstance(init_data, str) or not init_data:
            _fail(400, "INVALID_INIT_DATA", "initData обязателен")
        try:
            init_data_size = len(init_data.encode("utf-8"))
        except UnicodeEncodeError:
            _fail(400, "INVALID_INIT_DATA", "initData содержит некорректный Unicode")
        if init_data_size > INIT_DATA_MAX_BYTES:
            _fail(400, "INVALID_INIT_DATA", "initData слишком большой")

        try:
            user = await asyncio.to_thread(auth_service.verify_init_data, init_data)
        except asyncio.CancelledError:
            raise
        except MiniConfigError:
            _fail(503, "TELEGRAM_MINIAPP_UNAVAILABLE", "Mini App временно недоступен: нет конфигурации бота")
        except InitDataError:
            _fail(401, "INVALID_INIT_DATA", "initData не прошёл проверку; откройте Mini App заново")
        except Exception as exc:  # noqa: BLE001 - initData/code never logged
            logger.warning("mini bind verification failed: %s", type(exc).__name__)
            _fail(503, "AUTH_UNAVAILABLE", "Не удалось проверить привязку; попробуйте позже")

        # The outer middleware already limits each client IP for /bind. This
        # second window is per verified Telegram user, so rotating IPs cannot
        # bypass one account's quota.
        if not allow_attempt(
            f"telegram:{user.id}",
            "mini_bind_user",
            limit=_BIND_RATE_LIMIT,
            window_seconds=_BIND_RATE_WINDOW,
        ):
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "RATE_LIMITED",
                    "message": "Слишком много попыток привязки; попробуйте позже",
                },
                headers={"Retry-After": str(_BIND_RATE_WINDOW)},
            )

        code = payload.get("code")
        if not isinstance(code, str) or not (
            MIN_BIND_CODE_LENGTH <= len(code.strip()) <= MAX_BIND_CODE_LENGTH
        ):
            _fail(400, "INVALID_BINDING_CODE", "Код привязки недействителен или истёк")

        try:
            _identity, expires_at, token = await asyncio.to_thread(
                auth_service.bind_with_code, user, code
            )
        except asyncio.CancelledError:
            raise
        except MiniConfigError:
            _fail(503, "TELEGRAM_MINIAPP_UNAVAILABLE", "Mini App временно недоступен: нет конфигурации бота")
        except MiniNotBound:
            _fail(
                409,
                "TELEGRAM_NOT_BOUND",
                "Код принят, но аккаунт привязки отключён или его workspace недоступен. "
                "Войдите в веб-кабинет и создайте новый код.",
            )
        except MiniBindError:
            _fail(400, "INVALID_BINDING_CODE", "Код привязки недействителен или истёк")
        except Exception as exc:  # noqa: BLE001 - no code/exception text leak
            logger.warning("mini bind failed: %s", type(exc).__name__)
            _fail(500, "BIND_UNAVAILABLE", "Не удалось выполнить привязку; попробуйте позже")
        return {"token": token, "expiresAt": int(expires_at)}

    @router.get("/bootstrap")
    async def bootstrap(request: Request) -> Dict[str, Any]:
        identity, _token = await require_identity(request)
        try:
            with telegram_workspace_scope(identity.workspace_id):
                settings = await asyncio.to_thread(repo.get_settings, identity.chat_id)
                workflows = await asyncio.to_thread(db.list_workflows)
                runs = await asyncio.to_thread(db.list_runs, RECENT_RUNS_LIMIT)
            await assert_live(identity)
        except HTTPException:
            raise
        scenarios = [
            {"id": str(item.get("id")), "name": str(item.get("name") or "")}
            for item in workflows
            if item.get("id")
        ]
        return {
            "user": {"displayName": identity.display_name},
            "settings": _settings_view(settings),
            "scenarios": scenarios,
            "runs": [_run_summary(item) for item in runs[:RECENT_RUNS_LIMIT]],
        }

    @router.put("/settings")
    async def update_settings(request: Request) -> Dict[str, Any]:
        identity, _token = await require_identity(request)
        payload = await _json_body(request, max_bytes=8 * 1024)

        delivery_mode: Optional[str] = None
        if "deliveryMode" in payload:
            value = payload["deliveryMode"]
            if not isinstance(value, str) or value not in DELIVERY_MODES:
                _fail(400, "INVALID_SETTINGS", "deliveryMode должен быть alerts, jsonc или both")
            delivery_mode = value

        scenario: Optional[str] = None
        if "scenario" in payload:
            value = payload["scenario"]
            if value is not None and not isinstance(value, str):
                _fail(400, "INVALID_SETTINGS", "scenario должен быть строкой или null")
            # Explicit null clears the saved scenario; an absent key keeps it.
            scenario = "" if value is None else value.strip()
            if len(scenario) > MAX_SCENARIO_ID:
                _fail(400, "INVALID_SETTINGS", "scenario слишком длинный")

        try:
            with telegram_workspace_scope(identity.workspace_id):
                if scenario:
                    await asyncio.to_thread(_workflow, scenario)
                await assert_live(identity)
                settings = await asyncio.to_thread(
                    repo.set_settings,
                    identity.chat_id,
                    user_id=identity.user_id,
                    workspace_id=identity.workspace_id,
                    binding_epoch=identity.binding_epoch,
                    delivery_mode=delivery_mode,
                    scenario=scenario,
                )
        except HTTPException:
            raise
        except SettingsOwnershipLost:
            _fail(409, "TELEGRAM_REBOUND", "Привязка Telegram изменилась; откройте Mini App заново")
        except ValueError as exc:
            _fail(400, "INVALID_SETTINGS", str(exc)[:300])
        return _settings_view(settings)

    @router.post("/runs")
    async def start_run(request: Request) -> JSONResponse:
        identity, _token = await require_identity(request)
        payload = await _json_body(request, max_bytes=8 * 1024)
        repository_url = _validate_repository_url(payload.get("repositoryUrl"))

        if stopping:
            _fail(503, "RUNS_UNAVAILABLE", "Сервис завершает работу; попробуйте позже")
        try:
            run_slots.get_nowait()
        except asyncio.QueueEmpty:
            raise HTTPException(
                status_code=429,
                detail={"code": "RUN_CAPACITY_REACHED", "message": "Все места для запусков заняты; попробуйте позже"},
                headers={"Retry-After": "5"},
            )
        ready = asyncio.get_running_loop().create_future()

        async def start_and_execute() -> None:
            run_id: Optional[str] = None
            try:
                with telegram_workspace_scope(identity.workspace_id):
                    settings = await asyncio.to_thread(repo.get_settings, identity.chat_id)
                    if payload.get("scenarioId") is not None:
                        scenario = _scenario_id(payload.get("scenarioId"))
                    else:
                        scenario = settings.scenario or ""
                    if not scenario:
                        _fail(
                            409,
                            "SCENARIO_REQUIRED",
                            "Сценарий не выбран. Выберите его в настройках или передайте scenarioId.",
                        )
                    _workflow_row, draft, revision_id = await asyncio.to_thread(
                        _workflow, scenario
                    )

                    # Re-check before the only outbound network step. If the chat
                    # was rebound meanwhile, this request must not import anything.
                    await assert_live(identity)
                    try:
                        manifest = await settle_before_cancel(importer(repository_url))
                    except RepositoryImportError as exc:
                        _fail(exc.status_code, "REPOSITORY_IMPORT_FAILED", exc.message)
                    except SafeIntakeError:
                        _fail(400, "REPOSITORY_IMPORT_FAILED", "Архив репозитория не прошёл проверку")
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - no raw URL/exception leak
                        logger.warning("mini repository import failed: %s", type(exc).__name__)
                        _fail(502, "REPOSITORY_IMPORT_FAILED", "Не удалось скачать репозиторий; попробуйте позже")

                    # Re-check after the network step and before any persisted
                    # run/executor work for the old binding.
                    await assert_live(identity)
                    try:
                        await settle_before_cancel(asyncio.to_thread(db.save_artifact, manifest))
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("mini artifact persist failed: %s", type(exc).__name__)
                        _fail(500, "ARTIFACT_SAVE_FAILED", "Не удалось сохранить артефакт репозитория")

                    artifact_id = str(manifest.get("id") or "")
                    if not artifact_id:
                        _fail(502, "REPOSITORY_IMPORT_FAILED", "Импорт не вернул артефакт")

                    run_id = str(uuid.uuid4())
                    try:
                        await settle_before_cancel(asyncio.to_thread(
                            db.create_run,
                            run_id=run_id,
                            workflow_id=scenario,
                            revision_id=revision_id,
                            artifact_id=artifact_id,
                            trigger_type="miniapp",
                        ))
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("mini run create failed: %s", type(exc).__name__)
                        _fail(500, "RUN_START_FAILED", "Не удалось создать запуск")

                    await assert_live(identity)
                    ready.set_result(run_id)
                    await executor.execute_run(
                        run_id=run_id, draft=draft, artifact_id=artifact_id,
                        trigger_type="miniapp", workflow_id=scenario, revision_id=revision_id,
                    )
            except (Exception, asyncio.CancelledError) as exc:
                if run_id is not None:
                    with telegram_workspace_scope(identity.workspace_id):
                        cleanup = asyncio.create_task(finish_aborted_run(
                            run_id, "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
                        ))
                        while not cleanup.done():
                            try:
                                await asyncio.shield(cleanup)
                            except asyncio.CancelledError:
                                pass
                        cleanup.result()
                if not ready.done():
                    if isinstance(exc, asyncio.CancelledError):
                        ready.cancel()
                    else:
                        ready.set_exception(exc)
                raise

        task = asyncio.create_task(start_and_execute(), name=f"miniapp-run-{uuid.uuid4()}")
        tasks.add(task)
        task.add_done_callback(task_finished)

        def cancel_waiter(done: asyncio.Task) -> None:
            if done.cancelled() and not ready.done():
                ready.cancel()

        task.add_done_callback(cancel_waiter)
        try:
            run_id = await asyncio.shield(ready)
        except asyncio.CancelledError:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if ready.done() and not ready.cancelled():
                ready.exception()
            raise
        return JSONResponse({"runId": run_id}, status_code=202)

    @router.get("/runs/{run_id}")
    async def run_detail(run_id: str, request: Request) -> Dict[str, Any]:
        identity, _token = await require_identity(request)
        with telegram_workspace_scope(identity.workspace_id):
            run = await asyncio.to_thread(db.get_run, run_id)
            if not run:
                _fail(404, "RUN_NOT_FOUND", "Запуск не найден")
            try:
                bundle = await asyncio.to_thread(load_export_bundle, db, run_id)
            except ReportNotFound:
                _fail(404, "RUN_NOT_FOUND", "Запуск не найден")
            except Exception as exc:  # noqa: BLE001
                logger.warning("mini run detail failed: %s", type(exc).__name__)
                _fail(500, "RUN_UNAVAILABLE", "Не удалось прочитать запуск")
            await assert_live(identity)
        return {
            "run": _run_summary(run, last_event_seq=int(run.get("last_event_seq") or 0)),
            "findings": bundle.findings[:MAX_FINDINGS],
        }

    @router.get("/runs/{run_id}/report.jsonc")
    async def run_report(run_id: str, request: Request) -> Response:
        identity, _token = await require_identity(request)
        with telegram_workspace_scope(identity.workspace_id):
            report = await asyncio.to_thread(db.get_report, run_id)
            if not report:
                _fail(404, "REPORT_NOT_FOUND", "Отчёт для этого запуска ещё не сформирован")
            try:
                bundle = await asyncio.to_thread(load_export_bundle, db, run_id)
                content = build_jsonc(bundle)
            except ReportNotFound:
                _fail(404, "REPORT_NOT_FOUND", "Отчёт для этого запуска ещё не сформирован")
            except Exception as exc:  # noqa: BLE001
                logger.warning("mini report build failed: %s", type(exc).__name__)
                _fail(500, "REPORT_UNAVAILABLE", "Не удалось собрать отчёт")
            await assert_live(identity)
        return Response(
            content=content,
            media_type="application/json",
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": f'inline; filename="nedra-{run_id}.jsonc"',
            },
        )

    return router
