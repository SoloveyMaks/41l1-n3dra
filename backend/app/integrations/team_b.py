"""Optional Team B analyzer engine.

Team B lives in the root ``analyzers`` package. This module is an adapter only:
it never replaces ``backend/app/analyzers``, never executes Kotlin/Gradle, and
builds the Team B AnalysisBundle once per run. The bundle is then normalized to
the intake ZIP identity/full manifest and passed to the existing canonical
publisher.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.app.codefacts.identity import (
    revision_uuid,
    run_uuid,
    storage_manifest_hash,
)
from backend.app.codefacts.map_builder import ensure_artifact_meta
from backend.app.codefacts.publish import map_from_analysis_bundle
from backend.app.codefacts.store import CodeFactsStore
from backend.app.contracts.schemas import (
    Confidence,
    Coverage,
    Diagnostic,
    Finding,
    FindingStatus,
    Location,
    ScanResult,
    Severity,
    TraceStep,
)

SCAN_ENGINE = "team_b"
DEFAULT_ENGINE = "backend"
_ADVISORY_MAX_BYTES = 10 * 1024 * 1024
_BUILD_LOCK = threading.Lock()


class TeamBError(RuntimeError):
    """Team B cannot be used safely for this run."""


@dataclass
class TeamBPayload:
    bundle: Dict[str, Any]
    scan_results: Dict[str, ScanResult]


def _trusted_advisory_path() -> Optional[Path]:
    raw = os.getenv("CG26_ADVISORY_DB", "").strip()
    if not raw:
        return None
    if raw.lower().startswith(("http://", "https://")):
        raise TeamBError("CG26_ADVISORY_DB должен быть локальным файлом, URL запрещён")
    path = Path(raw).resolve()
    if not path.is_file():
        raise TeamBError("CG26_ADVISORY_DB не найден")
    if path.stat().st_size > _ADVISORY_MAX_BYTES:
        raise TeamBError("CG26_ADVISORY_DB слишком большой")
    root = os.getenv("CG26_ADVISORY_ROOT", "").strip()
    if root:
        trusted_root = Path(root).resolve()
        if trusted_root != path and trusted_root not in path.parents:
            raise TeamBError("CG26_ADVISORY_DB вне CG26_ADVISORY_ROOT")
    return path


def _require_hmac_environment() -> None:
    key = os.getenv("CG26_HMAC_KEY", "").strip()
    version = os.getenv("CG26_HMAC_KEY_VERSION", "").strip()
    if not key:
        raise TeamBError("Team B требует серверный CG26_HMAC_KEY; demo key запрещён")
    if not version or version == "local-demo-1":
        raise TeamBError("Team B требует CG26_HMAC_KEY_VERSION, отличный от local-demo-1")


def _backend_location(location: Dict[str, Any]) -> Location:
    return Location(
        path=str(location["path"]),
        startLine=int(location["line"]),
        endLine=int(location["endLine"]),
        startColumn=int(location["column"]),
        endColumn=int(location["endColumn"]),
        byteRange=[int(location["byteStart"]), int(location["byteEnd"])],
    )


def _confidence(value: str) -> Confidence:
    return {"high": Confidence.CONFIRMED, "medium": Confidence.POTENTIAL}.get(
        value, Confidence.INCONCLUSIVE,
    )


def _severity(value: str) -> Severity:
    try:
        return Severity(value)
    except ValueError:
        return Severity.INFO


def _backend_findings(bundle: Dict[str, Any], category: str) -> List[Finding]:
    evidence_by_id: Dict[str, Any] = {}
    for part in [*bundle.get("scanResults", []), bundle.get("codeFacts", {})]:
        for item in part.get("evidence", []):
            evidence_by_id[item["id"]] = item
    result: List[Finding] = []
    for scan in bundle["scanResults"]:
        if scan["category"] != category:
            continue
        for item in scan.get("findings", []):
            primary = evidence_by_id.get(item.get("primaryEvidenceId"))
            if not primary:
                continue
            related_ids = list(item.get("relatedEvidenceIds", []))
            trace_ids = list(item.get("traceEvidenceIds", []))
            related = [
                _backend_location(evidence_by_id[eid]["location"])
                for eid in related_ids if eid in evidence_by_id
            ]
            trace: List[TraceStep] = []
            for step_number, eid in enumerate(trace_ids, start=1):
                evidence = evidence_by_id.get(eid)
                if evidence:
                    trace.append(TraceStep(
                        step=step_number,
                        description=str(evidence.get("redactedSnippet", "")),
                        location=_backend_location(evidence["location"]),
                    ))
            result.append(Finding(
                id=str(item["id"]),
                fingerprint=str(item["fingerprint"]),
                analyzer=category.lower(),
                ruleId=str(item["ruleId"]),
                ruleVersion=str(item.get("ruleVersion", "1")),
                severity=_severity(item["severity"]),
                severityBasis=str(item.get("severityBasis", "")),
                confidence=_confidence(item.get("confidence", "low")),
                confidenceBasis=str(item.get("confidenceBasis", "")),
                status=FindingStatus(item.get("status", "inconclusive")),
                primaryLocation=_backend_location(primary["location"]),
                relatedLocations=related,
                trace=trace,
                redactedEvidence=str(primary.get("redactedSnippet", "")),
                advisoryIds=list(item.get("advisoryIds", [])),
                coverageWarnings=list(item.get("coverageWarnings", [])),
            ))
    return result


def _backend_scan_results(bundle: Dict[str, Any]) -> Dict[str, ScanResult]:
    results: Dict[str, ScanResult] = {}
    for scan in bundle["scanResults"]:
        category = str(scan["category"])
        coverage = scan["coverage"]
        diagnostics = [
            Diagnostic(
                level="error" if str(item.get("code", "")).endswith("error") else "warning",
                message=str(item.get("message", "")),
                source="team_b",
                details={"code": str(item.get("code", "")), "category": category},
            )
            for item in scan.get("diagnostics", [])
        ]
        total = int(coverage.get("filesTotal", 0))
        visited = int(coverage.get("filesVisited", 0))
        results[category] = ScanResult(
            analyzer=category.lower(),
            findings=_backend_findings(bundle, category),
            diagnostics=diagnostics,
            coverage=Coverage(
                files_scanned=max(0, min(visited, total)),
                files_skipped=max(0, total - visited),
                lines_of_code=0,
                rules_executed=list(coverage.get("rulesApplied", [])),
                parser_errors=len(coverage.get("parserErrors", [])),
                unresolved_dependencies=len(coverage.get("unresolvedSymbols", [])),
            ),
            duration_ms=float(scan.get("durationMs", 0) or 0),
        )
    return results


class TeamBAdapter:
    """Cache exactly one Team B build per run."""

    def __init__(self, store: Optional[CodeFactsStore] = None):
        self.store = store or CodeFactsStore()
        self._cache: Dict[str, TeamBPayload] = {}
        self._lock = threading.Lock()

    def scan_category(self, *, run_id: str, artifact_id: str, artifact_row: Dict[str, Any],
                      revision_id: str, category: str) -> ScanResult:
        payload = self._payload(run_id, artifact_id, artifact_row, revision_id)
        try:
            return payload.scan_results[category]
        except KeyError as exc:
            raise TeamBError(f"Team B не вернул scan result для {category}") from exc

    def get_bundle(self, run_id: str) -> Optional[Dict[str, Any]]:
        payload = self._cache.get(run_id)
        return payload.bundle if payload else None

    def release(self, run_id: str) -> None:
        with self._lock:
            self._cache.pop(run_id, None)

    def _payload(self, run_id: str, artifact_id: str, artifact_row: Dict[str, Any],
                 revision_id: str) -> TeamBPayload:
        with self._lock:
            cached = self._cache.get(run_id)
        if cached:
            return cached
        with _BUILD_LOCK:
            with self._lock:
                cached = self._cache.get(run_id)
            if cached:
                return cached
            payload = self._build(run_id, artifact_id, artifact_row, revision_id)
            with self._lock:
                self._cache[run_id] = payload
            return payload

    def _build(self, run_id: str, artifact_id: str, artifact_row: Dict[str, Any],
               revision_id: str) -> TeamBPayload:
        _require_hmac_environment()
        advisory = _trusted_advisory_path()
        extracted_path = artifact_row.get("extracted_path")
        if not extracted_path or not Path(extracted_path).is_dir():
            raise TeamBError("Team B: распакованный артефакт недоступен")
        artifact_hash = str(artifact_row.get("hash_sha256") or "")
        if len(artifact_hash) != 64:
            raise TeamBError("Team B: у артефакта нет полного ZIP sha256")
        workspace = str(artifact_row.get("workspace_id") or "")
        if not workspace:
            # Runtime PG rows intentionally hide workspace; use the server scope.
            from backend.app.codefacts.identity import workspace_id
            workspace = workspace_id()
        meta = ensure_artifact_meta(self.store, workspace, artifact_row)
        snapshot = self.store.load_snapshot(workspace, str(meta.get("snapshotId")))
        if not snapshot:
            raise TeamBError("Team B: frozen snapshot не найден")
        manifest_hash = storage_manifest_hash(snapshot.get("files", []))
        identity = {
            "workspaceId": workspace,
            "runId": run_uuid(run_id, workspace),
            "snapshotId": str(meta["snapshotId"]),
            "artifactHash": artifact_hash,
            "snapshotManifestHash": manifest_hash,
            "workflowRevisionId": revision_uuid(revision_id, workspace),
        }
        try:
            from analyzers.scan import run as team_b_run
            from analyzers.cg26.contract import build_bundle
            from analyzers.cg26.project import Project
            import analyzers.cg26.sca as sca_module
        except Exception as exc:  # noqa: BLE001
            raise TeamBError(f"Team B package недоступен: {exc}") from exc

        original_locator = sca_module.locate_database

        def trusted_locator(path: Any, project: Any) -> Optional[Path]:
            # Never search inside the uploaded ZIP or CWD. Only the configured
            # server-side catalog is trusted.
            return advisory

        project = Project(str(extracted_path))
        try:
            sca_module.locate_database = trusted_locator
            result = team_b_run(str(extracted_path), str(advisory) if advisory else None, project=project)
            bundle = build_bundle(
                str(extracted_path), project, result,
                str(advisory) if advisory else None,
                workspace_id=identity["workspaceId"],
                run_id=identity["runId"],
                snapshot_id=identity["snapshotId"],
                workflow_revision_id=identity["workflowRevisionId"],
            )
        finally:
            sca_module.locate_database = original_locator

        # Rebuild identity against the real intake ZIP and full frozen manifest.
        bundle["identity"] = identity
        for part in [*bundle.get("scanResults", []), bundle.get("codeFacts", {})]:
            if isinstance(part, dict):
                part["identity"] = identity
        for policy in bundle.get("codeFacts", {}).get("policyInputs", []):
            policy["workspaceId"] = workspace
        scan_results = _backend_scan_results(bundle)
        return TeamBPayload(bundle=bundle, scan_results=scan_results)


def persist_team_b_run(*, run_id: str, artifact_id: str, workspace: str,
                       store: CodeFactsStore, adapter: TeamBAdapter) -> Dict[str, Any]:
    """Build and persist local map/run artifact; publication stays in executor."""
    bundle = adapter.get_bundle(run_id)
    if not bundle:
        raise TeamBError("Team B bundle не построен для run")
    mapped = map_from_analysis_bundle(bundle)
    if not mapped:
        raise TeamBError("Team B bundle не маппится в SecurityMapDto")
    payload = {
        "schemaVersion": "cg-run-facts/1",
        "internalRunId": run_id,
        "workspaceId": workspace,
        "artifactId": artifact_id,
        "identity": bundle["identity"],
        "map": mapped["map"],
        "findings": mapped["findings"],
        "analysisBundle": bundle,
        "redactionVersion": "cg-redaction/1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "limitations": [
            "Team B static analyzers; Kotlin/Gradle не исполнялись.",
            "Общий graph compare остаётся inconclusive при expectedScopesVerified=false.",
        ],
    }
    store.save_run(workspace, bundle["identity"]["runId"], payload)
    return payload
