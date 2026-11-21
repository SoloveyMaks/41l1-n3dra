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
    max_queue: int = 4,
    timeout_seconds: float = 45.0,
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["scenario-builder"])
    builder = service or ScenarioBuilderService()
    concurrency = max(1, min(int(max_concurrent), 4))
    queued = max(0, min(int(max_queue), 16))
    semaphore = asyncio.Semaphore(concurrency)
    timeout = max(5.0, min(float(timeout_seconds), 60.0))
    max_inflight = concurrency + queued
    inflight = 0

    @router.post("/preview", response_model=ScenarioPreview)
    async def preview(payload: ScenarioPromptRequest) -> ScenarioPreview:
        nonlocal inflight
        if inflight >= max_inflight:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "SCENARIO_BUSY",
                    "message": "Слишком много одновременных запросов. Попробуйте позже.",
                },
                headers={"Retry-After": "5"},
            )
        inflight += 1
        acquired = False
        future: Optional[asyncio.Future] = None
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            try:
                await asyncio.wait_for(semaphore.acquire(), timeout=timeout)
                acquired = True
            except asyncio.TimeoutError:
                raise HTTPException(
                    status_code=429,
                    detail={
                        "code": "SCENARIO_BUSY",
                        "message": "Очередь планировщика переполнена. Попробуйте позже.",
                    },
                    headers={"Retry-After": "5"},
                ) from None

            remaining = timeout - (loop.time() - started)
            if remaining <= 0:
                raise HTTPException(
                    status_code=429,
                    detail={
                        "code": "SCENARIO_BUSY",
                        "message": "Общий лимит ожидания исчерпан.",
                    },
                    headers={"Retry-After": "5"},
                )
            future = asyncio.ensure_future(asyncio.to_thread(builder.preview, payload.prompt))

            def release_after_thread(done: asyncio.Future) -> None:
                # A timed-out HTTP coroutine must not free the slot while the
                # blocking provider thread is still running.
                try:
                    done.result()
                except BaseException:
                    pass
                semaphore.release()

            try:
                result = await asyncio.wait_for(
                    asyncio.shield(future),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                future.add_done_callback(release_after_thread)
                acquired = False
                raise HTTPException(
                    status_code=504,
                    detail={
                        "code": "SCENARIO_TIMEOUT",
                        "message": "Планировщик не ответил вовремя",
                    },
                ) from None
            except asyncio.CancelledError:
                future.add_done_callback(release_after_thread)
                acquired = False
                raise
            except BaseException:
                semaphore.release()
                acquired = False
                raise
            else:
                semaphore.release()
                acquired = False
                return result
        except HTTPException:
            raise
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
        finally:
            inflight -= 1
            if acquired:
                semaphore.release()

    return router
