"""Validate a versioned reference-suite manifest.

The manifest is data, not code: it names the reference branch/commit, pins the
analyzer signature and runner version that produced the reference bundle, and
lists expected findings plus safe controls. It is stored in the repository and
read by the comparison runner; nothing from it is executed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

SUITE_SCHEMA = "reference-suite/v1"
CATEGORIES = ("SECRET", "SCA", "SAST")
SEVERITIES = {"critical", "high", "medium", "low", "info"}
SIGNATURE_KEYS = (
    "parserVersion",
    "parserHash",
    "rulesVersion",
    "rulesHash",
    "advisoryVersion",
    "advisoryHash",
    "configVersion",
    "scanConfigHash",
    "domainPolicyRevisionId",
    "domainPolicyVersion",
    "domainPolicyHash",
    "hmacKeyVersion",
)
_SHA_RE = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")
_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


class ReferenceCheckError(ValueError):
    """A rejected input, with a machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ExpectedFinding:
    id: str
    category: str
    rule_id: str
    path: str
    severity: str | None
    fingerprint: str | None
    symbol_key: str | None


@dataclass(frozen=True)
class ExpectedControl:
    id: str
    category: str
    rule_id: str
    path: str
    description: str | None


@dataclass(frozen=True)
class ReferenceSuite:
    suite_id: str
    suite_version: str
    project_id: str
    repository: str | None
    branch: str
    sha: str
    snapshot_manifest_hash: str | None
    artifact_hash: str | None
    bundle_sha256: str | None
    runner_version: str
    signature: Mapping[str, str | None]
    bundle_schema_version: str
    expected_findings: tuple[ExpectedFinding, ...]
    expected_controls: tuple[ExpectedControl, ...]


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReferenceCheckError("INVALID_MANIFEST", f"{field} must be an object")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReferenceCheckError("INVALID_MANIFEST", f"{field} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _text(value, field)


def _sha(value: Any, field: str, *, allow_hex64: bool = True) -> str:
    text = _text(value, field).lower()
    if not (_SHA_RE.fullmatch(text) and (allow_hex64 or len(text) == 40)):
        raise ReferenceCheckError(
            "INVALID_PROVENANCE", f"{field} must be a 40-character commit SHA"
        )
    return text


def _optional_sha(value: Any, field: str) -> str | None:
    if value is None:
        return None
    text = _text(value, field).lower()
    if not _HEX_RE.fullmatch(text):
        raise ReferenceCheckError(
            "INVALID_PROVENANCE", f"{field} must be a 64-character SHA-256"
        )
    return text


def _category(value: Any, field: str) -> str:
    text = _text(value, field)
    if text not in CATEGORIES:
        raise ReferenceCheckError(
            "INVALID_MANIFEST", f"{field} must be one of {', '.join(CATEGORIES)}"
        )
    return text


def _signature(raw: Any) -> dict[str, str | None]:
    source = _mapping(raw, "reference.signature")
    missing = [key for key in SIGNATURE_KEYS if key not in source]
    if missing:
        raise ReferenceCheckError(
            "INVALID_PROVENANCE",
            f"reference.signature is missing pinned fields: {', '.join(missing)}",
        )
    signature: dict[str, str | None] = {}
    for key in SIGNATURE_KEYS:
        value = source[key]
        if value is None:
            signature[key] = None
        elif isinstance(value, str) and value.strip():
            signature[key] = value.strip()
        else:
            raise ReferenceCheckError(
                "INVALID_PROVENANCE",
                f"reference.signature.{key} must be a non-empty string or null",
            )
    policy = [signature[key] for key in (
        "domainPolicyRevisionId", "domainPolicyVersion", "domainPolicyHash"
    )]
    if any(item is None for item in policy) and not all(item is None for item in policy):
        raise ReferenceCheckError(
            "INVALID_PROVENANCE", "domain policy identity must be all null or all pinned"
        )
    return signature


def _items(raw: Any, field: str) -> list[Mapping[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise ReferenceCheckError("INVALID_MANIFEST", f"{field} must be a non-empty array")
    if any(not isinstance(item, Mapping) for item in raw):
        raise ReferenceCheckError("INVALID_MANIFEST", f"{field} must contain objects")
    return list(raw)


def _findings(raw: Any) -> tuple[ExpectedFinding, ...]:
    result: list[ExpectedFinding] = []
    seen_ids: set[str] = set()
    seen_identity: set[tuple[str, str]] = set()
    for index, item in enumerate(_items(raw, "reference.expectedFindings")):
        field = f"reference.expectedFindings[{index}]"
        identifier = _text(item.get("id"), f"{field}.id")
        if identifier in seen_ids:
            raise ReferenceCheckError("INVALID_MANIFEST", f"duplicate finding id: {identifier}")
        seen_ids.add(identifier)
        category = _category(item.get("category"), f"{field}.category")
        rule_id = _text(item.get("ruleId"), f"{field}.ruleId")
        path = _text(item.get("path"), f"{field}.path")
        severity = item.get("severity")
        if severity is not None:
            if not isinstance(severity, str) or severity.strip().lower() not in SEVERITIES:
                raise ReferenceCheckError(
                    "INVALID_MANIFEST", f"{field}.severity is not a known severity"
                )
            severity = severity.strip().lower()
        fingerprint = item.get("fingerprint")
        if fingerprint is not None:
            if not isinstance(fingerprint, str) or not _HEX_RE.fullmatch(fingerprint.lower()):
                raise ReferenceCheckError(
                    "INVALID_MANIFEST", f"{field}.fingerprint must be a SHA-256 hex digest"
                )
            fingerprint = fingerprint.lower()
        symbol_key = _optional_text(item.get("symbolKey"), f"{field}.symbolKey")
        identity = (category, fingerprint or f"{rule_id}:{path}:{symbol_key or ''}")
        if identity in seen_identity:
            raise ReferenceCheckError(
                "INVALID_MANIFEST", f"{field} repeats another expected finding"
            )
        seen_identity.add(identity)
        result.append(ExpectedFinding(
            id=identifier,
            category=category,
            rule_id=rule_id,
            path=path,
            severity=severity,
            fingerprint=fingerprint,
            symbol_key=symbol_key,
        ))
    return tuple(result)


def _controls(raw: Any) -> tuple[ExpectedControl, ...]:
    result: list[ExpectedControl] = []
    seen_ids: set[str] = set()
    seen_identity: set[tuple[str, str, str]] = set()
    for index, item in enumerate(_items(raw, "reference.expectedControls")):
        field = f"reference.expectedControls[{index}]"
        identifier = _text(item.get("id"), f"{field}.id")
        if identifier in seen_ids:
            raise ReferenceCheckError("INVALID_MANIFEST", f"duplicate control id: {identifier}")
        seen_ids.add(identifier)
        category = _category(item.get("category"), f"{field}.category")
        rule_id = _text(item.get("ruleId"), f"{field}.ruleId")
        path = _text(item.get("path"), f"{field}.path")
        identity = (category, rule_id, path)
        if identity in seen_identity:
            raise ReferenceCheckError(
                "INVALID_MANIFEST", f"{field} repeats another control target"
            )
        seen_identity.add(identity)
        description = _optional_text(item.get("description"), f"{field}.description")
        result.append(ExpectedControl(
            id=identifier,
            category=category,
            rule_id=rule_id,
            path=path,
            description=description,
        ))
    return tuple(result)


def parse_manifest(raw: Any) -> ReferenceSuite:
    """Return a validated suite or raise ``ReferenceCheckError``."""

    manifest = _mapping(raw, "manifest")
    if manifest.get("schemaVersion") != SUITE_SCHEMA:
        raise ReferenceCheckError(
            "UNSUPPORTED_MANIFEST",
            f"schemaVersion must be {SUITE_SCHEMA!r}",
        )
    project = _mapping(manifest.get("project"), "project")
    reference = _mapping(manifest.get("reference"), "reference")
    compatibility = _mapping(manifest.get("compatibility"), "compatibility")
    bundle_schema = _text(
        compatibility.get("bundleSchemaVersion"),
        "compatibility.bundleSchemaVersion",
    )
    if bundle_schema != "0.1":
        raise ReferenceCheckError(
            "UNSUPPORTED_MANIFEST",
            "only AnalysisBundle 0.1 is supported",
        )
    return ReferenceSuite(
        suite_id=_text(manifest.get("suiteId"), "suiteId"),
        suite_version=_text(manifest.get("suiteVersion"), "suiteVersion"),
        project_id=_text(project.get("id"), "project.id"),
        repository=_optional_text(project.get("repository"), "project.repository"),
        branch=_text(reference.get("branch"), "reference.branch"),
        sha=_sha(reference.get("sha"), "reference.sha", allow_hex64=False),
        snapshot_manifest_hash=_optional_sha(
            reference.get("snapshotManifestHash"), "reference.snapshotManifestHash"
        ),
        artifact_hash=_optional_sha(
            reference.get("artifactHash"), "reference.artifactHash"
        ),
        bundle_sha256=_optional_sha(reference.get("bundleSha256"), "reference.bundleSha256"),
        runner_version=_text(reference.get("runnerVersion"), "reference.runnerVersion"),
        signature=_signature(reference.get("signature")),
        bundle_schema_version=bundle_schema,
        expected_findings=_findings(reference.get("expectedFindings")),
        expected_controls=_controls(reference.get("expectedControls")),
    )