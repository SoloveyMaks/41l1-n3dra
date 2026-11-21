"""Read-only responsibility enrichment for one saved finding."""

from __future__ import annotations

import asyncio
import contextvars
from typing import Any, Dict, Optional
from pathlib import PurePosixPath

from backend.app.codefacts.identity import workspace_id
from backend.app.codefacts.source import is_safe_source_path
from backend.app.responsibility.codeowners import load_and_match_codeowners
from backend.app.responsibility.history import fetch_file_history, parse_repository_target
from backend.app.responsibility.models import (
    CodeownersInfo,
    ResponsibilityResponse,
    ResponsibilityScope,
    RoleSuggestion,
)


RESPONSIBILITY_DEADLINE_SECONDS = 10.0
_MAX_ACTIVE_RESPONSIBILITY = 4
_MAX_QUEUED_RESPONSIBILITY = 8
_INFLIGHT: Dict[str, asyncio.Task] = {}
_REGISTRY_LOCK = asyncio.Lock()


class ResponsibilityNotFound(Exception):
    pass


class ResponsibilityBusy(Exception):
    pass


class _Admission:
    """Bounded active+queue admission for real (sync) responsibility work."""

    def __init__(self) -> None:
        self.active = 0
        self.waiting = 0
        self.lock = asyncio.Lock()
        self.changed = asyncio.Condition(self.lock)

    async def reserve(self) -> bool:
        """Reserve an active slot, or a bounded queue place.

        Returns True when active immediately, False when queued. Raises
        ResponsibilityBusy without creating a task when both are full.
        """
        async with self.changed:
            if self.active < _MAX_ACTIVE_RESPONSIBILITY:
                self.active += 1
                return True
            if self.waiting >= _MAX_QUEUED_RESPONSIBILITY:
                raise ResponsibilityBusy("Слишком много одновременных запросов ответственности.")
            self.waiting += 1
            return False

    async def activate(self, deadline: float) -> None:
        async with self.changed:
            if self.active < _MAX_ACTIVE_RESPONSIBILITY:
                if self.waiting > 0:
                    self.waiting -= 1
                self.active += 1
                return
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                self.waiting -= 1
                raise asyncio.TimeoutError
            try:
                await asyncio.wait_for(
                    self.changed.wait_for(
                        lambda: self.active < _MAX_ACTIVE_RESPONSIBILITY
                    ),
                    timeout=remaining,
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self.waiting -= 1
                raise
            self.waiting -= 1
            self.active += 1

    async def cancel_wait(self) -> None:
        async with self.changed:
            if self.waiting > 0:
                self.waiting -= 1
            self.changed.notify()

    async def release(self) -> None:
        async with self.changed:
            if self.active > 0:
                self.active -= 1
            self.changed.notify()


_ADMISSION = _Admission()


class _AdmissionSlot:
    def __init__(self) -> None:
        self.active = False
        self.queued = False
        self.released = False
        self.sync_future: Optional[asyncio.Future] = None
        self.loop = asyncio.get_running_loop()

    async def activate(self, deadline: float) -> None:
        if self.active:
            return
        await _ADMISSION.activate(deadline)
        self.queued = False
        self.active = True

    def defer_or_release(self) -> None:
        if self.released:
            return
        if not self.active:
            if self.queued:
                self.released = True
                self._schedule(self._cancel_wait)
            return
        self.released = True
        sync_future = self.sync_future
        if sync_future is not None and not sync_future.done():
            # A cancelled/timed-out caller must not free a slot while the
            # thread is still inside PG/CPU. Release only after actual end.
            sync_future.add_done_callback(lambda _future: self._schedule_release())
        else:
            self._schedule_release()

    def _schedule_release(self) -> None:
        self._schedule(_ADMISSION.release)

    def _schedule(self, coroutine_factory: Any) -> None:
        def run() -> None:
            try:
                asyncio.create_task(coroutine_factory())
            except RuntimeError:
                pass  # loop is shutting down

        try:
            self.loop.call_soon_threadsafe(run)
        except RuntimeError:
            pass

    async def _cancel_wait(self) -> None:
        await _ADMISSION.cancel_wait()


def _module_name(path: str) -> str:
    parts = PurePosixPath(path).parts
    if "kotlin" in parts:
        index = len(parts) - 1 - list(reversed(parts)).index("kotlin")
        remainder = parts[index + 1 :]
        if len(remainder) >= 2:
            return remainder[-2]
        if remainder:
            return remainder[0]
    if len(parts) >= 2:
        return parts[-2]
    return "приложение"


def _suggest_role(finding: Dict[str, Any], path: str, codeowners: CodeownersInfo) -> RoleSuggestion:
    analyzer = str(finding.get("analyzer") or "")
    rule_id = str(finding.get("ruleId") or "")
    module = _module_name(path)
    role = f"Владелец модуля {module}"
    basis = f"Роль по пути {path}; специального правила для {analyzer or 'unknown'} нет."
    confidence = "low"

    if analyzer == "secret" or rule_id.startswith("SECRET") or rule_id.startswith("CG26-SEC"):
        role = "Владелец конфигурации и секретов"
        basis = f"analyzer={analyzer or '—'}; ruleId={rule_id or '—'}; путь={path}. Секреты и конфигурация обычно принадлежат владельцу окружения/конфигурации."
        confidence = "high"
    elif analyzer == "sca" or rule_id.startswith("CG26-SCA"):
        role = "Ответственный за зависимости и сборку"
        basis = f"analyzer={analyzer or '—'}; ruleId={rule_id or '—'}; путь={path}. Зависимости и версии обслуживает владелец сборки."
        confidence = "high"
    elif rule_id.startswith("CF-ROUTE") or rule_id.startswith("CF-OWNER") or "IDOR" in rule_id.upper():
        role = "Владелец маршрута и модели доступа"
        basis = f"ruleId={rule_id or '—'}; путь={path}. Правило относится к авторизации маршрута или проверке владельца."
        confidence = "high"
    elif "SQL" in rule_id.upper():
        role = "Backend-разработчик доступа к данным"
        basis = f"ruleId={rule_id or '—'}; путь={path}. Правило относится к формированию запроса/обработке данных."
        confidence = "high"
    elif analyzer == "sast":
        role = "Backend-разработчик обработчика"
        basis = f"analyzer=sast; ruleId={rule_id or '—'}; путь={path}. Находка в обработчике/коде приложения."
        confidence = "medium"
    if codeowners.owners:
        basis += f" CODEOWNERS: {codeowners.matchedPattern} → {', '.join(codeowners.owners)}."
        confidence = "high"
    return RoleSuggestion(role=role, basis=basis, confidence=confidence)


def _history_without_repo(reason: str) -> Dict[str, Any]:
    return {
        "status": "unavailable",
        "provider": None,
        "reason": reason,
        "evidenceKind": "last_file_commit",
        "notBlame": True,
        "pullRequests": [],
        "pullRequestsStatus": "unsupported",
        "pullRequestsReason": "История репозитория/PR недоступна для ZIP или неизвестного provider.",
        "pullRequestsTruncated": False,
        "lineBlame": {
            "status": "unsupported",
            "reason": "Line blame не запрашивался: безопасной поддержки без токена нет.",
        },
        "limitations": [
            "Это не автор уязвимости и не blame строк.",
            "Роль всё равно рассчитана по analyzer/rule/path.",
        ],
    }


def _load_context(db: Any, store: Any, run_id: str, finding_id: str) -> Dict[str, Any]:
    """All synchronous DB/store/CODEOWNERS reads, called in a worker thread.

    The caller must run this through contextvars.copy_context().run so the
    tenant workspace stays attached to these scoped reads.
    """
    run = db.get_run(run_id)
    if not run:
        raise ResponsibilityNotFound("Запуск не найден")
    findings = db.get_findings(run_id)
    finding = next((item for item in findings if str(item.get("id")) == finding_id), None)
    if finding is None:
        raise ResponsibilityNotFound("Находка не найдена")
    primary = finding.get("primaryLocation") if isinstance(finding.get("primaryLocation"), dict) else {}
    path = str(primary.get("path") or "")
    safe_path = is_safe_source_path(path)

    artifact_id = run.get("artifact_id")
    artifact = db.get_artifact(str(artifact_id)) if artifact_id else None
    artifact_workspace = str((artifact or {}).get("workspace_id") or workspace_id())
    meta = None
    if artifact and artifact_id:
        try:
            meta = store.load_artifact_meta(artifact_workspace, str(artifact_id))
        except (ValueError, OSError):
            meta = None

    if safe_path:
        codeowners = load_and_match_codeowners(
            store=store,
            workspace=artifact_workspace,
            artifact_row=artifact,
            meta=meta,
            path=path,
        )
    else:
        codeowners = CodeownersInfo(
            status="unavailable",
            basis="Путь находки не прошёл проверку; CODEOWNERS не искался.",
            limitations=["Некорректный путь не используется для чтения файлов или внешних запросов."],
        )
    return {
        "run": run,
        "finding": finding,
        "path": path,
        "safe_path": safe_path,
        "artifact_id": artifact_id,
        "artifact": artifact,
        "artifact_workspace": artifact_workspace,
        "meta": meta,
        "codeowners": codeowners,
    }


async def _build_responsibility_impl(
    *,
    db: Any,
    store: Any,
    run_id: str,
    finding_id: str,
    slot: _AdmissionSlot,
) -> ResponsibilityResponse:
    loop = asyncio.get_running_loop()
    # run_in_executor does NOT copy ContextVars. Copy explicitly, otherwise
    # db.workspace_id()/store fallback can use env workspace instead of the
    # authenticated account workspace.
    context = contextvars.copy_context()
    future = loop.run_in_executor(
        None, context.run, _load_context, db, store, run_id, finding_id
    )
    slot.sync_future = future
    # Shield the executor future: cancelling the HTTP waiter must not hide the
    # fact that a real thread is still using PG/CPU.
    context = await asyncio.shield(future)
    run = context["run"]
    finding = context["finding"]
    path = context["path"]
    safe_path = context["safe_path"]
    artifact_id = context["artifact_id"]
    meta = context["meta"]
    codeowners = context["codeowners"]
    suggestion = _suggest_role(finding, path or "неизвестный путь", codeowners)
    repository = meta.get("repository") if isinstance(meta, dict) else None
    provider, _, commit_sha = parse_repository_target(repository)

    if not safe_path:
        history_payload = _history_without_repo("Путь находки не прошёл проверку; история не запрашивалась.")
    elif provider is None or commit_sha is None:
        history_payload = _history_without_repo(
            "Артефакт из ZIP или без repository/commit_sha: .git и история репозитория не входят в snapshot."
        )
    else:
        history = await fetch_file_history(repository, path)
        history_payload = history.model_dump()

    snapshot_id = str(meta.get("snapshotId")) if isinstance(meta, dict) and meta.get("snapshotId") else None
    scope = ResponsibilityScope(
        runId=str(run.get("id") or run_id),
        artifactId=str(artifact_id) if artifact_id else None,
        snapshotId=snapshot_id,
        commitSha=commit_sha,
        path=path,
    )

    limitations = [
        "Рекомендуемая роль — подсказка по analyzer/rule/path, не назначение человека и не факт авторства.",
        "Последний коммит файла не является автором уязвимости; line blame отдельно не поддерживается.",
        "CODEOWNERS берётся только из immutable snapshot; ZIP без снимка не даёт истории.",
        "Чужие run/artifact/finding не адресуются клиентом; scope проверяется по текущему workspace.",
    ]
    return ResponsibilityResponse(
        scope=scope,
        findingId=str(finding_id),
        analyzer=str(finding.get("analyzer") or ""),
        ruleId=str(finding.get("ruleId") or ""),
        suggestedRole=suggestion,
        codeowners=codeowners,
        history=history_payload,
        limitations=limitations,
    )

async def build_responsibility(
    *,
    db: Any,
    store: Any,
    run_id: str,
    finding_id: str,
) -> ResponsibilityResponse:
    key = f"{workspace_id()}:{run_id}:{finding_id}"
    deadline = asyncio.get_running_loop().time() + RESPONSIBILITY_DEADLINE_SECONDS
    async with _REGISTRY_LOCK:
        existing = _INFLIGHT.get(key)
        if existing is not None and not existing.done():
            task = existing
        else:
            active = await _ADMISSION.reserve()
            slot = _AdmissionSlot()
            slot.active = active
            slot.queued = not active

            async def run() -> ResponsibilityResponse:
                try:
                    try:
                        await slot.activate(deadline)
                    except BaseException:
                        # activate() already removed the queue place.
                        slot.queued = False
                        raise
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise asyncio.TimeoutError
                    return await asyncio.wait_for(
                        _build_responsibility_impl(
                            db=db,
                            store=store,
                            run_id=run_id,
                            finding_id=finding_id,
                            slot=slot,
                        ),
                        timeout=remaining,
                    )
                finally:
                    slot.defer_or_release()

            task = asyncio.create_task(run())
            _INFLIGHT[key] = task

            def cleanup(finished: asyncio.Task) -> None:
                if _INFLIGHT.get(key) is finished:
                    _INFLIGHT.pop(key, None)
                if not finished.cancelled():
                    try:
                        finished.exception()
                    except Exception:
                        pass

            task.add_done_callback(cleanup)

    remaining = max(0.0, deadline - asyncio.get_running_loop().time())
    if remaining <= 0:
        raise asyncio.TimeoutError
    # Waiting on the shared task with shield: cancellation of one HTTP caller
    # does not cancel the build for other callers or free its worker slot.
    return await asyncio.wait_for(asyncio.shield(task), timeout=remaining)
