"""DI router factory for pinned reference-suite comparison.

The root application mounts this after auth/workspace middleware:

    from backend.app.api.reference_checks import create_reference_checks_router
    api_router.include_router(create_reference_checks_router())

This module never executes repository code and never checks out a branch. It
compares one already-published ``AnalysisBundle`` with one versioned manifest.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.app.reference_checks import ReferenceCheckError, compare_reference_suite


class ReferenceComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest: Dict[str, Any] = Field(description="reference-suite/v1 manifest")
    bundle: Dict[str, Any] = Field(description="published AnalysisBundle 0.1")
    targetSha: str = Field(description="commit SHA of the scanned snapshot")
    targetProjectId: str = Field(description="same project as the reference suite")
    targetBranch: Optional[str] = None


def create_reference_checks_router(*, prefix: str = "/reference-checks") -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["reference-checks"])

    @router.post("/compare")
    async def compare(payload: ReferenceComparisonRequest) -> Dict[str, Any]:
        try:
            return compare_reference_suite(
                manifest=payload.manifest,
                bundle=payload.bundle,
                target_sha=payload.targetSha,
                target_project_id=payload.targetProjectId,
                target_branch=payload.targetBranch,
            )
        except ReferenceCheckError as exc:
            # Version incompatibility is a report verdict, not an HTTP error.
            raise HTTPException(
                status_code=422,
                detail={"code": exc.code, "message": exc.message},
            ) from None

    return router