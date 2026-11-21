"""DI router factory for the Russian-text scenario builder.

Root mounts this after the auth/workspace middleware:

    from backend.app.api.scenario_builder import create_scenario_builder_router
    api_router.include_router(create_scenario_builder_router())

The router never saves a draft and never runs a scan; the frontend applies the
returned draft only after an explicit user click.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException

from backend.app.scenario_builder.models import ScenarioPreview, ScenarioPromptRequest
from backend.app.scenario_builder.service import (
    InvalidDraft,
    InvalidModelResponse,
    PromptTooLong,
    ProviderUnavailable,
    ScenarioBuilderService,
)


def create_scenario_builder_router(
    service: Optional[ScenarioBuilderService] = None,
    *,
    prefix: str = "/scenario-builder",
    max_concurrent: int = 2,
    timeout_seconds: float = 45.0,
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["scenario-builder"])
    builder = service or ScenarioBuilderService()
    semaphore = asyncio.Semaphore(max(1, min(int(max_concurrent), 4)))
    timeout = max(5.0, min(float(timeout_seconds), 60.0))

    @router.post("/preview", response_model=ScenarioPreview)
    async def preview(payload: ScenarioPromptRequest) -> ScenarioPreview:
        try:
            async with semaphore:
                return await asyncio.wait_for(
                    asyncio.to_thread(builder.preview, payload.prompt),
                    timeout=timeout,
                )
        except PromptTooLong as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": "SCENARIO_PROMPT_INVALID", "message": str(exc)},
            ) from None
        except InvalidModelResponse as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "SCENARIO_MODEL_INVALID", "message": str(exc)},
            ) from None
        except InvalidDraft as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "SCENARIO_DRAFT_INVALID", "message": str(exc)},
            ) from None
        except ProviderUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "SCENARIO_PROVIDER_UNAVAILABLE", "message": str(exc)},
            ) from None
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=504,
                detail={
                    "code": "SCENARIO_TIMEOUT",
                    "message": "Планировщик не ответил вовремя",
                },
            ) from None
        except Exception as exc:  # noqa: BLE001 - no traceback/raw provider text
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "SCENARIO_BUILDER_UNAVAILABLE",
                    "message": f"Планировщик временно недоступен: {type(exc).__name__}",
                },
            ) from None

    return router
