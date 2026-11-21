"""Fullest supported analysis: union of compatible scanner engines per category.

Each scanner node runs the backend analyzer and the Team B analyzer for its
category. One failing engine becomes an error diagnostic and never discards the
other engine's findings. Duplicates are merged by vulnerability identity rather
than by engine-specific fingerprints, so a SQL injection reported by both
engines stays one finding while a backend-only path traversal survives.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from analyzers.cg26.textfiles import (
    EXCLUDED_DIRS as TEAM_B_EXCLUDED_DIRS,
    MAX_FILE_BYTES as TEAM_B_MAX_FILE_BYTES,
    SELF_DIR_NAMES as TEAM_B_SELF_DIRS,
    TEXT_SUFFIXES as TEAM_B_TEXT_SUFFIXES,
)
from backend.app.analyzers.advisory_catalog import advisory_sources, expand_advisory_ids
from backend.app.analyzers.redaction import mask_secret_text
from backend.app.analyzers.sast_analyzer import SastAnalyzer
from backend.app.analyzers.sca_analyzer import ScaAnalyzer
from backend.app.analyzers.secret_analyzer import (
    MAX_FILE_SIZE as SECRET_MAX_FILE_BYTES,
    TEXT_EXTENSIONS as SECRET_TEXT_EXTENSIONS,
    SecretAnalyzer,
)
from backend.app.artifacts.intake import _SKIP_DIR_NAMES as INTAKE_SKIP_DIRS
from backend.app.codefacts.identity import sha256_hex, workspace_id
from backend.app.codefacts.source import (
    SnapshotIntegrityError,
    is_safe_source_path,
    load_snapshot_sources,
)
from backend.app.codefacts.store import CodeFactsStore
from backend.app.contracts.schemas import (
    Confidence,
    Coverage,
    Diagnostic,
    Finding,
    FindingStatus,
    ScanResult,
    Severity,
    TraceStep,
)
from backend.app.integrations.team_b import TeamBAdapter

CATEGORY_ANALYZERS = {
    "SECRET": SecretAnalyzer,
    "SCA": ScaAnalyzer,
    "SAST": SastAnalyzer,
}
CATEGORY_NAMES = {"SECRET": "secret", "SCA": "sca", "SAST": "sast"}

_TEAM_B_RULE_CANONICAL = {
    "CG26-SCA-001": "SCA-VULNERABLE-DEPENDENCY",
    "CG26-SAST-001": "KOTLIN-SQL-INJECTION",
    "CG26-SAST-002": "KOTLIN-TLS-VERIFICATION",
    "CG26-SAST-003": "KOTLIN-CORS-MISCONFIG",
    "CG26-SAST-004": "KOTLIN-INSECURE-ENDPOINT",
    "CG26-SAST-005": "KOTLIN-IDOR",
}

_SAST_FAMILY = {
    "CG26-SAST-001": "sql-injection",
    "CG26-SAST-002": "tls-verification",
    "CG26-SAST-003": "cors",
    "CG26-SAST-004": "route-authorization",
    "CG26-SAST-005": "object-authorization",
    "KOTLIN-SQL-INJECTION": "sql-injection",
    "KOTLIN-TLS-VERIFICATION": "tls-verification",
    "KOTLIN-CORS-MISCONFIG": "cors",
    "KOTLIN-INSECURE-ENDPOINT": "route-authorization",
    "KOTLIN-IDOR": "object-authorization",
    "KOTLIN-PATH-TRAVERSAL": "path-traversal",
    "KOTLIN-COMMAND-INJECTION": "command-injection",
    "CF-SQL-PARAM-INTERPOLATION": "sql-injection",
    "CF-ROUTE-UNAUTH-PII": "route-authorization",
    "CF-OWNER-CHECK-UNPROVEN": "object-authorization",
}

_SEVERITY_RANK = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}
_CONFIDENCE_RANK = {
    Confidence.INCONCLUSIVE: 0,
    Confidence.POTENTIAL: 1,
    Confidence.CONFIRMED: 2,
}


def _engine_reads(path: str, size: int) -> bool:
    name = path.rsplit("/", 1)[-1]
    suffix = Path(name).suffix.lower()
    if suffix == ".kt":
        # SAST reads every Kotlin file; it has no size ceiling.
        return True
    if (suffix in SECRET_TEXT_EXTENSIONS or name.startswith(".env")) and size <= SECRET_MAX_FILE_BYTES:
        return True
    parts = path.split("/")[:-1]
    if any(part in TEAM_B_EXCLUDED_DIRS or part in TEAM_B_SELF_DIRS for part in parts):
        return False
    if suffix not in TEAM_B_TEXT_SUFFIXES and name not in {".env", ".env.local"}:
        return False
    return size <= TEAM_B_MAX_FILE_BYTES


def _reject_added_scan_files(root: Path, snapshot: Dict[str, Any]) -> None:
    known = {str(item.get("path") or "") for item in snapshot.get("files", [])}
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            name for name in dirnames if name.casefold() not in INTAKE_SKIP_DIRS
        )
        current_path = Path(current)
        for name in sorted(filenames):
            target = current_path / name
            rel = target.relative_to(root).as_posix()
            if rel in known:
                continue
            if target.is_symlink():
                if _engine_reads(rel, 0):
                    raise SnapshotIntegrityError(f"Файл сканирования добавлен после приёма: {rel}")
                continue
            try:
                size = target.stat().st_size
            except OSError:
                continue
            if _engine_reads(rel, size):
                raise SnapshotIntegrityError(f"Файл сканирования добавлен после приёма: {rel}")


def verify_frozen_artifact(artifact_row: Dict[str, Any], artifact_path: str) -> None:
    """Reject bytes that no longer match the intake snapshot.

    Both scanner engines read the extracted directory. They may run only after
    every snapshot file has been re-hashed against the frozen manifest; a
    mutated artifact is a hard integrity error, never a new snapshot identity.
    """
    artifact_id = str(artifact_row.get("id") or "")
    if not artifact_id:
        raise SnapshotIntegrityError("frozen artifact id отсутствует")
    workspace = str(artifact_row.get("workspace_id") or workspace_id())
    store = CodeFactsStore()
    meta = store.load_artifact_meta(workspace, artifact_id)
    if not meta:
        raise SnapshotIntegrityError("frozen artifact meta отсутствует; байты не проверены")
    expected_artifact = str(artifact_row.get("hash_sha256") or "")
    meta_artifact = str(meta.get("artifactHash") or "")
    if expected_artifact and meta_artifact and expected_artifact != meta_artifact:
        raise SnapshotIntegrityError("artifactHash не совпадает с intake snapshot")
    snapshot = store.load_snapshot(workspace, str(meta.get("snapshotId") or ""))
    if not snapshot:
        raise SnapshotIntegrityError("frozen snapshot отсутствует; байты не проверены")

    root = Path(artifact_path).resolve()
    sources, _ = load_snapshot_sources(root, snapshot)
    checked = set(sources)
    for item in snapshot.get("files", []):
        rel = str(item.get("path") or "")
        if rel in checked:
            continue
        if not is_safe_source_path(rel):
            raise SnapshotIntegrityError(f"Недопустимый путь в snapshot: {rel!r}")
        target = (root / rel).resolve()
        if root != target and root not in target.parents:
            raise SnapshotIntegrityError(f"Путь выходит за snapshot: {rel}")
        if not target.is_file():
            raise SnapshotIntegrityError(f"Файл snapshot отсутствует: {rel}")
        if sha256_hex(target.read_bytes()) != str(item.get("sha256") or ""):
            raise SnapshotIntegrityError(f"Файл snapshot изменён после приёма: {rel}")
    _reject_added_scan_files(root, snapshot)


async def scan_category_full(
    *,
    category: str,
    artifact_path: str,
    artifact_row: Dict[str, Any],
    run_id: str,
    revision_id: str,
    adapter: TeamBAdapter,
) -> ScanResult:
    verify_frozen_artifact(artifact_row, artifact_path)
    analyzer_cls = CATEGORY_ANALYZERS[category]
    backend_task = asyncio.to_thread(lambda: analyzer_cls().analyze(artifact_path))
    team_b_task = asyncio.to_thread(
        adapter.scan_category,
        run_id=run_id,
        artifact_id=str(artifact_row.get("id") or ""),
        artifact_row=artifact_row,
        revision_id=revision_id,
        category=category,
    )
    backend_result, team_b_result = await asyncio.gather(
        backend_task, team_b_task, return_exceptions=True,
    )
    return merge_full_results(
        category,
        _engine_result(category, "backend", backend_result),
        _engine_result(category, "team_b", team_b_result),
    )


def _engine_result(category: str, engine: str, value: Any) -> ScanResult:
    if isinstance(value, ScanResult):
        return value
    if isinstance(value, BaseException):
        message = str(value) or type(value).__name__
        return ScanResult(
            analyzer=CATEGORY_NAMES[category],
            findings=[],
            diagnostics=[Diagnostic(
                level="error",
                message=f"Движок {engine} не отработал для категории {category} ({message})",
                source=f"full/{engine}",
                details={"code": f"{engine}.unavailable", "category": category},
            )],
            duration_ms=0.0,
        )
    return ScanResult(analyzer=CATEGORY_NAMES[category], duration_ms=0.0)


def merge_full_results(category: str, backend_result: ScanResult,
                       team_b_result: ScanResult) -> ScanResult:
    backend_findings = list(backend_result.findings)
    team_b_findings = [_canonicalize_team_b(item, category) for item in team_b_result.findings]

    merged: List[Finding] = []
    duplicates = 0
    for finding in backend_findings:
        merged.append(finding)
    for finding in team_b_findings:
        index = next(
            (i for i, existing in enumerate(merged) if _same_vulnerability(category, existing, finding)),
            None,
        )
        if index is None:
            merged.append(finding)
        else:
            duplicates += 1
            merged[index] = _merge_pair(merged[index], finding)

    if category == "SCA":
        merged = [_with_catalog_aliases(item) for item in merged]
    if category == "SECRET":
        merged = [_sanitize_secret_finding(item) for item in merged]

    diagnostics = [*backend_result.diagnostics, *team_b_result.diagnostics]
    diagnostics.append(Diagnostic(
        level="info",
        message=(
            f"Полный анализ ({category}): backend={len(backend_result.findings)}, "
            f"team_b={len(team_b_result.findings)}, объединено={len(merged)}, "
            f"дублей={duplicates}"
        ),
        source="full_analysis",
        details={
            "code": "full.merged",
            "category": category,
            "backend": len(backend_result.findings),
            "team_b": len(team_b_result.findings),
            "merged": len(merged),
            "duplicates": duplicates,
        },
    ))

    coverage = _merge_coverage(backend_result.coverage, team_b_result.coverage)
    return ScanResult(
        analyzer=CATEGORY_NAMES[category],
        findings=merged,
        diagnostics=diagnostics,
        coverage=coverage,
        duration_ms=round(max(backend_result.duration_ms, team_b_result.duration_ms), 2),
    )


def merge_extra_findings(existing: List[Finding], extra: List[Finding],
                         category: str) -> List[Finding]:
    """Add findings that survive the same identity rule used across engines."""
    merged = list(existing)
    for finding in extra:
        index = next(
            (i for i, current in enumerate(merged) if _same_vulnerability(category, current, finding)),
            None,
        )
        if index is None:
            merged.append(finding)
        else:
            merged[index] = _merge_pair(merged[index], finding)
    return merged


def _merge_coverage(left: Coverage, right: Coverage) -> Coverage:
    return Coverage(
        files_scanned=max(left.files_scanned, right.files_scanned),
        files_skipped=max(left.files_skipped, right.files_skipped),
        lines_of_code=max(left.lines_of_code, right.lines_of_code),
        rules_executed=sorted(set(left.rules_executed) | set(right.rules_executed)),
        parser_errors=max(left.parser_errors, right.parser_errors),
        unresolved_dependencies=max(left.unresolved_dependencies, right.unresolved_dependencies),
        rule_observations=[*left.rule_observations, *right.rule_observations],
    )


def _canonicalize_team_b(finding: Finding, category: str) -> Finding:
    updates: Dict[str, Any] = {}
    canonical_rule = _TEAM_B_RULE_CANONICAL.get(finding.ruleId)
    if canonical_rule and canonical_rule != finding.ruleId:
        updates["ruleId"] = canonical_rule
        updates["confidenceBasis"] = (
            f"{finding.confidenceBasis} (team_b {finding.ruleId}@{finding.ruleVersion})"
        ).strip()
    if category == "SCA":
        ids = expand_advisory_ids(finding.advisoryIds)
        updates["advisoryIds"] = ids
        sources = advisory_sources(ids)
        if sources and not any("Источники:" in step.description for step in finding.trace):
            updates["trace"] = [
                *finding.trace,
                TraceStep(
                    step=len(finding.trace) + 1,
                    description="Источники advisory: " + ", ".join(sources),
                    location=None,
                ),
            ]
    return finding.model_copy(update=updates) if updates else finding


def _with_catalog_aliases(finding: Finding) -> Finding:
    ids = expand_advisory_ids(finding.advisoryIds)
    if ids == finding.advisoryIds:
        return finding
    return finding.model_copy(update={"advisoryIds": ids})


def _sanitize_secret_finding(finding: Finding) -> Finding:
    trace = [
        step.model_copy(update={"description": mask_secret_text(step.description)})
        for step in finding.trace
    ]
    return finding.model_copy(update={
        "redactedEvidence": mask_secret_text(finding.redactedEvidence),
        "trace": trace,
        "severityBasis": mask_secret_text(finding.severityBasis),
    })


def _sast_family(rule_id: str) -> str:
    return _SAST_FAMILY.get(rule_id, rule_id)


def _overlaps(left: Any, right: Any) -> bool:
    return (
        left.path == right.path
        and left.startLine <= right.endLine
        and right.startLine <= left.endLine
    )


def _byte_overlaps(left: Any, right: Any) -> Optional[bool]:
    left_range = getattr(left, "byteRange", None)
    right_range = getattr(right, "byteRange", None)
    if left_range and right_range:
        return left_range[0] < right_range[1] and right_range[0] < left_range[1]
    return None


def _same_vulnerability(category: str, left: Finding, right: Finding) -> bool:
    if category == "SECRET":
        if left.primaryLocation.path != right.primaryLocation.path:
            return False
        byte_overlap = _byte_overlaps(left.primaryLocation, right.primaryLocation)
        if byte_overlap is not None:
            # One line can hold two independent literals; only overlapping
            # byte spans identify the same secret value.
            return byte_overlap
        return _overlaps(left.primaryLocation, right.primaryLocation)
    if not _overlaps(left.primaryLocation, right.primaryLocation):
        return False
    if category == "SCA":
        return bool(set(left.advisoryIds) & set(right.advisoryIds))
    if category == "SAST":
        return _sast_family(left.ruleId) == _sast_family(right.ruleId)
    return False


def _merge_pair(base: Finding, other: Finding) -> Finding:
    related = list(base.relatedLocations)
    for location in other.relatedLocations:
        if not any(
            item.path == location.path
            and item.startLine == location.startLine
            and item.endLine == location.endLine
            for item in related
        ):
            related.append(location)

    trace = list(base.trace)
    for step in other.trace:
        key = (
            step.description,
            step.location.path if step.location else "",
            step.location.startLine if step.location else 0,
        )
        if not any(
            (
                item.description,
                item.location.path if item.location else "",
                item.location.startLine if item.location else 0,
            ) == key
            for item in trace
        ):
            trace.append(TraceStep(
                step=len(trace) + 1,
                description=step.description,
                location=step.location,
            ))

    severity = base.severity if _SEVERITY_RANK[base.severity] >= _SEVERITY_RANK[other.severity] else other.severity
    confidence = (
        base.confidence
        if _CONFIDENCE_RANK[base.confidence] >= _CONFIDENCE_RANK[other.confidence]
        else other.confidence
    )
    status = (
        FindingStatus.CONFIRMED
        if FindingStatus.CONFIRMED in {base.status, other.status}
        else base.status
    )
    notes = list(base.coverageWarnings)
    note = "Совпадение независимых движков анализа"
    if note not in notes:
        notes.append(note)
    return base.model_copy(update={
        "severity": severity,
        "confidence": confidence,
        "status": status,
        "relatedLocations": related,
        "trace": trace,
        "advisoryIds": expand_advisory_ids([*base.advisoryIds, *other.advisoryIds]),
        "coverageWarnings": notes,
        "confidenceBasis": (
            f"{base.confidenceBasis} Совпадение с {other.analyzer} "
            f"{other.ruleId}@{other.ruleVersion}."
        ).strip(),
    })