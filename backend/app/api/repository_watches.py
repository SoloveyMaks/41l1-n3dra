"""Admin API расписаний репозиториев (runtime watches).

Фабрика не трогает `api/router.py`: владелец importer-а подключает её через
`create_repository_watch_router(db, executor)` внутри `api_router`.

Все ответы — только безопасные поля строки: claim_token/claimed_until и
workspace_id наружу не уходят. Ошибки importer-а уже приходят безопасным
текстом, а scheduler дополнительно вычищает credentials из URL.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request

from backend.app.api.session import require_admin
from backend.app.engine.scheduler import (
    DEFAULT_WATCH_INTERVAL_SECONDS,
    RepositoryWatchService,
    WatchNotFoundError,
    WatchUnavailableError,
    WatchValidationError,
)

_PUBLIC_WATCH_KEYS = (
    "id", "workflow_id", "url", "provider", "branch", "cron_or_interval",
    "enabled", "next_check_at", "last_checked_at", "last_seen_commit",
    "last_run_id", "last_error", "created_at",
)


def _public_watch(row: Dict[str, Any]) -> Dict[str, Any]:
    return {key: row.get(key) for key in _PUBLIC_WATCH_KEYS}


def create_repository_watch_router(db: Any, executor: Any = None) -> APIRouter:
    router = APIRouter(prefix="/repository-watches")
    service = RepositoryWatchService(db, executor)

    @router.get("")
    async def list_repository_watches(request: Request) -> List[Dict[str, Any]]:
        require_admin(request)
        rows = await asyncio.to_thread(db.list_repository_watches)
        return [_public_watch(row) for row in rows]

    @router.post("")
    async def create_repository_watch(payload: Dict[str, Any],
                                      request: Request) -> Dict[str, Any]:
        require_admin(request)
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Тело запроса должно быть объектом")
        cron = payload.get("cronOrInterval", f"interval:{DEFAULT_WATCH_INTERVAL_SECONDS}")
        try:
            row = await service.create_watch(
                payload.get("workflowId"), payload.get("url"),
                payload.get("branch"), cron,
            )
        except WatchValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except WatchUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return _public_watch(row)

    @router.patch("/{watch_id}")
    async def update_repository_watch(watch_id: str, payload: Dict[str, Any],
                                      request: Request) -> Dict[str, Any]:
        require_admin(request)
        enabled = payload.get("enabled") if isinstance(payload, dict) else None
        if not isinstance(enabled, bool):
            raise HTTPException(status_code=400, detail="enabled должен быть boolean")
        try:
            row = await service.set_enabled(watch_id, enabled)
        except WatchNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _public_watch(row)

    @router.post("/{watch_id}/check")
    async def check_repository_watch(watch_id: str, request: Request) -> Dict[str, Any]:
        require_admin(request)
        try:
            return await service.check_now(watch_id)
        except WatchNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WatchValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except WatchUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    return router