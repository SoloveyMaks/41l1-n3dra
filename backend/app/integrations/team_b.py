"""Optional Team B analyzer engine.

Team B lives in the root ``analyzers`` package. This module is an adapter only:
it never replaces ``backend/app/analyzers``, never executes Kotlin/Gradle, and
builds the Team B AnalysisBundle once per run. The bundle is then normalized to
the intake ZIP identity/full manifest and passed to the existing canonical
publisher.
"""

from __future__ import annotations

import hmac
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.app.analyzers.advisory_catalog import PACKAGED_ADVISORY_DB
from backend.app.codefacts.identity import (
    revision_uuid,
    run_uuid,
    storage_manifest_hash,
)
from backend.app.codefacts.map_builder import ensure_artifact_meta
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


_DEMO_HMAC_KEY = b"cg26-local-demo-key-not-for-production"
_ALLOWED_DIAGNOSTIC_LEVELS = {"info", "warning", "error"}


@dataclass
class TeamBPayload:
    bundle: Dict[str, Any]
    scan_results: Dict[str, ScanResult]
    raw_result: Any = None
    warnings: tuple[str, ...] = ()


def _trusted_advisory_path() -> tuple[Optional[Path], Optional[str]]:
    """Resolve the server-side catalog; an uploaded project never influences it.

    A broken catalog configuration degrades SCA only: Secret and SAST still run
    against the frozen snapshot. The packaged snapshot is never selected by the
    uploaded repository.
    """
    raw = os.getenv("CG26_ADVISORY_DB", "").strip()
    warning: Optional[str] = None
    path: Optional[Path] = PACKAGED_ADVISORY_DB
    if raw:
        if raw.lower().startswith(("http://", "https://")):
            warning = "CG26_ADVISORY_DB URL запрещён; используется встроенный advisory snapshot"
        else:
            configured = Path(raw).resolve()
            root = os.getenv("CG26_ADVISORY_ROOT", "").strip()
            if root:
                trusted_root = Path(root).resolve()
                if trusted_root != configured and trusted_root not in configured.parents:
                    warning = (
                        "CG26_ADVISORY_DB вне CG26_ADVISORY_ROOT; "
                        "используется встроенный advisory snapshot"
                    )
                    configured = None
            if configured is not None and configured.is_file():
                path = configured
            elif configured is not None:
                warning = (
                    "CG26_ADVISORY_DB не найден; используется встроенный доверенный "
                    f"advisory snapshot {PACKAGED_ADVISORY_DB.name}"
                )
    if path is not None and path.is_file():
        if path.stat().st_size > _ADVISORY_MAX_BYTES:
            warning = (
                "advisory snapshot слишком большой; SCA работает без каталога"
            )
            path = None
        return path, warning
    return None, (
        "доверенный advisory snapshot не найден; SCA работает без каталога, "
        "SECRET/SAST продолжают анализ"
    )


def _require_hmac_environment() -> None:
    key = os.getenv("CG26_HMAC_KEY", "").strip()
    version = os.getenv("CG26_HMAC_KEY_VERSION", "").strip()
    if not key:
        raise TeamBError("Team B требует серверный CG26_HMAC_KEY; demo key запрещён")
    if hmac.compare_digest(key.encode("utf-8"), _DEMO_HMAC_KEY):
        raise TeamBError("Team B: известный demo HMAC key запрещён")
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


def _backend_scan_results(bundle: Dict[str, Any], raw_result: Any = None,
                          warnings: tuple[str, ...] = ()) -> Dict[str, ScanResult]:
    raw_by_code: Dict[str, List[Any]] = {}
    for diagnostic in getattr(raw_result, "diagnostics", []) or []:
        raw_by_code.setdefault(str(getattr(diagnostic, "code", "")), []).append(diagnostic)
    results: Dict[str, ScanResult] = {}
    for scan in bundle["scanResults"]:
        category = str(scan["category"])
        coverage = scan["coverage"]
        diagnostics: List[Diagnostic] = []
        for warning in warnings:
            diagnostics.append(Diagnostic(
                level="warning",
                message=warning,
                source="team_b",
                details={"code": "team_b.config-fallback", "category": category},
            ))
        for item in scan.get("diagnostics", []):
            code = str(item.get("code", ""))
            candidates = raw_by_code.get(code, [])
            raw = candidates.pop(0) if candidates else None
            level = str(getattr(raw, "level", "warning") or "warning")
            if level not in _ALLOWED_DIAGNOSTIC_LEVELS:
                level = "warning"
            diagnostics.append(Diagnostic(
                level=level,
                message=str(item.get("message", "")),
                source="team_b",
                details={"code": code, "category": category},
            ))
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
        self._failures: Dict[str, str] = {}
        self._lock = threading.Lock()

    def scan_category(self, *, run_id: str, artifact_id: str, artifact_row: Dict[str, Any],
                      revision_id: str, category: str) -> ScanResult:
        payload = self._payload(run_id, artifact_id, artifact_row, revision_id)
        try:
            return payload.scan_results[category]
        except KeyError as exc:
            raise TeamBError(f"Team B не вернул scan result для {category}") from exc

    def release(self, run_id: str) -> None:
        with self._lock:
            self._cache.pop(run_id, None)
            self._failures.pop(run_id, None)

    def _payload(self, run_id: str, artifact_id: str, artifact_row: Dict[str, Any],
                 revision_id: str) -> TeamBPayload:
        with self._lock:
            cached = self._cache.get(run_id)
            failure = self._failures.get(run_id)
        if cached:
            return cached
        if failure:
            raise TeamBError(failure)
        with _BUILD_LOCK:
            with self._lock:
                cached = self._cache.get(run_id)
                failure = self._failures.get(run_id)
            if cached:
                return cached
            if failure:
                raise TeamBError(failure)
            try:
                payload = self._build(run_id, artifact_id, artifact_row, revision_id)
            except Exception as exc:  # noqa: BLE001 - one failed build serves every category
                with self._lock:
                    self._failures[run_id] = str(exc)
                raise
            with self._lock:
                self._cache[run_id] = payload
            return payload

    def _build(self, run_id: str, artifact_id: str, artifact_row: Dict[str, Any],
               revision_id: str) -> TeamBPayload:
        _require_hmac_environment()
        advisory, advisory_warning = _trusted_advisory_path()
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
        scan_results = _backend_scan_results(
            bundle, result, (advisory_warning,) if advisory_warning else (),
        )
        return TeamBPayload(
            bundle=bundle,
            scan_results=scan_results,
            raw_result=result,
            warnings=(advisory_warning,) if advisory_warning else (),
        )
