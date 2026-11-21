"""Admin-only operational overview.

This is deliberately a read-only operator surface, not a user-account system.
The demo has one shared workspace; the signed session role only gates operator
actions such as workflow authoring, schedules and training hooks.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request

from backend.app.api.session import admin_available, public_demo, require_admin
from backend.app.artifacts.intake import (
    MAX_ARCHIVE_BYTES,
    MAX_COMPRESSION_RATIO,
    MAX_FILE_BYTES,
    MAX_FILE_COUNT,
    MAX_PATH_DEPTH,
    MAX_UNCOMPRESSED_BYTES,
)
from backend.app.storage.database import get_pool

admin_router = APIRouter(prefix="/api/admin", tags=["admin"])


def _bounded_db_overview(workspace_id: str) -> Dict[str, Any]:
    """Small, fixed queries; never return DSN, credentials or raw DB errors."""
    started = time.perf_counter()
    result: Dict[str, Any] = {
        "healthy": False,
        "latencyMs": None,
        "counts": None,
        "vectorAvailable": None,
        "vectorInstalledVersion": None,
        "activeEmbeddingSpecs": None,
        "migrations": None,
    }
    try:
        with get_pool().connection() as conn:
            conn.execute("SELECT 1").fetchone()
            result["healthy"] = True
            result["latencyMs"] = round((time.perf_counter() - started) * 1000, 1)
            counts = conn.execute(
                """
                SELECT
                    (SELECT count(*) FROM runtime.artifacts WHERE workspace_id = %s) AS artifacts,
                    (SELECT count(*) FROM runtime.workflows WHERE workspace_id = %s) AS workflows,
                    (SELECT count(*) FROM runtime.schedules WHERE workspace_id = %s) AS schedules,
                    (SELECT count(*) FROM runtime.schedules WHERE workspace_id = %s AND enabled = 1) AS schedules_enabled,
                    (SELECT count(*) FROM runtime.runs WHERE workspace_id = %s) AS runs,
                    (SELECT count(*) FROM runtime.runs WHERE workspace_id = %s AND execution_status = 'running') AS running,
                    (SELECT count(*) FROM runtime.runs WHERE workspace_id = %s AND execution_status = 'queued') AS queued,
                    (SELECT count(*) FROM runtime.runs WHERE workspace_id = %s AND execution_status = 'failed') AS failed
                """,
                (workspace_id,) * 8,
            ).fetchone()
            result["counts"] = {
                "projects": int(counts["artifacts"]),
                "artifacts": int(counts["artifacts"]),
                "workflows": int(counts["workflows"]),
                "schedules": int(counts["schedules"]),
                "schedulesEnabled": int(counts["schedules_enabled"]),
                "runs": int(counts["runs"]),
                "running": int(counts["running"]),
                "queued": int(counts["queued"]),
                "failed": int(counts["failed"]),
            }
            vector_available = conn.execute(
                "SELECT count(*) AS n FROM pg_available_extensions WHERE name = 'vector'"
            ).fetchone()
            result["vectorAvailable"] = bool(vector_available["n"])
            vector_installed = conn.execute(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            ).fetchone()
            result["vectorInstalledVersion"] = (
                str(vector_installed["extversion"]) if vector_installed else None
            )
            try:
                active = conn.execute(
                    "SELECT count(*) AS n FROM data_contract.embedding_specs WHERE status = 'active'"
                ).fetchone()
                result["activeEmbeddingSpecs"] = int(active["n"])
            except Exception:  # noqa: BLE001 - optional schema table
                result["activeEmbeddingSpecs"] = None
            try:
                migrations = conn.execute(
                    "SELECT count(*) AS n, max(applied_at) AS latest FROM public.schema_migrations"
                ).fetchone()
                result["migrations"] = {
                    "applied": int(migrations["n"]),
                    "latestAppliedAt": (
                        migrations["latest"].isoformat() if migrations["latest"] else None
                    ),
                }
            except Exception:  # noqa: BLE001 - ledger may be absent on a fresh DB
                result["migrations"] = None
    except Exception as exc:  # noqa: BLE001 - health is the answer, not a traceback
        result["healthy"] = False
        result["error"] = type(exc).__name__
    return result


@admin_router.get("/overview")
def admin_overview(request: Request) -> Dict[str, Any]:
    require_admin(request)

    # Lazy import avoids a router/admin import cycle at module import time.
    from backend.app.api.router import ai_service, db_repo

    db = _bounded_db_overview(db_repo.workspace_id)
    provider_config = getattr(ai_service, "provider_config", None)
    vector_info = ai_service.vector_runtime.status()
    vector_config = ai_service.vector_runtime.config

    provider: Optional[str] = None
    model = str(getattr(ai_service, "model_name", "") or "")
    if provider_config is not None:
        provider = str(getattr(provider_config, "provider", "reformboss"))
        model = str(getattr(provider_config, "model", model) or model)
    else:
        provider = "deterministic"

    return {
        "counts": db["counts"],
        "db": {
            "healthy": db["healthy"],
            "latencyMs": db["latencyMs"],
            **({"error": db["error"]} if db.get("error") else {}),
            "migrations": db["migrations"],
        },
        "ai": {
            "provider": provider,
            "model": model,
            "configured": provider_config is not None,
        },
        "vector": {
            "enabled": bool(vector_config.enabled),
            "available": db["vectorAvailable"],
            "installedVersion": db["vectorInstalledVersion"],
            "status": vector_info.status,
            "activeEmbeddingSpecs": db["activeEmbeddingSpecs"],
            "embeddingModel": vector_config.model or None,
            "dimension": vector_config.dimension,
            "metric": vector_config.metric or None,
            "missingConfigFields": list(vector_config.missing) if vector_config.enabled else [],
        },
        "uploads": {
            "maxArchiveBytes": MAX_ARCHIVE_BYTES,
            "maxUncompressedBytes": MAX_UNCOMPRESSED_BYTES,
            "maxFileCount": MAX_FILE_COUNT,
            "maxFileBytes": MAX_FILE_BYTES,
            "maxCompressionRatio": MAX_COMPRESSION_RATIO,
            "maxPathDepth": MAX_PATH_DEPTH,
        },
        "workspace": {
            "shared": True,
            "userAccounts": False,
            "accessModel": "shared demo workspace; signed role gates operator actions",
        },
        "auth": {
            "publicDemo": public_demo(),
            "adminAvailable": admin_available(),
            "adminAccessRequiresSeparateCode": True,
        },
    }