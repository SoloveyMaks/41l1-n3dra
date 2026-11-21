"""Strict grounding validator for AI vulnerability hypotheses.

This is intentionally separate from `AiResponseValidator`: that validator can
only explain existing finding IDs, while this one must accept genuinely new
observations and prove their source anchor before they are shown.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set

from backend.app.ai.provider import mask_sensitive_text
from backend.app.ai.scan_prompt import RULE_CLASSES
from backend.app.ai.scan_selection import SelectedFile, SelectionResult
from backend.app.codefacts.identity import canonical_json
from backend.app.codefacts.source import mask_text, offset_at
from backend.app.contracts.schemas import (
    AiObservation,
    AiObservationGrounding,
    Confidence,
    Finding,
    FindingStatus,
    Location,
    Severity,
)

VALIDATOR_VERSION = "ai-scan-validator/1"

CVE_RE = re.compile(r"CVE-\d{4}-\d{4,7}", re.IGNORECASE)
_ALLOWED_SEVERITIES = {"low", "medium", "high"}
_MAX_QUOTE_CHARS = 400
_MAX_TEXT_CHARS = 1200
_MAX_ERRORS = 40

_RULE_TO_DETERMINISTIC: Dict[str, Set[str]] = {
    "SQL_INJECTION": {"KOTLIN-SQL-INJECTION", "CF-SQL-PARAM-INTERPOLATION"},
    "PATH_TRAVERSAL": {"KOTLIN-PATH-TRAVERSAL"},
    "COMMAND_INJECTION": {"KOTLIN-COMMAND-INJECTION"},
    "IDOR": {"KOTLIN-IDOR", "CF-OWNER-CHECK-UNPROVEN"},
    "OPEN_ENDPOINT": {"KOTLIN-INSECURE-ENDPOINT", "CF-ROUTE-UNAUTH-PII"},
    "SSRF": set(),
    "CRYPTO": set(),
    "TLS": set(),
    "CORS": set(),
}


@dataclass
class AiScanValidation:
    shape_valid: bool
    observations: List[AiObservation] = field(default_factory=list)
    suppressed_duplicates: int = 0
    errors: List[str] = field(default_factory=list)


def _add_error(errors: List[str], code: str, detail: str = "") -> None:
    if len(errors) >= _MAX_ERRORS:
        return
    text = f"{code}: {detail}" if detail else code
    errors.append(mask_sensitive_text(text)[:300])


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start <= b_end and b_start <= a_end


def _duplicates_deterministic(
    rule_class: str,
    path: str,
    start_line: int,
    end_line: int,
    findings: Sequence[Finding],
) -> bool:
    allowed_rules = _RULE_TO_DETERMINISTIC.get(rule_class, set())
    if not allowed_rules:
        return False
    for finding in findings:
        if finding.ruleId not in allowed_rules:
            continue
        for location in [finding.primaryLocation, *finding.relatedLocations]:
            if location.path == path and _overlaps(
                start_line, end_line, location.startLine, location.endLine
            ):
                return True
    return False


def _server_id(workspace: str, run_id: str, ai_fp: str) -> str:
    return "aiobs_" + str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"cg-aiobs:{workspace}:{run_id}:{ai_fp}",
    ))


def _fingerprint(rule_class: str, path: str, start_byte: int, end_byte: int, quote: str) -> str:
    quote_sha = hashlib.sha256(quote.encode("utf-8")).hexdigest()
    payload = ["ai-fp/v1", rule_class, path, start_byte, end_byte, quote_sha]
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _locate_quote(
    selected: SelectedFile,
    declared_start: int,
    declared_end: int,
    quote: str,
) -> Optional[Dict[str, Any]]:
    """Return a server-derived location only when the quote is exact and unique."""
    source = selected.source
    lines = source.text.split("\n")
    if declared_start < selected.start_line or declared_end > selected.end_line:
        return None
    if declared_start < 1 or declared_end > len(lines) or declared_end < declared_start:
        return None
    window = "\n".join(lines[declared_start - 1:declared_end])
    first = window.find(quote)
    if first < 0:
        return None
    if window.find(quote, first + 1) >= 0:
        return None

    before = window[:first]
    line_delta = before.count("\n")
    quote_start_line = declared_start + line_delta
    quote_end_line = quote_start_line + quote.count("\n")
    if quote_start_line != declared_start or quote_end_line != declared_end:
        return None

    start_column = len(before.rsplit("\n", 1)[-1]) + 1
    last_segment = quote.rsplit("\n", 1)[-1]
    end_column = len(last_segment) + 1
    try:
        start_byte = offset_at(source.text, quote_start_line, start_column)
    except (ValueError, IndexError):
        return None
    end_byte = start_byte + len(quote.encode("utf-8"))
    if start_byte < 0 or end_byte < start_byte or end_byte > len(source.raw):
        return None
    return {
        "startLine": quote_start_line,
        "endLine": quote_end_line,
        "startColumn": start_column,
        "endColumn": end_column,
        "byteStart": start_byte,
        "byteEnd": end_byte,
        "window": window,
    }


def validate_ai_scan_response(
    data: Any,
    selection: SelectionResult,
    deterministic_findings: Sequence[Finding],
    workspace: str,
    run_id: str,
    max_observations: int,
) -> AiScanValidation:
    errors: List[str] = []
    if not isinstance(data, dict):
        return AiScanValidation(shape_valid=False, errors=["AI_RESPONSE_NOT_OBJECT"])

    raw_observations = data.get("observations")
    if not isinstance(raw_observations, list):
        return AiScanValidation(shape_valid=False, errors=["AI_RESPONSE_OBSERVATIONS_NOT_LIST"])

    observations: List[AiObservation] = []
    by_ref = selection.by_ref
    seen_fingerprints: Set[str] = set()
    suppressed = 0

    for index, raw in enumerate(raw_observations):
        if len(observations) >= max_observations:
            _add_error(errors, "AI_TOO_MANY_OBSERVATIONS", f"index {index}")
            break
        if not isinstance(raw, dict):
            _add_error(errors, "AI_OBSERVATION_NOT_OBJECT", f"index {index}")
            continue

        file_ref = raw.get("fileRef")
        if not isinstance(file_ref, str) or file_ref not in by_ref:
            _add_error(errors, "AI_REF_UNKNOWN", f"index {index}")
            continue
        selected = by_ref[file_ref]

        rule_class = raw.get("ruleClass")
        if not isinstance(rule_class, str):
            _add_error(errors, "AI_RULE_MISSING", f"index {index}")
            continue
        rule_class = rule_class.strip().upper().replace("-", "_").replace(" ", "_")
        if rule_class not in RULE_CLASSES:
            _add_error(errors, "AI_RULE_UNKNOWN", rule_class)
            continue

        severity_raw = raw.get("severity")
        if not isinstance(severity_raw, str) or severity_raw.strip().lower() not in _ALLOWED_SEVERITIES:
            _add_error(errors, "AI_SEVERITY_FORBIDDEN", f"index {index}")
            continue
        severity = Severity(severity_raw.strip().lower())

        start_line = raw.get("startLine")
        end_line = raw.get("endLine")
        if not _is_int(start_line) or not _is_int(end_line):
            _add_error(errors, "AI_LINE_NOT_INTEGER", f"index {index}")
            continue
        if start_line < 1 or end_line < start_line:
            _add_error(errors, "AI_LINE_OUT_OF_RANGE", f"{start_line}-{end_line}")
            continue
        if start_line < selected.start_line or end_line > selected.end_line:
            _add_error(errors, "AI_LINE_OUTSIDE_EXCERPT", f"{start_line}-{end_line}")
            continue

        quote_raw = raw.get("quote")
        if not isinstance(quote_raw, str):
            _add_error(errors, "AI_QUOTE_MISSING", f"index {index}")
            continue
        quote = quote_raw.strip()
        if not quote or len(quote) > _MAX_QUOTE_CHARS:
            _add_error(errors, "AI_QUOTE_INVALID", f"length {len(quote)}")
            continue

        rationale_raw = raw.get("rationale")
        remediation_raw = raw.get("remediation")
        if not isinstance(rationale_raw, str) or not rationale_raw.strip():
            _add_error(errors, "AI_RATIONALE_MISSING", f"index {index}")
            continue
        if not isinstance(remediation_raw, str) or not remediation_raw.strip():
            _add_error(errors, "AI_REMEDIATION_MISSING", f"index {index}")
            continue
        combined = f"{quote}\n{rationale_raw}\n{remediation_raw}"
        if CVE_RE.search(combined):
            _add_error(errors, "AI_CVE_FORBIDDEN", f"index {index}")
            continue

        located = _locate_quote(selected, start_line, end_line, quote)
        if located is None:
            _add_error(errors, "AI_QUOTE_NOT_FOUND", f"{selected.path}:{start_line}-{end_line}")
            continue

        ai_fp = _fingerprint(
            rule_class, selected.path,
            located["byteStart"], located["byteEnd"], quote,
        )
        if ai_fp in seen_fingerprints:
            suppressed += 1
            continue
        if _duplicates_deterministic(
            rule_class, selected.path,
            located["startLine"], located["endLine"],
            deterministic_findings,
        ):
            suppressed += 1
            continue
        seen_fingerprints.add(ai_fp)

        rationale = mask_sensitive_text(rationale_raw.strip())[:_MAX_TEXT_CHARS]
        remediation = mask_sensitive_text(remediation_raw.strip())[:_MAX_TEXT_CHARS]
        observations.append(AiObservation(
            id=_server_id(workspace, run_id, ai_fp),
            origin="ai_hypothesis",
            ruleClass=rule_class,
            severity=severity,
            confidence=Confidence.POTENTIAL,
            status=FindingStatus.POTENTIAL,
            primaryLocation=Location(
                path=selected.path,
                startLine=located["startLine"],
                endLine=located["endLine"],
                startColumn=located["startColumn"],
                endColumn=located["endColumn"],
                byteRange=[located["byteStart"], located["byteEnd"]],
            ),
            redactedEvidence=mask_text(quote),
            rationale=rationale,
            remediation=remediation,
            grounding=AiObservationGrounding(
                fileRef=selected.file_ref,
                sourceHash=selected.source.sha256,
                quoteSha256=hashlib.sha256(quote.encode("utf-8")).hexdigest(),
                startByte=located["byteStart"],
                endByte=located["byteEnd"],
                modelStartLine=start_line,
                modelEndLine=end_line,
            ),
        ))

    return AiScanValidation(
        shape_valid=True,
        observations=observations,
        suppressed_duplicates=suppressed,
        errors=errors,
    )