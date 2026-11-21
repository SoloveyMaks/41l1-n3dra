"""Bounded AI vulnerability scan over a frozen project snapshot.

The block is opt-in: it only runs when an AI_SCAN node is present in the
workflow. It never mutates findings, policy, canonical bundles or the report.
Provider failures return `degraded`; they are not analysis failures.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.app.ai.provider import (
    AiProviderError,
    complete_json,
    mask_sensitive_text,
    resolve_provider_config,
)
from backend.app.ai.scan_prompt import PROMPT_VERSION, SYSTEM_PROMPT
from backend.app.ai.scan_selection import (
    SELECTION_VERSION,
    ScanLimits,
    payload_for_model,
    select_sources,
)
from backend.app.ai.scan_validator import VALIDATOR_VERSION, validate_ai_scan_response
from backend.app.codefacts.identity import canonical_json, workspace_id
from backend.app.codefacts.map_builder import ensure_artifact_meta
from backend.app.codefacts.source import SnapshotIntegrityError, load_snapshot_sources
from backend.app.codefacts.store import CodeFactsStore
from backend.app.contracts.schemas import (
    AiScanCoverage,
    AiScanCoverageIssue,
    AiScanProvenance,
    AiScanResult,
    Finding,
)

_PROFILE_DEFAULTS: Dict[str, Tuple[int, int, int, int]] = {
    # max files, max bytes, per-file bytes, max observations
    "fast": (3, 12288, 4096, 6),
    "balanced": (6, 24576, 4096, 12),
    "deep": (10, 49152, 6144, 20),
}
_HARD_CAPS = {
    "maxFiles": 20,
    "maxBytes": 131072,
    "perFileBytes": 8192,
    "maxObservations": 30,
}


def _bounded_int(raw: Dict[str, Any], key: str, default: int, upper: int) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, upper))


def resolve_limits(config: Optional[Dict[str, Any]]) -> ScanLimits:
    raw = dict(config or {})
    profile = str(raw.get("profile") or "balanced").strip().lower()
    if profile not in _PROFILE_DEFAULTS:
        profile = "balanced"
    files, total, per_file, observations = _PROFILE_DEFAULTS[profile]
    return ScanLimits(
        profile=profile,
        max_files=_bounded_int(raw, "maxFiles", files, _HARD_CAPS["maxFiles"]),
        max_bytes=_bounded_int(raw, "maxBytes", total, _HARD_CAPS["maxBytes"]),
        per_file_bytes=_bounded_int(raw, "perFileBytes", per_file, _HARD_CAPS["perFileBytes"]),
        max_observations=_bounded_int(
            raw, "maxObservations", observations, _HARD_CAPS["maxObservations"]
        ),
    )


def _config_version(limits: ScanLimits, provider: Any) -> str:
    payload = {
        "profile": limits.profile,
        "maxFiles": limits.max_files,
        "maxBytes": limits.max_bytes,
        "perFileBytes": limits.per_file_bytes,
        "maxObservations": limits.max_observations,
        "promptVersion": PROMPT_VERSION,
        "validatorVersion": VALIDATOR_VERSION,
        "selectionVersion": SELECTION_VERSION,
        "provider": getattr(provider, "provider", "none") if provider else "none",
        "model": getattr(provider, "model", "") if provider else "",
        "endpoint": getattr(provider, "endpoint", "") if provider else "",
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:16]


def _provenance(
    provider: Any,
    limits: ScanLimits,
    ai_provenance: Any = None,
    fallback_reason: str = "",
) -> AiScanProvenance:
    return AiScanProvenance(
        provider=(
            getattr(ai_provenance, "provider", None)
            or (getattr(provider, "provider", "none") if provider else "none")
        ),
        model=(
            getattr(ai_provenance, "model", None)
            or (getattr(provider, "model", "") if provider else "")
        ),
        endpoint=(
            getattr(ai_provenance, "endpoint", None)
            or (getattr(provider, "endpoint", "") if provider else "")
        ),
        promptVersion=PROMPT_VERSION,
        validatorVersion=VALIDATOR_VERSION,
        selectionVersion=SELECTION_VERSION,
        configVersion=_config_version(limits, provider),
        requestId=getattr(ai_provenance, "request_id", None),
        latencyMs=float(getattr(ai_provenance, "latency_ms", 0.0) or 0.0),
        usage={
            "prompt": getattr(ai_provenance, "prompt_tokens", None),
            "completion": getattr(ai_provenance, "completion_tokens", None),
            "total": getattr(ai_provenance, "total_tokens", None),
        },
        finishReason=getattr(ai_provenance, "finish_reason", None),
        responseSha256=getattr(ai_provenance, "response_sha256", None),
        fallbackReason=mask_sensitive_text(fallback_reason)[:300] if fallback_reason else None,
    )


class AiScanService:
    """One bounded provider request per AI_SCAN node execution."""

    def __init__(self) -> None:
        self.provider_config = resolve_provider_config()

    def scan(
        self,
        *,
        artifact_id: str,
        artifact_row: Dict[str, Any],
        artifact_path: str,
        deterministic_findings: Sequence[Finding],
        config: Optional[Dict[str, Any]],
        run_id: str,
        workspace: Optional[str] = None,
    ) -> AiScanResult:
        limits = resolve_limits(config)
        # Resolve per execution (ContextVar-compatible); never cache workspace on self.
        workspace = workspace or workspace_id()
        coverage = AiScanCoverage(
            maxFiles=limits.max_files,
            maxBytes=limits.max_bytes,
            maxObservations=limits.max_observations,
        )
        provenance = _provenance(self.provider_config, limits)

        if not self.provider_config:
            coverage.status = "degraded"
            coverage.providerErrors = ["provider_not_configured"]
            return AiScanResult(
                status="degraded",
                coverage=coverage,
                validationErrors=["AI_PROVIDER_NOT_CONFIGURED"],
                provenance=provenance,
            )

        try:
            store = CodeFactsStore()
            meta = ensure_artifact_meta(store, workspace, artifact_row)
            snapshot = store.load_snapshot(workspace, str(meta.get("snapshotId") or ""))
            if not snapshot:
                raise ValueError("snapshot missing")
            sources, source_diags = load_snapshot_sources(Path(artifact_path), snapshot)
        except SnapshotIntegrityError as exc:
            coverage.status = "degraded"
            coverage.providerErrors = ["source_integrity"]
            return AiScanResult(
                status="degraded",
                coverage=coverage,
                validationErrors=[f"AI_SOURCE_INTEGRITY: {mask_sensitive_text(str(exc))[:200]}"],
                provenance=provenance,
            )
        except Exception as exc:  # noqa: BLE001 - AI must not break deterministic analysis
            coverage.status = "degraded"
            coverage.providerErrors = ["snapshot_unavailable"]
            return AiScanResult(
                status="degraded",
                coverage=coverage,
                validationErrors=[f"AI_SNAPSHOT_UNAVAILABLE: {mask_sensitive_text(str(exc))[:200]}"],
                provenance=provenance,
            )

        coverage.filesTotal = len(snapshot.get("files") or [])
        selection = select_sources(sources, snapshot.get("files") or [], deterministic_findings, limits)
        coverage = selection.coverage
        coverage.maxFiles = limits.max_files
        coverage.maxBytes = limits.max_bytes
        coverage.maxObservations = limits.max_observations
        for diag in source_diags[:20]:
            coverage.failed.append(AiScanCoverageIssue(
                path=str(diag.get("code", "")),
                code="snapshot_source",
                detail=str(diag.get("message", ""))[:200],
            ))
        coverage.failedCount = max(coverage.failedCount, len(coverage.failed))

        if not selection.files:
            coverage.status = "degraded"
            if "no_selected_source" not in coverage.providerErrors:
                coverage.providerErrors.append("no_selected_source")
            return AiScanResult(
                status="degraded",
                coverage=coverage,
                validationErrors=["AI_NO_SELECTED_SOURCE"],
                provenance=provenance,
            )

        # Deterministic findings are used server-side for duplicate suppression;
        # sending their full list makes the model enumerate instead of looking
        # for additional issues and can multiply output length.
        payload = payload_for_model(selection)
        try:
            data, ai_prov = complete_json(
                self.provider_config,
                SYSTEM_PROMPT,
                payload,
                max_tokens=min(4096, self.provider_config.max_tokens),
            )
        except AiProviderError as exc:
            reason = mask_sensitive_text(str(exc))[:300]
            coverage.status = "degraded"
            coverage.providerErrors = [reason or "provider_error"]
            return AiScanResult(
                status="degraded",
                coverage=coverage,
                validationErrors=[f"AI_PROVIDER_ERROR: {reason}"],
                provenance=_provenance(
                    self.provider_config, limits, ai_provenance=exc, fallback_reason=reason,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            reason = mask_sensitive_text(str(exc))[:300] or "provider_error"
            coverage.status = "degraded"
            coverage.providerErrors = [reason]
            return AiScanResult(
                status="degraded",
                coverage=coverage,
                validationErrors=[f"AI_PROVIDER_ERROR: {reason}"],
                provenance=_provenance(self.provider_config, limits, fallback_reason=reason),
            )

        outcome = validate_ai_scan_response(
            data,
            selection,
            deterministic_findings,
            workspace=workspace,
            run_id=run_id,
            max_observations=limits.max_observations,
        )
        result = AiScanResult(
            observations=outcome.observations,
            suppressedDuplicates=outcome.suppressed_duplicates,
            validationErrors=outcome.errors,
            coverage=coverage,
            provenance=_provenance(self.provider_config, limits, ai_provenance=ai_prov),
        )

        if not outcome.shape_valid:
            result.status = "rejected"
            coverage.status = "rejected"
        elif outcome.errors and not outcome.observations:
            result.status = "rejected"
            coverage.status = "rejected"
        elif outcome.errors:
            result.status = "partial"
            coverage.status = "partial"
        else:
            result.status = "succeeded" if coverage.status == "complete" else "partial"
            if coverage.status == "complete":
                coverage.status = "complete"
        return result