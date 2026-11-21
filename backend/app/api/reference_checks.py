"""DI router for pinned reference-suite comparison.

The caller sends only server-owned identifiers::

    POST /api/reference-checks/compare
    {"referenceManifestId": "reference-suite", "targetRunId": "run_...",
     "targetArtifactId": "..."}  # artifact id is optional; run implies it

    GET /api/reference-checks/references
    # -> [{"id", "label", "projectId", "branch", "sha"}, ...]

Never accepted: a raw manifest, a raw bundle, ``targetSha``, ``targetBranch``
or ``targetProjectId``. The authenticated workspace comes from the existing
session middleware; the loaders resolve the published envelope and source
commit server-side. The root wires this factory after auth/workspace
middleware; ``main.py`` and the global router are not modified here.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.app.reference_checks import (
    SERVER_MODE,
    ReferenceCheckError,
    ReferenceSuite,
    ReferenceSuiteRegistry,
    ServerSnapshot,
    compare_reference_suite,
    parse_manifest,
)

WorkspaceResolver = Callable[[Request], str]
TargetLoader = Callable[[str, str, Optional[str]], ServerSnapshot]
ReferenceLoader = Callable[
    [str, ReferenceSuite, ReferenceSuiteRegistry, str], Optional[ServerSnapshot]
]

_STATUS_CODES = {
    "REFERENCE_UNKNOWN": 404,
    "REFERENCE_UNREADABLE": 503,
    "INVALID_MANIFEST": 422,
    "UNSUPPORTED_MANIFEST": 422,
    "INVALID_PROVENANCE": 422,
    "TARGET_RUN_UNKNOWN": 404,
    "TARGET_ARTIFACT_UNKNOWN": 404,
    "TARGET_RUN_ARTIFACT_MISMATCH": 404,
    "TARGET_SNAPSHOT_UNKNOWN": 409,
    "TARGET_SNAPSHOT_MISMATCH": 409,
    "TARGET_NOT_PUBLISHED": 409,
    "TARGET_CANONICAL_INCOMPLETE": 409,
    "TARGET_IDENTITY_MISMATCH": 409,
    "TARGET_PROVENANCE_MISSING": 409,
    "INVALID_BUNDLE": 409,
    "INCOMPATIBLE_BUNDLE": 409,
}


class ReferenceComparisonRequest(BaseModel):
    """Only server-owned IDs. Raw manifests/bundles/SHAs are rejected."""

    model_config = ConfigDict(extra="forbid")

    referenceManifestId: str = Field(
        min_length=1, max_length=64, description="server-owned reference suite id"
    )
    targetRunId: str = Field(
        min_length=1, max_length=128, description="server-owned target run id"
    )
    targetArtifactId: Optional[str] = Field(
        default=None,
        max_length=128,
        description="optional; must be the run's artifact when present",
    )


def _default_workspace(request: Request) -> str:
    from backend.app.api.session import require_workspace_actor

    session = require_workspace_actor(request)
    return str(session["workspaceId"])


def _default_target_loader(
    workspace: str, run_id: str, artifact_id: Optional[str]
) -> ServerSnapshot:
    from backend.app.reference_checks.canonical import load_canonical_snapshot

    return load_canonical_snapshot(workspace, run_id, artifact_id=artifact_id)


def _default_reference_loader(
    workspace: str,
    suite: ReferenceSuite,
    registry: ReferenceSuiteRegistry,
    manifest_id: str,
) -> Optional[ServerSnapshot]:
    from backend.app.reference_checks.canonical import load_reference_snapshot

    return load_reference_snapshot(workspace, suite, registry, manifest_id)


def _http_error(exc: ReferenceCheckError) -> HTTPException:
    status_code = _STATUS_CODES.get(exc.code, 422)
    return HTTPException(
        status_code=status_code,
        detail={"code": exc.code, "message": exc.message},
    )


def create_reference_checks_router(
    *,
    prefix: str = "/reference-checks",
    registry: ReferenceSuiteRegistry | None = None,
    resolve_workspace: WorkspaceResolver | None = None,
    load_target_snapshot: TargetLoader | None = None,
    load_reference_snapshot: ReferenceLoader | None = None,
) -> APIRouter:
    """Build the router. Every dependency is injectable for the root owner."""

    suite_registry = registry or ReferenceSuiteRegistry()
    workspace_resolver = resolve_workspace or _default_workspace
    target_loader = load_target_snapshot or _default_target_loader
    reference_loader = load_reference_snapshot or _default_reference_loader
    router = APIRouter(prefix=prefix, tags=["reference-checks"])

    @router.get("/references")
    async def list_references(request: Request) -> List[Dict[str, Any]]:
        """Server-owned descriptors for the UI picker; never bundles or paths.

        The workspace guard runs first, exactly like compare. Each descriptor is
        only ``{id, label, projectId, branch, sha}``: the manifest id is the
        compare handle, the label is the manifest's own suite id, and
        project/branch/SHA come from the validated server-owned manifest. No
        value is fabricated: a manifest that fails ``parse_manifest`` raises.
        """
        try:
            workspace_resolver(request)
            descriptors: List[Dict[str, Any]] = []
            for manifest_id, raw_manifest in suite_registry.list_manifests():
                suite = parse_manifest(raw_manifest)
                descriptors.append({
                    "id": manifest_id,
                    "label": suite.suite_id,
                    "projectId": suite.project_id,
                    "branch": suite.branch,
                    "sha": suite.sha,
                })
            return descriptors
        except ReferenceCheckError as exc:
            raise _http_error(exc) from None

    @router.post("/compare")
    async def compare(payload: ReferenceComparisonRequest, request: Request) -> Dict[str, Any]:
        try:
            workspace = workspace_resolver(request)
            raw_manifest = suite_registry.load_manifest(payload.referenceManifestId)
            suite = parse_manifest(raw_manifest)
            target = target_loader(
                workspace, payload.targetRunId, payload.targetArtifactId
            )
            if target.bundle is None:
                raise ReferenceCheckError(
                    "TARGET_NOT_PUBLISHED", "target snapshot has no published bundle"
                )
            reference = reference_loader(
                workspace, suite, suite_registry, payload.referenceManifestId
            )
            return compare_reference_suite(
                manifest=raw_manifest,
                bundle=target.bundle,
                mode=SERVER_MODE,
                reference_snapshot=reference,
                target_snapshot=target,
            )
        except ReferenceCheckError as exc:
            raise _http_error(exc) from None

    return router
