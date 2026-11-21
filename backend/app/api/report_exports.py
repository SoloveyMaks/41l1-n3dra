"""HTTP factory for downloadable account report exports.

Root mounts this router into the existing `/api` router. Authorization and
workspace scope stay in the existing session middleware; this module only uses
the scoped DatabaseRepository and never trusts a workspace from the URL.
"""

from __future__ import annotations

import io
import re
from typing import Dict

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse

from backend.app.reports.exports import (
    ReportExportUnavailable,
    ReportNotFound,
    build_docx,
    build_jsonc,
    load_export_bundle,
)
from backend.app.storage.database import DatabaseRepository

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def _safe_run_id(run_id: str) -> str:
    return run_id if _RUN_ID_RE.fullmatch(str(run_id)) else "run"


def _headers(run_id: str, extension: str) -> Dict[str, str]:
    return {
        "Content-Disposition": f'attachment; filename="nedra-{_safe_run_id(run_id)}.{extension}"',
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }


def create_report_exports_router(db: DatabaseRepository) -> APIRouter:
    router = APIRouter(tags=["report-exports"])

    def _load(run_id: str):
        try:
            return load_export_bundle(db, run_id)
        except ReportNotFound as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "RUN_NOT_FOUND", "message": str(exc)},
            ) from None

    @router.get("/runs/{run_id}/report/export.jsonc")
    def export_jsonc(run_id: str) -> Response:
        bundle = _load(run_id)
        content = build_jsonc(bundle)
        return Response(
            content=content,
            media_type="application/json; charset=utf-8",
            headers=_headers(run_id, "jsonc"),
        )

    @router.get("/runs/{run_id}/report/export.docx")
    def export_docx(run_id: str) -> StreamingResponse:
        bundle = _load(run_id)
        try:
            content = build_docx(bundle)
        except ReportExportUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "REPORT_EXPORT_UNAVAILABLE", "message": str(exc)},
            ) from None
        return StreamingResponse(
            io.BytesIO(content),
            media_type=(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            headers=_headers(run_id, "docx"),
        )

    return router