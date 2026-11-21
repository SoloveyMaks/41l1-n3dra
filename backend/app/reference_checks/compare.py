"""Compare a pinned reference suite with one analyzer bundle.

Compatibility is exact on the bundle's static signature and runner version.
Coverage is checked per expected path/rule before any finding is called missing:
a scope that was not ``checked`` yields ``inconclusive``, never ``pass`` and
never a fabricated ``fail``.
"""

from __future__ import annotations

from typing import Any, Mapping

from .bundle import BundleView, ScanOutcome, parse_bundle
from .manifest import (
    CATEGORIES,
    SIGNATURE_KEYS,
    ExpectedControl,
    ExpectedFinding,
    ReferenceCheckError,
    ReferenceSuite,
    parse_manifest,
)

COMPARATOR_VERSION = "reference-checks/1"


def _signature_reasons(suite: ReferenceSuite, bundle: BundleView) -> list[str]:
    reasons: list[str] = []
    for key in SIGNATURE_KEYS:
        expected = suite.signature[key]
        actual = bundle.signature[key]
        if expected != actual:
            reasons.append(f"{key} differs: reference {expected!r}, bundle {actual!r}")
    return reasons


def _compatibility(
    suite: ReferenceSuite,
    bundle: BundleView,
    target_sha: str,
    target_project_id: str,
) -> list[str]:
    reasons: list[str] = []
    if suite.bundle_schema_version != bundle.schema_version:
        reasons.append(
            f"bundle schema {bundle.schema_version} is not compatible with "
            f"{suite.bundle_schema_version}"
        )
    if target_project_id != suite.project_id:
        reasons.append(
            f"target project {target_project_id!r} differs from reference project "
            f"{suite.project_id!r}"
        )
    if len(bundle.runner_versions) != 1:
        reasons.append(
            "bundle mixes runner versions: " + ", ".join(bundle.runner_versions)
        )
    elif bundle.runner_versions[0] != suite.runner_version:
        reasons.append(
            f"runner version differs: reference {suite.runner_version!r}, "
            f"bundle {bundle.runner_versions[0]!r}"
        )
    reasons.extend(_signature_reasons(suite, bundle))
    if len(target_sha) not in {40, 64}:
        reasons.append("target sha is not a 40/64 character commit SHA")
    return reasons


def _severity(value: Any) -> str | None:
    return value.strip().lower() if isinstance(value, str) and value.strip() else None


def _matches(expected: ExpectedFinding, actual: Mapping[str, Any]) -> bool:
    if expected.category != actual.get("category"):
        return False
    if expected.rule_id != actual.get("ruleId"):
        return False
    location = actual.get("primaryLocation")
    path = location.get("path") if isinstance(location, Mapping) else None
    if expected.path != path:
        return False
    anchor = actual.get("anchor")
    if expected.symbol_key is not None:
        symbol = anchor.get("symbolKey") if isinstance(anchor, Mapping) else None
        if expected.symbol_key != symbol:
            return False
    if expected.severity is not None and expected.severity != _severity(actual.get("severity")):
        return False
    return True


def _expected_row(
    expected: ExpectedFinding,
    outcome: ScanOutcome,
    actual_findings: list[Mapping[str, Any]],
) -> tuple[dict[str, Any], int | None, bool]:
    """Return (row, matched index, hard failure) for one expected finding."""

    scope = outcome.scopes.get((expected.path, expected.rule_id))
    row: dict[str, Any] = {
        "id": expected.id,
        "category": expected.category,
        "ruleId": expected.rule_id,
        "path": expected.path,
        "expectedFingerprint": expected.fingerprint,
    }
    if scope is None:
        return {
            **row,
            "status": "inconclusive",
            "reason": "bundle has no coverage scope for this path and rule",
        }, None, False
    if scope != "checked":
        return {
            **row,
            "status": "inconclusive",
            "reason": f"coverage scope is {scope}, not checked",
        }, None, False

    candidates = [
        index for index, actual in enumerate(actual_findings) if _matches(expected, actual)
    ]
    if expected.fingerprint is not None:
        candidates = [
            index
            for index in candidates
            if str(actual_findings[index].get("fingerprint", "")).lower()
            == expected.fingerprint
        ]
    if not candidates:
        return {
            **row,
            "status": "missing",
            "reason": "expected finding is absent from a checked scope",
        }, None, True
    if len(candidates) > 1:
        return {
            **row,
            "status": "inconclusive",
            "reason": "several actual findings match one reference identity",
        }, None, False
    matched = actual_findings[candidates[0]]
    return {
        **row,
        "status": "matched",
        "actualFingerprint": matched.get("fingerprint"),
        "actualSeverity": _severity(matched.get("severity")),
        "reason": "expected finding matched inside a checked scope",
    }, candidates[0], False


def _control_row(
    control: ExpectedControl,
    outcome: ScanOutcome,
) -> tuple[dict[str, Any], bool]:
    row: dict[str, Any] = {
        "id": control.id,
        "category": control.category,
        "ruleId": control.rule_id,
        "path": control.path,
    }
    scope = outcome.scopes.get((control.path, control.rule_id))
    if scope is None:
        return {
            **row,
            "status": "inconclusive",
            "actualFindings": None,
            "reason": "bundle has no coverage scope for this control",
        }, False
    if scope != "checked":
        return {
            **row,
            "status": "inconclusive",
            "actualFindings": None,
            "reason": f"control scope is {scope}, not checked",
        }, False
    hits = [
        actual
        for actual in outcome.findings
        if actual.get("ruleId") == control.rule_id
        and isinstance(actual.get("primaryLocation"), Mapping)
        and actual["primaryLocation"].get("path") == control.path
    ]
    if hits:
        return {
            **row,
            "status": "triggered",
            "actualFindings": len(hits),
            "reason": "safe control produced findings",
        }, True
    return {
        **row,
        "status": "matched",
        "actualFindings": 0,
        "reason": "control scope checked and clean",
    }, False


def _summary(
    findings: list[dict[str, Any]],
    controls: list[dict[str, Any]],
    unexpected: list[dict[str, Any]],
    suite: ReferenceSuite,
) -> dict[str, int]:
    def count(rows: list[dict[str, Any]], status: str) -> int:
        return sum(1 for row in rows if row["status"] == status)

    return {
        "expectedFindings": len(suite.expected_findings),
        "matchedFindings": count(findings, "matched"),
        "missingFindings": count(findings, "missing"),
        "inconclusiveFindings": count(findings, "inconclusive"),
        "unexpectedFindings": len(unexpected),
        "expectedControls": len(suite.expected_controls),
        "matchedControls": count(controls, "matched"),
        "triggeredControls": count(controls, "triggered"),
        "inconclusiveControls": count(controls, "inconclusive"),
    }


def compare_reference_suite(
    *,
    manifest: Any,
    bundle: Any,
    target_sha: str,
    target_project_id: str,
    target_branch: str | None = None,
) -> dict[str, Any]:
    """Compare one publishing bundle with a pinned suite and return a report."""

    suite = parse_manifest(manifest)
    view = parse_bundle(bundle)
    target_sha = (target_sha or "").strip().lower()
    target_project_id = (target_project_id or "").strip()
    if not target_project_id:
        raise ReferenceCheckError(
            "INVALID_PROVENANCE", "targetProjectId must be pinned"
        )
    if len(target_sha) not in {40, 64} or any(
        char not in "0123456789abcdef" for char in target_sha
    ):
        raise ReferenceCheckError(
            "INVALID_PROVENANCE", "targetSha must be a 40 or 64 character hex SHA"
        )

    base: dict[str, Any] = {
        "schemaVersion": "reference-report/v1",
        "comparatorVersion": COMPARATOR_VERSION,
        "suiteId": suite.suite_id,
        "suiteVersion": suite.suite_version,
        "projectId": suite.project_id,
        "reference": {
            "branch": suite.branch,
            "sha": suite.sha,
            "snapshotManifestHash": suite.snapshot_manifest_hash,
            "artifactHash": suite.artifact_hash,
            "bundleSha256": suite.bundle_sha256,
            "runnerVersion": suite.runner_version,
        },
        "target": {
            "branch": target_branch,
            "sha": target_sha,
            "projectId": target_project_id,
            "runId": view.identity["runId"],
            "snapshotId": view.identity["snapshotId"],
            "artifactHash": view.identity["artifactHash"],
            "snapshotManifestHash": view.identity["snapshotManifestHash"],
            "runnerVersions": list(view.runner_versions),
        },
    }

    reasons = _compatibility(suite, view, target_sha, target_project_id)
    if reasons:
        return {
            **base,
            "status": "inconclusive",
            "compatibility": {
                "compatible": False,
                "bundleSchemaVersion": view.schema_version,
                "reasons": reasons,
            },
            "findings": [],
            "controls": [],
            "unexpectedFindings": [],
            "summary": {
                "expectedFindings": len(suite.expected_findings),
                "matchedFindings": 0,
                "missingFindings": 0,
                "inconclusiveFindings": 0,
                "unexpectedFindings": 0,
                "expectedControls": len(suite.expected_controls),
                "matchedControls": 0,
                "triggeredControls": 0,
                "inconclusiveControls": 0,
            },
        }

    findings: list[dict[str, Any]] = []
    controls: list[dict[str, Any]] = []
    used: dict[str, set[int]] = {category: set() for category in CATEGORIES}
    hard_failure = False
    for expected in suite.expected_findings:
        outcome = view.outcomes[expected.category]
        row, matched_index, failed = _expected_row(
            expected, outcome, list(outcome.findings)
        )
        if matched_index is not None:
            used[expected.category].add(matched_index)
        hard_failure = hard_failure or failed
        findings.append(row)
    for control in suite.expected_controls:
        row, failed = _control_row(control, view.outcomes[control.category])
        hard_failure = hard_failure or failed
        controls.append(row)

    unexpected: list[dict[str, Any]] = []
    for category in CATEGORIES:
        for index, actual in enumerate(view.outcomes[category].findings):
            if index in used[category]:
                continue
            location = actual.get("primaryLocation")
            unexpected.append({
                "category": category,
                "ruleId": actual.get("ruleId"),
                "path": location.get("path") if isinstance(location, Mapping) else None,
                "fingerprint": actual.get("fingerprint"),
                "status": actual.get("status"),
            })

    inconclusive = any(
        row["status"] == "inconclusive" for row in [*findings, *controls]
    )
    if hard_failure:
        status = "fail"
    elif inconclusive or unexpected:
        status = "inconclusive"
    else:
        status = "pass"

    return {
        **base,
        "status": status,
        "compatibility": {
            "compatible": True,
            "bundleSchemaVersion": view.schema_version,
            "reasons": [],
        },
        "findings": findings,
        "controls": controls,
        "unexpectedFindings": unexpected,
        "summary": _summary(findings, controls, unexpected, suite),
    }