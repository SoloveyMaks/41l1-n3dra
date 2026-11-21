"""Compare a pinned reference suite with one analyzer bundle.

Two boundaries are enforced here:

* The server, not the caller, supplies the reference identity and the target
  run/snapshot/artifact hashes. ``compare_reference_suite`` receives already
  loaded ``ServerSnapshot`` objects for server mode and the raw-fixture CLI
  mode is explicitly marked as unattested.
* Every scanner and finding used for a ``pass`` must be contract-valid and
  complete. Missing coverage is ``inconclusive``; a missing expectation inside
  a checked scope is an honest ``fail``. Failed, partial or unavailable
  scanners never produce ``pass``, and each actual finding can satisfy at most
  one expectation.
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
from .provenance import (
    ServerSnapshot,
    Verification,
    identity_matches,
    verify_reference_snapshot,
    verify_target_snapshot,
)

COMPARATOR_VERSION = "reference-checks/2"
REPORT_SCHEMA = "reference-report/v2"
SERVER_MODE = "server-canonical"
RAW_MODE = "raw-fixture"


def _signature_reasons(suite: ReferenceSuite, bundle: BundleView) -> list[str]:
    reasons: list[str] = []
    for key in SIGNATURE_KEYS:
        expected = suite.signature[key]
        actual = bundle.signature[key]
        if expected != actual:
            reasons.append(f"{key} differs: reference {expected!r}, bundle {actual!r}")
    return reasons


def _hard_compatibility(suite: ReferenceSuite, bundle: BundleView) -> list[str]:
    reasons: list[str] = []
    if suite.bundle_schema_version != bundle.schema_version:
        reasons.append(
            f"bundle schema {bundle.schema_version} is not compatible with "
            f"{suite.bundle_schema_version}"
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
    return reasons


def _scan_gaps(bundle: BundleView) -> list[str]:
    """Reasons that can never coexist with ``pass``.

    A failed/cancelled/unsupported scanner or ``unavailable`` coverage never
    inspected its surface. Partial coverage is handled per scope: a checked
    expectation can match, an unchecked one becomes inconclusive.
    """

    reasons: list[str] = []
    for category in CATEGORIES:
        outcome = bundle.outcomes[category]
        if outcome.status in {"failed", "timeout", "unsupported"}:
            reasons.append(
                f"{category} scanner status is {outcome.status!r}; it did not complete"
            )
        if outcome.coverage_status == "unavailable":
            reasons.append(f"{category} coverage is unavailable")
    return reasons


def _coverage_warnings(bundle: BundleView) -> list[str]:
    warnings: list[str] = []
    for category in CATEGORIES:
        outcome = bundle.outcomes[category]
        if outcome.status == "partial":
            warnings.append(f"{category} scanner status is 'partial'")
        if outcome.coverage_status == "partial":
            warnings.append(f"{category} coverage is partial")
    return warnings


def _severity(value: Any) -> str | None:
    return value.strip().lower() if isinstance(value, str) and value.strip() else None


def _expected_row(
    expected: ExpectedFinding,
    outcome: ScanOutcome,
    consumed: set[int],
) -> tuple[dict[str, Any], int | None, bool]:
    """Return (row, consumed index, hard failure) for one expected finding."""

    scope = outcome.scopes.get((expected.path, expected.rule_id))
    row: dict[str, Any] = {
        "id": expected.id,
        "category": expected.category,
        "ruleId": expected.rule_id,
        "path": expected.path,
        "expectedFingerprint": expected.fingerprint,
    }
    if not outcome.ready:
        return {
            **row,
            "status": "inconclusive",
            "reason": (
                f"{expected.category} scanner is {outcome.status!r} with "
                f"coverage {outcome.coverage_status!r}; not a complete scan"
            ),
        }, None, False
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
            "reason": f"coverage scope is {scope!r}, not checked",
        }, None, False
    candidates = [
        index
        for index, actual in enumerate(outcome.findings)
        if index not in consumed and identity_matches(expected, actual)
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
            "reason": "several unused actual findings match one reference identity",
        }, None, False
    index = candidates[0]
    matched = outcome.findings[index]
    status = matched.get("status")
    if status != "confirmed":
        return {
            **row,
            "status": "inconclusive",
            "actualFingerprint": matched.get("fingerprint"),
            "reason": f"actual finding status is {status!r}, not confirmed",
        }, index, False
    if expected.fingerprint is not None and (
        str(matched.get("fingerprint", "")).lower() != expected.fingerprint
    ):
        return {
            **row,
            "status": "mismatch",
            "actualFingerprint": matched.get("fingerprint"),
            "reason": "finding identity changed inside a checked scope",
        }, index, True
    if expected.severity is not None and expected.severity != _severity(matched.get("severity")):
        return {
            **row,
            "status": "mismatch",
            "actualFingerprint": matched.get("fingerprint"),
            "actualSeverity": _severity(matched.get("severity")),
            "reason": "severity differs from the pinned expectation",
        }, index, True
    return {
        **row,
        "status": "matched",
        "actualFingerprint": matched.get("fingerprint"),
        "actualSeverity": _severity(matched.get("severity")),
        "reason": "expected finding matched inside a checked scope",
    }, index, False


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
    if not outcome.ready:
        return {
            **row,
            "status": "inconclusive",
            "actualFindings": None,
            "reason": (
                f"{control.category} scanner is {outcome.status!r} with "
                f"coverage {outcome.coverage_status!r}; not a complete scan"
            ),
        }, False
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
            "reason": f"control scope is {scope!r}, not checked",
        }, False
    hits = [
        actual
        for actual in outcome.findings
        if actual.get("ruleId") == control.rule_id
        and isinstance(actual.get("primaryLocation"), Mapping)
        and actual["primaryLocation"].get("path") == control.path
    ]
    confirmed = [item for item in hits if item.get("status") == "confirmed"]
    if confirmed:
        return {
            **row,
            "status": "triggered",
            "actualFindings": len(confirmed),
            "reason": "safe control produced confirmed findings",
        }, True
    if hits:
        return {
            **row,
            "status": "inconclusive",
            "actualFindings": len(hits),
            "reason": "safe control has non-confirmed findings; cannot call it clean",
        }, False
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
        "mismatchedFindings": count(findings, "mismatch"),
        "inconclusiveFindings": count(findings, "inconclusive"),
        "unexpectedFindings": len(unexpected),
        "expectedControls": len(suite.expected_controls),
        "matchedControls": count(controls, "matched"),
        "triggeredControls": count(controls, "triggered"),
        "inconclusiveControls": count(controls, "inconclusive"),
    }


def _raw_target(view: BundleView) -> dict[str, Any]:
    return {
        "branch": None,
        "sha": None,
        "workspaceId": None,
        "runId": None,
        "snapshotId": None,
        "artifactId": None,
        "artifactHash": None,
        "snapshotManifestHash": None,
        "runnerVersions": list(view.runner_versions),
        "provenance": "raw-fixture-untrusted",
    }


def _server_target(snapshot: ServerSnapshot, view: BundleView) -> dict[str, Any]:
    return {
        "branch": snapshot.branch,
        "sha": snapshot.commit_sha,
        "workspaceId": snapshot.workspace_id,
        "runId": snapshot.run_id,
        "snapshotId": snapshot.snapshot_id,
        "artifactId": snapshot.details.get("artifactId"),
        "artifactHash": view.identity.get("artifactHash"),
        "snapshotManifestHash": view.identity.get("snapshotManifestHash"),
        "runnerVersions": list(view.runner_versions),
        "provenance": snapshot.source,
    }


def _provenance_block(
    *,
    mode: str,
    reference_snapshot: ServerSnapshot | None,
    target_snapshot: ServerSnapshot | None,
    reference_verification: Verification,
    target_verification: Verification,
    untrusted_claims: Mapping[str, Any] | None,
) -> dict[str, Any]:
    verified = reference_verification.verified and target_verification.verified
    reasons = [
        *(
            f"reference: {reason}"
            for reason in reference_verification.reasons
        ),
        *(
            f"target: {reason}"
            for reason in target_verification.reasons
        ),
    ]
    block: dict[str, Any] = {
        "mode": mode,
        "attestation": "canonical-published"
        if mode == SERVER_MODE and verified
        else "none",
        "verified": verified,
        "reasons": reasons,
        "reference": reference_snapshot.public() if reference_snapshot else None,
        "target": target_snapshot.public() if target_snapshot else None,
        "checks": {
            "reference": dict(reference_verification.checks),
            "target": dict(target_verification.checks),
        },
    }
    if untrusted_claims:
        block["untrustedClaims"] = dict(untrusted_claims)
    return block


def compare_reference_suite(
    *,
    manifest: Any,
    bundle: Any,
    mode: str = SERVER_MODE,
    reference_snapshot: ServerSnapshot | None = None,
    target_snapshot: ServerSnapshot | None = None,
    untrusted_claims: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare one bundle with a pinned reference suite and return a report.

    ``SERVER_MODE`` requires server-loaded snapshots. ``RAW_MODE`` compares a
    local manifest and bundle for fixtures only; its report is explicitly
    unattested and never carries a client-claimed target SHA.
    """

    if mode not in {SERVER_MODE, RAW_MODE}:
        raise ReferenceCheckError("INVALID_MODE", f"unknown comparison mode: {mode!r}")
    suite = parse_manifest(manifest)
    view = parse_bundle(bundle)

    if mode == SERVER_MODE:
        if target_snapshot is None:
            raise ReferenceCheckError(
                "TARGET_PROVENANCE_MISSING",
                "server mode requires a server-loaded target snapshot",
            )
        reference_verification, reference_view = verify_reference_snapshot(
            suite, reference_snapshot
        )
        target_verification = verify_target_snapshot(target_snapshot, view)
        target = _server_target(target_snapshot, view)
    else:
        reference_verification = Verification(
            False,
            (
                "raw-fixture mode: pinned reference identity was not verified "
                "against server storage",
            ),
            {"source": "raw-fixture"},
        )
        reference_view = None
        target_verification = Verification(
            False,
            (
                "raw-fixture mode: target bundle identity and commit were not "
                "verified against server storage",
            ),
            {"source": "raw-fixture"},
        )
        target = _raw_target(view)

    base: dict[str, Any] = {
        "schemaVersion": REPORT_SCHEMA,
        "comparatorVersion": COMPARATOR_VERSION,
        "suiteId": suite.suite_id,
        "suiteVersion": suite.suite_version,
        "projectId": suite.project_id,
        "reference": {
            "branch": suite.branch,
            "sha": suite.sha,
            "snapshotManifestHash": suite.snapshot_manifest_hash,
            "artifactHash": suite.artifact_hash,
            "bundleContentSha256": suite.bundle_content_sha256,
            "bundleSha256": suite.bundle_sha256,
            "runnerVersion": suite.runner_version,
            "snapshotPresent": reference_view is not None,
        },
        "target": target,
    }
    provenance = _provenance_block(
        mode=mode,
        reference_snapshot=reference_snapshot,
        target_snapshot=target_snapshot,
        reference_verification=reference_verification,
        target_verification=target_verification,
        untrusted_claims=untrusted_claims,
    )

    hard_reasons = _hard_compatibility(suite, view)
    if hard_reasons:
        return {
            **base,
            "status": "inconclusive",
            "compatibility": {
                "compatible": False,
                "bundleSchemaVersion": view.schema_version,
                "reasons": hard_reasons,
                "coverageReasons": _scan_gaps(view),
                "coverageWarnings": _coverage_warnings(view),
            },
            "provenance": provenance,
            "findings": [],
            "controls": [],
            "unexpectedFindings": [],
            "summary": {
                "expectedFindings": len(suite.expected_findings),
                "matchedFindings": 0,
                "missingFindings": 0,
                "mismatchedFindings": 0,
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
            expected, outcome, used[expected.category]
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

    coverage_reasons = _scan_gaps(view)
    inconclusive = any(
        row["status"] == "inconclusive" for row in [*findings, *controls]
    )
    provenance_ok = reference_verification.verified and target_verification.verified
    if mode == SERVER_MODE and not provenance_ok:
        status = "inconclusive"
    elif hard_failure:
        status = "fail"
    elif inconclusive or unexpected or coverage_reasons:
        status = "inconclusive"
    else:
        status = "pass"

    report: dict[str, Any] = {
        **base,
        "status": status,
        "compatibility": {
            "compatible": True,
            "bundleSchemaVersion": view.schema_version,
            "reasons": [],
            "coverageReasons": coverage_reasons,
            "coverageWarnings": _coverage_warnings(view),
        },
        "provenance": provenance,
        "findings": findings,
        "controls": controls,
        "unexpectedFindings": unexpected,
        "summary": _summary(findings, controls, unexpected, suite),
    }
    if mode == RAW_MODE:
        report["untrusted"] = {
            "bundleIdentity": dict(view.identity),
            "note": (
                "Raw fixture comparison only. The bundle identity, commit and "
                "branch were supplied by the caller and are not attestation."
            ),
        }
    return report
