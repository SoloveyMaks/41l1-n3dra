"""Read one real ``AnalysisBundle`` 0.1 without trusting invented fields.

Only facts the bundle actually publishes are used: the shared static signature,
each scanner's nested identity/signature and status, coverage scopes and
findings with their ``fp/v1`` fingerprint. Nested identity or signature that
disagrees with the top-level bundle, an unknown status, or a non-``fp/v1``
fingerprint is a contract violation, not a comparison verdict. Nothing is
executed and no repository code is checked out.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .manifest import CATEGORIES, SIGNATURE_KEYS, ReferenceCheckError

BUNDLE_SCHEMA = "0.1"
FINGERPRINT_VERSION = "fp/v1"
_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_SCAN_STATUSES = {"complete", "partial", "failed", "timeout", "unsupported"}
_COVERAGE_STATUSES = {"complete", "partial", "unavailable"}
_SCOPE_STATUSES = {"checked", "partial", "skipped", "error", "unsupported"}
_FINDING_STATUSES = {"confirmed", "potential", "inconclusive"}
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
    schema_version: str
    identity: Mapping[str, str]
    signature: Mapping[str, str | None]
    producer_version: str
    coverage_status: str
    scopes: Mapping[tuple[str, str], str]
    findings: tuple[Mapping[str, Any], ...]

    @property
    def ready(self) -> bool:
        """Failed/cancelled scanners and unavailable coverage cannot support ``pass``.

        ``partial`` is allowed here because per-scope coverage decides whether a
        specific expectation was checked. ``unavailable`` is different: the
        scanner did not inspect its surface at all.
        """

        return self.status in {"complete", "partial"} and self.coverage_status in {
            "complete",
            "partial",
        }


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


def _identity(raw: Any, field: str) -> dict[str, str]:
    source = _mapping(raw, field)
    return {
        key: _text(source.get(key), f"{field}.{key}") for key in _IDENTITY_KEYS
    }


def _signature(raw: Any, field: str) -> tuple[dict[str, str | None], Mapping[str, Any]]:
    source = _mapping(raw, field)
    missing = [key for key in SIGNATURE_KEYS if key not in source]
    if missing:
        raise ReferenceCheckError(
            "INCOMPATIBLE_BUNDLE",
            f"{field} lacks pinned fields: {', '.join(missing)}",
        )
    signature: dict[str, str | None] = {}
    for key in SIGNATURE_KEYS:
        value = source[key]
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ReferenceCheckError(
                "INVALID_BUNDLE", f"{field}.{key} must be a string or null"
            )
        signature[key] = value.strip() if isinstance(value, str) else None
    return signature, source


def _fingerprint(item: Mapping[str, Any], field: str) -> str:
    """Recompute the ``fp/v1`` recipe from the published anchor."""

    anchor = item.get("anchor")
    if not isinstance(anchor, Mapping):
        raise ReferenceCheckError(
            "INCOMPATIBLE_BUNDLE", f"{field}.anchor is required for identity"
        )
    parts = [
        FINGERPRINT_VERSION,
        item.get("category"),
        item.get("ruleId"),
        anchor.get("normalizedPath"),
        anchor.get("symbolKey"),
        anchor.get("normalizedContextHash"),
        anchor.get("role"),
        anchor.get("secretHmac"),
    ]
    if any(part is None for part in parts[1:7]):
        raise ReferenceCheckError(
            "INCOMPATIBLE_BUNDLE", f"{field}.anchor lacks the fp/v1 identity fields"
        )
    if item.get("category") == "SECRET":
        if not isinstance(anchor.get("secretHmac"), str) or not anchor["secretHmac"]:
            raise ReferenceCheckError(
                "INCOMPATIBLE_BUNDLE", f"{field} requires anchor.secretHmac"
            )
    elif anchor.get("secretHmac") is not None:
        raise ReferenceCheckError(
            "INCOMPATIBLE_BUNDLE",
            f"{field} is not SECRET and must not carry anchor.secretHmac",
        )
    recipe = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(recipe.encode("utf-8")).hexdigest()


def _coverage(scan: Mapping[str, Any], field: str) -> tuple[str, dict[tuple[str, str], str]]:
    coverage = _mapping(scan.get("coverage"), f"{field}.coverage")
    status = _text(coverage.get("status"), f"{field}.coverage.status")
    if status not in _COVERAGE_STATUSES:
        raise ReferenceCheckError(
            "INVALID_BUNDLE", f"{field}.coverage.status is unknown: {status}"
        )
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


def _finding(
    raw: Any,
    field: str,
    category: str,
    identity: Mapping[str, str],
) -> Mapping[str, Any]:
    finding = _mapping(raw, field)
    if _text(finding.get("category"), f"{field}.category") != category:
        raise ReferenceCheckError(
            "INVALID_BUNDLE", f"{field} category differs from its scanner"
        )
    _text(finding.get("ruleId"), f"{field}.ruleId")
    _text(finding.get("ruleVersion"), f"{field}.ruleVersion")
    version = _text(
        finding.get("fingerprintVersion"), f"{field}.fingerprintVersion"
    )
    if version != FINGERPRINT_VERSION:
        raise ReferenceCheckError(
            "INCOMPATIBLE_BUNDLE",
            f"{field}.fingerprintVersion is {version!r}, expected {FINGERPRINT_VERSION!r}",
        )
    for key in ("workspaceId", "runId", "snapshotId"):
        if _text(finding.get(key), f"{field}.{key}") != identity[key]:
            raise ReferenceCheckError(
                "INVALID_BUNDLE", f"{field}.{key} differs from the bundle identity"
            )
    status = _text(finding.get("status"), f"{field}.status")
    if status not in _FINDING_STATUSES:
        raise ReferenceCheckError(
            "INVALID_BUNDLE", f"{field}.status is unknown: {status}"
        )
    fingerprint = _text(finding.get("fingerprint"), f"{field}.fingerprint").lower()
    if not _HEX_RE.fullmatch(fingerprint):
        raise ReferenceCheckError(
            "INVALID_BUNDLE", f"{field}.fingerprint is not SHA-256"
        )
    recomputed = _fingerprint(finding, field)
    if recomputed != fingerprint:
        raise ReferenceCheckError(
            "INVALID_BUNDLE",
            f"{field}.fingerprint does not match its fp/v1 anchor",
        )
    location = _mapping(finding.get("primaryLocation"), f"{field}.primaryLocation")
    _text(location.get("path"), f"{field}.primaryLocation.path")
    return finding


def _scan(
    scan: Any,
    index: int,
    top_identity: Mapping[str, str],
    top_signature: Mapping[str, str | None],
) -> ScanOutcome:
    field = f"scanResults[{index}]"
    entry = _mapping(scan, field)
    schema = _text(entry.get("schemaVersion"), f"{field}.schemaVersion")
    if schema != BUNDLE_SCHEMA:
        raise ReferenceCheckError(
            "INCOMPATIBLE_BUNDLE", f"{field}.schemaVersion must be {BUNDLE_SCHEMA!r}"
        )
    identity = _identity(entry.get("identity"), f"{field}.identity")
    for key in _IDENTITY_KEYS:
        if identity[key] != top_identity[key]:
            raise ReferenceCheckError(
                "INVALID_BUNDLE", f"{field}.identity.{key} differs from bundle.identity"
            )
    signature, _ = _signature(entry.get("signature"), f"{field}.signature")
    for key in SIGNATURE_KEYS:
        if signature[key] != top_signature[key]:
            raise ReferenceCheckError(
                "INVALID_BUNDLE", f"{field}.signature.{key} differs from bundle.signature"
            )
    category = _text(entry.get("category"), f"{field}.category")
    if category not in CATEGORIES:
        raise ReferenceCheckError("INVALID_BUNDLE", f"{field}.category is unknown")
    status = _text(entry.get("status"), f"{field}.status")
    if status not in _SCAN_STATUSES:
        raise ReferenceCheckError(
            "INVALID_BUNDLE", f"{field}.status is unknown: {status}"
        )
    producer = _text(entry.get("producerVersion"), f"{field}.producerVersion")
    coverage_status, scopes = _coverage(entry, field)
    raw_findings = entry.get("findings")
    if not isinstance(raw_findings, list):
        raise ReferenceCheckError("INVALID_BUNDLE", f"{field}.findings must be an array")
    findings = tuple(
        _finding(raw, f"{field}.findings[{position}]", category, top_identity)
        for position, raw in enumerate(raw_findings)
    )
    return ScanOutcome(
        category=category,
        status=status,
        schema_version=schema,
        identity=identity,
        signature=signature,
        producer_version=producer,
        coverage_status=coverage_status,
        scopes=scopes,
        findings=findings,
    )


def parse_bundle(raw: Any) -> BundleView:
    """Return a validated bundle view or raise ``ReferenceCheckError``."""

    bundle = _mapping(raw, "bundle")
    schema = _text(bundle.get("schemaVersion"), "bundle.schemaVersion")
    if schema != BUNDLE_SCHEMA:
        raise ReferenceCheckError(
            "INCOMPATIBLE_BUNDLE", f"bundle.schemaVersion must be {BUNDLE_SCHEMA!r}"
        )
    identity = _identity(bundle.get("identity"), "bundle.identity")
    signature, signature_raw = _signature(bundle.get("signature"), "bundle.signature")
    scans = bundle.get("scanResults")
    if not isinstance(scans, list) or not scans:
        raise ReferenceCheckError("INVALID_BUNDLE", "bundle.scanResults must be a non-empty array")
    outcomes: dict[str, ScanOutcome] = {}
    for index, scan in enumerate(scans):
        outcome = _scan(scan, index, identity, signature)
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
