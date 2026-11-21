"""JSONC-compatible report for Telegram delivery.

Deterministic findings are copied from runtime.findings after a second masking
pass. AI_SCAN observations are included only under `aiHypotheses`, explicitly
marked not verified; they are never merged into deterministic findings or a
verdict.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.app.ai.provider import mask_sensitive_text
from backend.app.codefacts.identity import data_root
from backend.app.codefacts.source import mask_text
from backend.app.storage.database import DatabaseRepository

JSONC_VERSION = "nedra-telegram-jsonc/1"
MAX_JSONC_BYTES = 10 * 1024 * 1024
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


@dataclass(frozen=True)
class JsoncReport:
    filename: str
    content: bytes
    sha256: str
    finding_count: int
    ai_hypothesis_count: int


def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _safe_location(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return {
        "path": value.get("path"),
        "startLine": value.get("startLine"),
        "endLine": value.get("endLine"),
        "startColumn": value.get("startColumn"),
        "endColumn": value.get("endColumn"),
        "byteRange": value.get("byteRange"),
    }


def _safe_trace(value: Any) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    if not isinstance(value, list):
        return result
    for step in value:
        if not isinstance(step, dict):
            continue
        result.append({
            "step": step.get("step"),
            "description": mask_sensitive_text(str(step.get("description") or "")),
            "location": _safe_location(step.get("location")),
        })
    return result


def _safe_finding(finding: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": finding.get("id"),
        "fingerprint": finding.get("fingerprint"),
        "analyzer": finding.get("analyzer"),
        "ruleId": finding.get("ruleId") or finding.get("rule_id"),
        "ruleVersion": finding.get("ruleVersion") or finding.get("rule_version"),
        "severity": finding.get("severity"),
        "severityBasis": mask_sensitive_text(str(finding.get("severityBasis") or "")),
        "confidence": finding.get("confidence"),
        "confidenceBasis": mask_sensitive_text(str(finding.get("confidenceBasis") or "")),
        "status": finding.get("status"),
        "primaryLocation": _safe_location(finding.get("primaryLocation")),
        "relatedLocations": [
            _safe_location(item) for item in (finding.get("relatedLocations") or [])
        ],
        "trace": _safe_trace(finding.get("trace")),
        "redactedEvidence": mask_text(str(finding.get("redactedEvidence") or "")),
        "advisoryIds": list(finding.get("advisoryIds") or []),
        "coverageWarnings": [
            mask_sensitive_text(str(item)) for item in (finding.get("coverageWarnings") or [])
        ],
    }


def _latest_ai_scan(db: DatabaseRepository, run_id: str) -> Dict[str, Any]:
    for node_run in reversed(db.get_node_runs(run_id)):
        if node_run.get("kind") != "ai_scan":
            continue
        output = node_run.get("output_data_masked") or {}
        result = output.get("ai_scan_result") if isinstance(output, dict) else None
        if isinstance(result, dict):
            return result
    return {}


def _safe_ai_hypotheses(result: Dict[str, Any]) -> Dict[str, Any]:
    observations = []
    for observation in result.get("observations") or []:
        if not isinstance(observation, dict):
            continue
        observations.append({
            "id": observation.get("id"),
            "origin": observation.get("origin"),
            "ruleClass": observation.get("ruleClass"),
            "severity": observation.get("severity"),
            "confidence": observation.get("confidence"),
            "status": observation.get("status"),
            "primaryLocation": _safe_location(observation.get("primaryLocation")),
            "redactedEvidence": mask_text(str(observation.get("redactedEvidence") or "")),
            "rationale": mask_sensitive_text(str(observation.get("rationale") or "")),
            "remediation": mask_sensitive_text(str(observation.get("remediation") or "")),
        })
    return {
        "verified": False,
        "countsAsFindings": False,
        "note": "AI hypotheses are not confirmed by deterministic scanners and must not change the verdict.",
        "status": result.get("status", "disabled"),
        "coverage": result.get("coverage"),
        "provenance": result.get("provenance"),
        "observations": observations,
    }


def _jsonc_path(workspace: str, run_id: str) -> Path:
    try:
        ws = str(uuid.UUID(str(workspace)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("workspace должен быть UUID") from exc
    if not _RUN_ID_RE.fullmatch(str(run_id)):
        raise ValueError("Некорректный run_id для jsonc")
    return data_root() / "telegram" / "reports" / ws / f"{run_id}.jsonc"


def build_jsonc_report(
    db: DatabaseRepository,
    run_id: str,
    workspace: str,
) -> JsoncReport:
    """Build a bounded, redacted JSONC-compatible report for one run."""
    run = db.get_run(run_id)
    if not run:
        raise ValueError("run не найден в текущем workspace")
    findings = db.get_findings(run_id)
    report = db.get_report(run_id)
    ai = _latest_ai_scan(db, run_id)
    hypotheses = _safe_ai_hypotheses(ai)

    document = {
        "format": JSONC_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "notice": (
            "This is a JSONC-compatible scanner report. Deterministic findings are "
            "separate from AI hypotheses; AI hypotheses are not verified."
        ),
        "run": {
            "id": run.get("id"),
            "workflowId": run.get("workflow_id"),
            "revisionId": run.get("revision_id"),
            "artifactId": run.get("artifact_id"),
            "triggerType": run.get("trigger_type"),
            "executionStatus": run.get("execution_status"),
            "securityVerdict": run.get("security_verdict"),
            "startedAt": _iso(run.get("started_at")),
            "finishedAt": _iso(run.get("finished_at")),
            "counts": {
                "total": run.get("total_findings", 0),
                "critical": run.get("critical_count", 0),
                "high": run.get("high_count", 0),
                "medium": run.get("medium_count", 0),
                "low": run.get("low_count", 0),
                "info": run.get("info_count", 0),
            },
        },
        "deterministic": {
            "count": len(findings),
            "findings": [_safe_finding(item) for item in findings],
        },
        "aiHypotheses": hypotheses,
        "securityPassport": (report or {}).get("passport"),
        "coverageNote": "Coverage and verdict are deterministic; AI hypotheses do not alter them.",
    }
    content = json.dumps(document, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    if len(content) > MAX_JSONC_BYTES:
        raise ValueError("JSONC report exceeds the delivery limit")
    digest = hashlib.sha256(content).hexdigest()
    return JsoncReport(
        filename=f"nedra-{run_id}.jsonc",
        content=content,
        sha256=digest,
        finding_count=len(findings),
        ai_hypothesis_count=len(hypotheses.get("observations") or []),
    )


def write_jsonc_report(report: JsoncReport, workspace: str, run_id: str) -> Path:
    path = _jsonc_path(workspace, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd, temp_name = tempfile.mkstemp(prefix=".jsonc-", dir=str(path.parent))
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(report.content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass
    return path