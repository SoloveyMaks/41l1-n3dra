"""Factory for read-only finding responsibility enrichment.

Mount after the auth/workspace middleware:

    from backend.app.api.responsibility import create_responsibility_router
    api_router.include_router(create_responsibility_router(db_repo, codefacts_store))
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException

from backend.app.codefacts.store import CodeFactsStore
from backend.app.responsibility.service import (
    ResponsibilityNotFound,
    build_responsibility,
)
from backend.app.storage.database import DatabaseRepository


def create_responsibility_router(
    db: DatabaseRepository,
    store: Optional[Any] = None,
    *,
    prefix: str = "",
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["responsibility"])
    facts = store or CodeFactsStore()

    @router.get("/runs/{run_id}/findings/{finding_id}/responsibility")
    async def get_responsibility(run_id: str, finding_id: str):
        try:
            return await build_responsibility(
                db=db,
                store=facts,
                run_id=run_id,
                finding_id=finding_id,
            )
        except ResponsibilityNotFound as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "RESPONSIBILITY_NOT_FOUND", "message": str(exc)},
            ) from None
        except Exception as exc:  # noqa: BLE001 - provider failures must not break analysis
            # The provider layer already converts network/rate-limit failures to
            # bounded DTO states. This is the last safety net for unexpected
            # snapshot/store errors: return a stable 503 instead of a traceback.
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "RESPONSIBILITY_UNAVAILABLE",
                    "message": f"Ответственный слой временно недоступен: {type(exc).__name__}",
                },
            ) from None

    return router
