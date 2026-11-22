"""Приём push-событий GitHub/GitLab.

Единственный публичный API без cookie/Origin: POST /api/repository-hooks/{id}.
Маршрут всё равно проверяет credential провайдера по сырым байтам тела до
разбора JSON. Валидное событие сначала durable-записывается в inbox, ответ
202 не ждёт скачивания репозитория. Секрет и тело наружу/в логи не попадают.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from backend.app.artifacts.repository import RepositoryImportError, _parse_repository_url
from backend.app.codefacts.identity import workspace_scope


logger = logging.getLogger("RepositoryHooks")

MAX_BODY_BYTES = 1024 * 1024
MAX_CONCURRENCY = 4
REQUEST_TIMEOUT_SECONDS = 15
_WATCH_ID_RE = re.compile(r"^rw_[A-Za-z0-9]{1,40}$")
_DELIVERY_RE = re.compile(r"^[A-Za-z0-9-]{1,128}$")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_ZERO_SHA_RE = re.compile(r"^0+$")
_HOOK_SEM = asyncio.Semaphore(MAX_CONCURRENCY)


class WebhookKeyUnavailable(RuntimeError):
    """Нет стабильного серверного ключа: секрет нельзя выпустить."""


def webhook_key() -> Optional[bytes]:
    for name in ("CG_REPOSITORY_WEBHOOK_KEY", "CG_SESSION_SECRET"):
        value = os.getenv(name, "").strip()
        if value:
            return value.encode("utf-8")
    return None


def webhook_key_available() -> bool:
    return webhook_key() is not None


def derive_webhook_secret(workspace: str, watch_id: str, nonce: str) -> str:
    """Purpose-separated HMAC; plaintext секрет в БД не пишется."""
    key = webhook_key()
    if key is None:
        raise WebhookKeyUnavailable(
            "Серверный ключ вебхуков не задан (CG_REPOSITORY_WEBHOOK_KEY или CG_SESSION_SECRET)"
        )
    message = f"repository-webhook:v1:{workspace}:{watch_id}:{nonce}".encode("utf-8")
    return "whsec_" + hmac.new(key, message, hashlib.sha256).hexdigest()


def _canonical_repo_url(value: Any) -> Optional[str]:
    try:
        target = _parse_repository_url(value)
    except RepositoryImportError:
        return None
    return target.url.lower()


def _delivery_id(provider: str, request: Request, body: bytes) -> Optional[str]:
    if provider == "github":
        value = request.headers.get("x-github-delivery", "").strip()
        return value if _DELIVERY_RE.fullmatch(value) else None
    # GitLab присылает один и тот же Webhook-UUID на все доставки hook-а,
    # поэтому он не годится. UUID события уникален, digest тела — детерминирован.
    event_uuid = request.headers.get("x-gitlab-event-uuid", "").strip()
    if _UUID_RE.fullmatch(event_uuid):
        return event_uuid.lower()
    return hashlib.sha256(body).hexdigest()[:40]


def _authenticate(provider: str, secret: str, request: Request,
                  body: bytes) -> bool:
    # compare_digest на str падает TypeError на non-ASCII. Сравниваем байты,
    # поэтому мусорный заголовок получает честный 401, а не 500.
    if provider == "github":
        received = request.headers.get("x-hub-signature-256", "").strip()
        expected = "sha256=" + hmac.new(
            secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        return bool(received) and hmac.compare_digest(
            received.encode("utf-8", "ignore"), expected.encode("ascii")
        )
    received = request.headers.get("x-gitlab-token", "").strip()
    return bool(received) and hmac.compare_digest(
        received.encode("utf-8", "ignore"), secret.encode("utf-8")
    )


async def _read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Тело вебхука слишком большое")
    total = 0
    chunks = []
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Тело вебхука слишком большое")
        chunks.append(chunk)
    return b"".join(chunks)


def _ignored(detail: str, provider: str, delivery_id: Optional[str] = None) -> JSONResponse:
    body: Dict[str, Any] = {"status": "ignored", "detail": detail, "provider": provider}
    if delivery_id:
        body["deliveryId"] = delivery_id
    return JSONResponse(body, status_code=202, headers={"Cache-Control": "no-store"})


def _bad_payload(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


class _HookIgnored(Exception):
    def __init__(self, detail: str, provider: str):
        super().__init__(detail)
        self.detail = detail
        self.provider = provider


def _parse_event(provider: str, event: str, payload: Any,
                 watch: Dict[str, Any]) -> Tuple[str, str, str]:
    if event in {"pull_request", "merge_request"}:
        return _parse_review_request(provider, payload, watch)
    return _parse_push(provider, payload, watch)


def _parse_review_request(provider: str, payload: Any,
                          watch: Dict[str, Any]) -> Tuple[str, str, str]:
    """Return (head sha, source ref, canonical_repo) for a PR/MR event."""
    if not isinstance(payload, dict):
        raise _bad_payload("Тело вебхука должно быть JSON-объектом")
    if provider == "github":
        action = payload.get("action")
        if action not in {"opened", "reopened", "synchronize"}:
            raise _HookIgnored("Действие pull request не требует проверки", provider)
        request_data = payload.get("pull_request") or {}
        repository = payload.get("repository") or {}
        repo_url = repository.get("html_url") if isinstance(repository, dict) else None
        base = ((request_data.get("base") or {}).get("ref"))
        head = request_data.get("head") or {}
        sha = head.get("sha")
        branch = head.get("ref")
    else:
        action = (payload.get("object_attributes") or {}).get("action")
        if action not in {"open", "reopen", "update"}:
            raise _HookIgnored("Действие merge request не требует проверки", provider)
        attrs = payload.get("object_attributes") or {}
        project = payload.get("project") or {}
        repo_url = project.get("web_url") if isinstance(project, dict) else None
        base = attrs.get("target_branch")
        sha = (attrs.get("last_commit") or {}).get("id")
        branch = attrs.get("source_branch")
    canonical = _canonical_repo_url(repo_url)
    if canonical is None:
        raise _bad_payload("В событии нет корректного адреса репозитория")
    watch_repo = _canonical_repo_url(watch.get("url"))
    if canonical != watch_repo:
        raise _HookIgnored("Другой репозиторий", provider)
    if base != watch.get("branch"):
        raise _HookIgnored("Pull request направлен в другую ветку", provider)
    if not isinstance(sha, str) or not _SHA_RE.fullmatch(sha):
        raise _bad_payload("Некорректный SHA head-коммита")
    ref = f"refs/heads/{branch}" if isinstance(branch, str) and branch else ""
    return sha, ref, canonical


def _parse_push(provider: str, payload: Any, watch: Dict[str, Any]
                ) -> Tuple[str, str, str]:
    """Return (commit_sha, ref, canonical_repo) or raise HookIgnored."""
    if not isinstance(payload, dict):
        raise _bad_payload("Тело вебхука должно быть JSON-объектом")
    if provider == "github":
        repository = payload.get("repository")
        repo_url = repository.get("html_url") if isinstance(repository, dict) else None
        ref = payload.get("ref")
        after = payload.get("after")
        deleted = payload.get("deleted") is True
    else:
        project = payload.get("project")
        repo_url = project.get("web_url") if isinstance(project, dict) else None
        ref = payload.get("ref")
        after = payload.get("after")
        deleted = False
    canonical = _canonical_repo_url(repo_url)
    if canonical is None:
        raise _bad_payload("В событии нет корректного адреса репозитория")
    watch_repo = _canonical_repo_url(watch.get("url"))
    if canonical != watch_repo:
        raise _HookIgnored("Другой репозиторий", provider)
    if not isinstance(ref, str):
        raise _bad_payload("В событии нет ref")
    if ref != f"refs/heads/{watch['branch']}":
        raise _HookIgnored("Другая ветка или тег", provider)
    if deleted or (isinstance(after, str) and _ZERO_SHA_RE.fullmatch(after)):
        raise _HookIgnored("Ветка удалена", provider)
    if not isinstance(after, str) or not _SHA_RE.fullmatch(after):
        raise _bad_payload("Некорректный SHA после push")
    return after, ref, canonical


async def _handle_hook(db: Any, watch_id: str,
                       request: Request) -> JSONResponse:
    """Resolve tenant by opaque watch id, then scope everything before effects."""
    body = await _read_body(request)
    try:
        matches = await asyncio.to_thread(
            db.find_repository_watches_by_id_server, watch_id
        )
    except Exception as exc:  # noqa: BLE001 - DSN/текст драйвера не наружу
        logger.warning("Repository hook lookup failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Хранилище недоступно") from None
    if len(matches) != 1:
        # Амбициозный/несуществующий id не должен угадывать tenant.
        raise HTTPException(status_code=404, detail="Вебхук не найден")
    watch = matches[0]
    workspace = watch.get("workspace_id")
    if not workspace:
        raise HTTPException(status_code=404, detail="Вебхук не найден")
    with workspace_scope(str(workspace)):
        return await _handle_scoped_hook(db, watch, str(workspace), body, request)


async def _handle_scoped_hook(db: Any, watch: Dict[str, Any], workspace: str,
                              body: bytes, request: Request) -> JSONResponse:
    watch_id = watch["id"]
    if watch.get("trigger_mode") != "webhook" or not watch.get("webhook_nonce"):
        raise HTTPException(status_code=409, detail="Вебхук для расписания не настроен")
    if not webhook_key_available():
        raise HTTPException(status_code=503, detail="Серверный ключ вебхуков не задан")
    provider = watch.get("provider")
    if provider not in ("github", "gitlab"):
        raise HTTPException(status_code=409, detail="Провайдер расписания не поддержан")
    secret = derive_webhook_secret(workspace, watch_id, watch["webhook_nonce"])

    if not _authenticate(provider, secret, request, body):
        raise HTTPException(status_code=401, detail="Неверный credential вебхука")

    event = request.headers.get(
        "x-github-event" if provider == "github" else "x-gitlab-event", ""
    ).strip().lower()
    if provider == "github":
        if event == "ping":
            return _ignored("Проверка подключения (ping)", provider)
        if event not in {"push", "pull_request"}:
            return _ignored("Событие не является push или pull request", provider)
    elif event and event not in {"push hook", "merge request"}:
        return _ignored("Событие не является push или merge request", provider)

    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        raise _bad_payload("Тело вебхука не является JSON") from None
    if provider == "gitlab" and isinstance(payload, dict):
        kind = payload.get("object_kind")
        if kind not in {"push", "merge_request"}:
            return _ignored("Событие не является push или merge request", provider)
        event = "push" if kind == "push" else "merge_request"
    elif provider == "github":
        event = "push" if event == "push" else "pull_request"

    if not watch.get("enabled"):
        return _ignored("Отслеживание приостановлено", provider)

    delivery_id = _delivery_id(provider, request, body)
    if delivery_id is None:
        raise _bad_payload("Некорректный идентификатор доставки")
    try:
        commit_sha, ref, _ = _parse_event(provider, event, payload, watch)
    except _HookIgnored as exc:
        return _ignored(exc.detail, provider, delivery_id)
    except HTTPException:
        raise

    workflow = await asyncio.to_thread(db.get_workflow, watch["workflow_id"])
    revision_id = (workflow or {}).get("latest_revision_id")
    if not workflow or not revision_id or not workflow.get("draft"):
        raise HTTPException(status_code=409, detail="У сценария нет сохранённой ревизии")

    now = datetime.now(timezone.utc).isoformat()
    row_id = f"whd_{uuid.uuid4().hex[:16]}"
    try:
        inserted = await asyncio.to_thread(
            db.enqueue_repository_hook_delivery,
            row_id, watch_id, provider, delivery_id, commit_sha, ref,
            revision_id, now,
        )
        if not inserted:
            ledger = await asyncio.to_thread(
                db.get_repository_watch_commit, watch_id, commit_sha
            )
            await asyncio.to_thread(
                db.mark_repository_watch_delivery, watch_id, "duplicate"
            )
            response: Dict[str, Any] = {
                "status": "duplicate", "provider": provider,
                "deliveryId": delivery_id,
            }
            if ledger and ledger.get("run_id"):
                response["runId"] = ledger["run_id"]
            return JSONResponse(
                response, status_code=202,
                headers={"Cache-Control": "no-store"},
            )
        await asyncio.to_thread(
            db.mark_repository_watch_delivery, watch_id, "accepted"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Repository hook enqueue failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Не удалось сохранить событие") from None
    return JSONResponse(
        {"status": "accepted", "provider": provider, "deliveryId": delivery_id},
        status_code=202,
        headers={"Cache-Control": "no-store"},
    )


def create_repository_hook_router(db: Any) -> APIRouter:
    router = APIRouter()

    @router.post("/repository-hooks/{watch_id}")
    async def receive_repository_hook(watch_id: str, request: Request) -> JSONResponse:
        if not _WATCH_ID_RE.fullmatch(watch_id):
            raise HTTPException(status_code=404, detail="Вебхук не найден")
        try:
            # Слот берём до чтения тела и DB: неаутентифицированный поток не
            # может раздувать память/соединения сверх MAX_CONCURRENCY.
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                async with _HOOK_SEM:
                    return await _handle_hook(db, watch_id, request)
        except TimeoutError:
            logger.warning("Repository hook timed out")
            raise HTTPException(
                status_code=408, detail="Превышено время обработки вебхука"
            ) from None

    @router.get("/repository-hooks/{watch_id}/ci-template")
    def repository_hook_ci_template(watch_id: str) -> JSONResponse:
        if not _WATCH_ID_RE.fullmatch(watch_id):
            raise HTTPException(status_code=404, detail="Вебхук не найден")
        content = "\n".join([
            "name: nedra-security",
            "on:",
            "  pull_request:",
            "    branches: [main]",
            "jobs:",
            "  nedra:",
            "    runs-on: ubuntu-latest",
            "    steps:",
            "      - name: Trigger NEDRA security workflow",
            "        env:",
            "          NEDRA_HOOK_URL: ${{ secrets.NEDRA_HOOK_URL }}",
            "          NEDRA_HOOK_SECRET: ${{ secrets.NEDRA_HOOK_SECRET }}",
            "        run: |",
            "          body=$(printf '{\"ref\":\"%s\"}' \"$GITHUB_REF\")",
            "          sig=$(printf '%s' \"$body\" | openssl dgst -sha256 -hmac \"$NEDRA_HOOK_SECRET\" | sed 's/^.* //')",
            "          curl --fail --silent --show-error -X POST \"$NEDRA_HOOK_URL\" \\",
            "            -H 'Content-Type: application/json' \\",
            "            -H 'X-GitHub-Event: pull_request' \\",
            "            -H \"X-Hub-Signature-256: sha256=$sig\" \\",
            "            --data \"$body\"",
            "",
        ])
        return JSONResponse({
            "filename": "nedra-security.yml",
            "watch_id": watch_id,
            "content": content,
            "note": "Подставьте NEDRA_HOOK_URL и NEDRA_HOOK_SECRET в GitHub Actions secrets.",
        })

    return router
