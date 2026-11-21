"""Fullest supported analysis: union of compatible scanner engines per category.

Each scanner node runs the backend analyzer and the Team B analyzer for its
category. One failing engine becomes an error diagnostic and never discards the
other engine's findings. Duplicates are merged by vulnerability identity rather
than by engine-specific fingerprints, so a SQL injection reported by both
engines stays one finding while a backend-only path traversal survives.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from backend.app.analyzers.advisory_catalog import advisory_sources, expand_advisory_ids
from backend.app.analyzers.redaction import mask_secret_text
from backend.app.analyzers.sast_analyzer import SastAnalyzer
from backend.app.analyzers.sca_analyzer import ScaAnalyzer
from backend.app.analyzers.secret_analyzer import SecretAnalyzer
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


async def scan_category_full(
    *,
    category: str,
    artifact_path: str,
    artifact_row: Dict[str, Any],
    run_id: str,
    revision_id: str,
    adapter: TeamBAdapter,
) -> ScanResult:
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


def _same_vulnerability(category: str, left: Finding, right: Finding) -> bool:
    if not _overlaps(left.primaryLocation, right.primaryLocation):
        return False
    if category == "SECRET":
        return True
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