"""Read one real ``AnalysisBundle`` 0.1 without trusting invented fields.

Only facts the bundle actually publishes are used: the shared static signature,
the runner (``producerVersion``) that emitted each scanner outcome, coverage
scopes and findings with their ``fp/v1`` fingerprint. Nothing is executed and
no repository code is checked out.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .manifest import CATEGORIES, SIGNATURE_KEYS, ReferenceCheckError

BUNDLE_SCHEMA = "0.1"
_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_SCOPE_STATUSES = {"checked", "skipped", "error", "not_applicable", "partial"}
_IDENTITY_KEYS = (
    "workspaceId",
    "runId",
    "snapshotId",
    "artifactHash",
    "snapshotManifestHash",
    "workflowRevisionId",
)


@dataclass(frozen=True)
class ScanOutcome:
    category: str
    status: str
    producer_version: str
    coverage_status: str
    scopes: Mapping[tuple[str, str], str]
    findings: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class BundleView:
    schema_version: str
    identity: Mapping[str, str]
    signature: Mapping[str, str | None]
    signature_raw: Mapping[str, Any]
    runner_versions: tuple[str, ...]
    outcomes: Mapping[str, ScanOutcome]

    @property
    def runner_version(self) -> str:
        return self.runner_versions[0] if len(self.runner_versions) == 1 else ",".join(
            self.runner_versions
        )


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReferenceCheckError("INVALID_BUNDLE", f"{field} must be an object")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReferenceCheckError("INVALID_BUNDLE", f"{field} must be a non-empty string")
    return value.strip()


def _fingerprint(item: Mapping[str, Any]) -> str:
    """Recompute the ``fp/v1`` recipe from the published anchor."""

    anchor = item.get("anchor")
    if not isinstance(anchor, Mapping):
        raise ReferenceCheckError(
            "INCOMPATIBLE_BUNDLE", "finding.anchor is required for identity"
        )
    parts = [
        "fp/v1",
        item.get("category"),
        item.get("ruleId"),
        anchor.get("normalizedPath"),
        anchor.get("symbolKey"),
        anchor.get("normalizedContextHash"),
        anchor.get("role"),
        anchor.get("secretHmac"),
    ]
    if any(part is None for part in parts[1:6]):
        raise ReferenceCheckError(
            "INCOMPATIBLE_BUNDLE", "finding.anchor lacks the fp/v1 identity fields"
        )
    recipe = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(recipe.encode("utf-8")).hexdigest()


def _coverage(scan: Mapping[str, Any], field: str) -> tuple[str, dict[tuple[str, str], str]]:
    coverage = _mapping(scan.get("coverage"), f"{field}.coverage")
    status = coverage.get("status")
    if not isinstance(status, str) or not status.strip():
        raise ReferenceCheckError("INVALID_BUNDLE", f"{field}.coverage.status is required")
    scopes = coverage.get("scopes")
    if not isinstance(scopes, list):
        raise ReferenceCheckError("INVALID_BUNDLE", f"{field}.coverage.scopes must be an array")
    index: dict[tuple[str, str], str] = {}
    for position, scope in enumerate(scopes):
        scope_field = f"{field}.coverage.scopes[{position}]"
        entry = _mapping(scope, scope_field)
        path = _text(entry.get("path"), f"{scope_field}.path")
        rule = _text(entry.get("ruleId"), f"{scope_field}.ruleId")
        scope_status = _text(entry.get("status"), f"{scope_field}.status")
        if scope_status not in _SCOPE_STATUSES:
            raise ReferenceCheckError(
                "INVALID_BUNDLE", f"{scope_field}.status is unknown: {scope_status}"
            )
        key = (path, rule)
        if key in index:
            raise ReferenceCheckError(
                "INVALID_BUNDLE", f"{scope_field} duplicates path/rule coverage"
            )
        index[key] = scope_status
    return status, index


def _scan(scan: Any, index: int) -> ScanOutcome:
    field = f"scanResults[{index}]"
    entry = _mapping(scan, field)
    category = _text(entry.get("category"), f"{field}.category")
    if category not in CATEGORIES:
        raise ReferenceCheckError("INVALID_BUNDLE", f"{field}.category is unknown")
    status = _text(entry.get("status"), f"{field}.status")
    producer = _text(entry.get("producerVersion"), f"{field}.producerVersion")
    coverage_status, scopes = _coverage(entry, field)
    raw_findings = entry.get("findings")
    if not isinstance(raw_findings, list):
        raise ReferenceCheckError("INVALID_BUNDLE", f"{field}.findings must be an array")
    findings: list[Mapping[str, Any]] = []
    for position, raw in enumerate(raw_findings):
        finding_field = f"{field}.findings[{position}]"
        finding = _mapping(raw, finding_field)
        if _text(finding.get("category"), f"{finding_field}.category") != category:
            raise ReferenceCheckError(
                "INVALID_BUNDLE", f"{finding_field} category differs from its scanner"
            )
        _text(finding.get("ruleId"), f"{finding_field}.ruleId")
        fingerprint = _text(finding.get("fingerprint"), f"{finding_field}.fingerprint").lower()
        if not _HEX_RE.fullmatch(fingerprint):
            raise ReferenceCheckError(
                "INVALID_BUNDLE", f"{finding_field}.fingerprint is not SHA-256"
            )
        recomputed = _fingerprint(finding)
        if recomputed != fingerprint:
            raise ReferenceCheckError(
                "INVALID_BUNDLE",
                f"{finding_field}.fingerprint does not match its fp/v1 anchor",
            )
        location = _mapping(
            finding.get("primaryLocation"), f"{finding_field}.primaryLocation"
        )
        _text(location.get("path"), f"{finding_field}.primaryLocation.path")
        findings.append(finding)
    return ScanOutcome(
        category=category,
        status=status,
        producer_version=producer,
        coverage_status=coverage_status,
        scopes=scopes,
        findings=tuple(findings),
    )


def parse_bundle(raw: Any) -> BundleView:
    """Return a validated bundle view or raise ``ReferenceCheckError``."""

    bundle = _mapping(raw, "bundle")
    schema = _text(bundle.get("schemaVersion"), "bundle.schemaVersion")
    if schema != BUNDLE_SCHEMA:
        raise ReferenceCheckError(
            "INCOMPATIBLE_BUNDLE", f"bundle.schemaVersion must be {BUNDLE_SCHEMA!r}"
        )
    identity_raw = _mapping(bundle.get("identity"), "bundle.identity")
    identity = {
        key: _text(identity_raw.get(key), f"bundle.identity.{key}") for key in _IDENTITY_KEYS
    }
    signature_raw = _mapping(bundle.get("signature"), "bundle.signature")
    missing = [key for key in SIGNATURE_KEYS if key not in signature_raw]
    if missing:
        raise ReferenceCheckError(
            "INCOMPATIBLE_BUNDLE",
            f"bundle.signature lacks pinned fields: {', '.join(missing)}",
        )
    signature: dict[str, str | None] = {}
    for key in SIGNATURE_KEYS:
        value = signature_raw[key]
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ReferenceCheckError(
                "INVALID_BUNDLE", f"bundle.signature.{key} must be a string or null"
            )
        signature[key] = value.strip() if isinstance(value, str) else None
    scans = bundle.get("scanResults")
    if not isinstance(scans, list) or not scans:
        raise ReferenceCheckError("INVALID_BUNDLE", "bundle.scanResults must be a non-empty array")
    outcomes: dict[str, ScanOutcome] = {}
    for index, scan in enumerate(scans):
        outcome = _scan(scan, index)
        if outcome.category in outcomes:
            raise ReferenceCheckError(
                "INVALID_BUNDLE", f"duplicate scanner outcome: {outcome.category}"
            )
        outcomes[outcome.category] = outcome
    if set(outcomes) != set(CATEGORIES):
        raise ReferenceCheckError(
            "INCOMPATIBLE_BUNDLE",
            "bundle must contain SECRET, SCA and SAST scanner outcomes",
        )
    runners = tuple(sorted({outcome.producer_version for outcome in outcomes.values()}))
    return BundleView(
        schema_version=schema,
        identity=identity,
        signature=signature,
        signature_raw=signature_raw,
        runner_versions=runners,
        outcomes=outcomes,
    )