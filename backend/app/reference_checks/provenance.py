"""Server-owned provenance checks for reference and target snapshots.

The router never accepts a client bundle, commit SHA or branch. It resolves a
server-owned reference manifest and loads the target run through canonical
storage. This module verifies the pinned reference identity against the stored
reference snapshot and the target identity against the loaded canonical
envelope. Every missing authoritative fact is an explicit inconclusive reason,
never a fabricated ``pass``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .bundle import BundleView, parse_bundle
from .manifest import ExpectedFinding, ReferenceCheckError, ReferenceSuite

_SHA_RE = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ServerSnapshot:
    """Authoritative facts loaded by the server, not supplied by the client."""

    source: str
    workspace_id: str
    run_id: str | None
    snapshot_id: str | None
    commit_sha: str | None
    branch: str | None
    snapshot_manifest_hash: str | None
    artifact_hash: str | None
    bundle_content_sha256: str | None
    source_export_sha256: str | None
    artifact_row_hash: str | None
    manifest_hash: str | None
    bundle: Mapping[str, Any] | None
    details: Mapping[str, Any]

    def public(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "workspaceId": self.workspace_id,
            "runId": self.run_id,
            "snapshotId": self.snapshot_id,
            "commitSha": self.commit_sha,
            "branch": self.branch,
            "snapshotManifestHash": self.snapshot_manifest_hash,
            "artifactHash": self.artifact_hash,
            "bundleContentSha256": self.bundle_content_sha256,
            "sourceExportSha256": self.source_export_sha256,
            "manifestHash": self.manifest_hash,
        }


@dataclass(frozen=True)
class Verification:
    verified: bool
    reasons: tuple[str, ...]
    checks: Mapping[str, Any]

    def public(self) -> dict[str, Any]:
        return {
            "verified": self.verified,
            "reasons": list(self.reasons),
            "checks": dict(self.checks),
        }


def canonical_content_sha256(value: Any) -> str:
    """Stable digest of parsed JSON, independent of key order and whitespace."""

    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def identity_matches(expected: ExpectedFinding, actual: Mapping[str, Any]) -> bool:
    if expected.category != actual.get("category"):
        return False
    if expected.rule_id != actual.get("ruleId"):
        return False
    location = actual.get("primaryLocation")
    path = location.get("path") if isinstance(location, Mapping) else None
    if expected.path != path:
        return False
    if expected.symbol_key is not None:
        anchor = actual.get("anchor")
        symbol = anchor.get("symbolKey") if isinstance(anchor, Mapping) else None
        if expected.symbol_key != symbol:
            return False
    return True


def _identity_checks(
    *,
    identity: Mapping[str, str],
    workspace_id: str,
    run_id: str | None,
    snapshot_id: str | None,
    snapshot_manifest_hash: str | None,
    artifact_hash: str | None,
) -> list[str]:
    reasons: list[str] = []
    if identity.get("workspaceId") != workspace_id:
        reasons.append("bundle identity.workspaceId differs from the authenticated workspace")
    if run_id is not None and identity.get("runId") != run_id:
        reasons.append("bundle identity.runId differs from the server-selected run")
    if snapshot_id is not None and identity.get("snapshotId") != snapshot_id:
        reasons.append("bundle identity.snapshotId differs from the server-selected snapshot")
    if snapshot_manifest_hash is not None and (
        identity.get("snapshotManifestHash") != snapshot_manifest_hash
    ):
        reasons.append(
            "bundle identity.snapshotManifestHash differs from the stored snapshot manifest"
        )
    if artifact_hash is not None and identity.get("artifactHash") != artifact_hash:
        reasons.append("bundle identity.artifactHash differs from the stored artifact hash")
    return reasons


def _commit_reasons(commit_sha: str | None, note: str | None = None) -> list[str]:
    if not commit_sha:
        reason = "server provenance has no source commit SHA for this run/artifact"
        return [f"{reason}: {note}" if note else reason]
    if not _SHA_RE.fullmatch(commit_sha):
        return ["server provenance source commit is not a 40/64 character hex SHA"]
    return []


def verify_target_snapshot(snapshot: ServerSnapshot, view: BundleView) -> Verification:
    reasons = _identity_checks(
        identity=view.identity,
        workspace_id=snapshot.workspace_id,
        run_id=snapshot.run_id,
        snapshot_id=snapshot.snapshot_id,
        snapshot_manifest_hash=snapshot.snapshot_manifest_hash,
        artifact_hash=snapshot.artifact_hash,
    )
    reasons.extend(_commit_reasons(snapshot.commit_sha, snapshot.details.get("provenanceNote")))
    for name in ("run_id", "snapshot_id", "snapshot_manifest_hash", "artifact_hash", "artifact_row_hash", "manifest_hash", "bundle_content_sha256"):
        if not getattr(snapshot, name):
            reasons.append(f"server target provenance has no authoritative {name}")
    if snapshot.bundle is None:
        reasons.append("server target snapshot has no published bundle")
    if snapshot.manifest_hash and (
        snapshot.manifest_hash != snapshot.snapshot_manifest_hash
    ):
        reasons.append("stored envelope manifest hash disagrees with snapshot manifest hash")
    if snapshot.artifact_row_hash and (
        snapshot.artifact_row_hash != view.identity.get("artifactHash")
    ):
        reasons.append("stored artifact row hash disagrees with bundle identity.artifactHash")
    checks = {
        "workspaceId": view.identity.get("workspaceId") == snapshot.workspace_id,
        "runId": view.identity.get("runId") == snapshot.run_id,
        "snapshotId": view.identity.get("snapshotId") == snapshot.snapshot_id,
        "snapshotManifestHash": (
            view.identity.get("snapshotManifestHash") == snapshot.snapshot_manifest_hash
        ),
        "artifactHash": view.identity.get("artifactHash") == snapshot.artifact_hash,
        "sourceCommit": bool(snapshot.commit_sha),
        "source": snapshot.source,
    }
    return Verification(not reasons, tuple(reasons), checks)


def _expected_present(
    suite: ReferenceSuite, view: BundleView
) -> list[str]:
    reasons: list[str] = []
    for expected in suite.expected_findings:
        outcome = view.outcomes.get(expected.category)
        if outcome is None:
            reasons.append(
                f"reference snapshot has no {expected.category} scanner for {expected.id}"
            )
            continue
        if not outcome.ready or outcome.scopes.get((expected.path, expected.rule_id)) != "checked":
            reasons.append(f"reference snapshot has no completed checked scope for {expected.id}")
        candidates = [
            actual
            for actual in outcome.findings
            if identity_matches(expected, actual)
        ]
        if len(candidates) != 1:
            reasons.append(
                f"reference snapshot does not contain exactly one {expected.id} "
                f"({len(candidates)} candidates)"
            )
            continue
        actual = candidates[0]
        if expected.fingerprint is not None and (
            str(actual.get("fingerprint", "")).lower() != expected.fingerprint
        ):
            reasons.append(
                f"reference snapshot {expected.id} fingerprint differs from the pinned value"
            )
        if actual.get("status") != "confirmed":
            reasons.append(
                f"reference snapshot {expected.id} status is {actual.get('status')!r}, "
                "not confirmed"
            )
    for control in suite.expected_controls:
        outcome = view.outcomes.get(control.category)
        if outcome is None:
            reasons.append(
                f"reference snapshot has no {control.category} scanner for control {control.id}"
            )
            continue
        if not outcome.ready or outcome.scopes.get((control.path, control.rule_id)) != "checked":
            reasons.append(f"reference snapshot has no completed checked scope for control {control.id}")
        hits = [
            actual
            for actual in outcome.findings
            if actual.get("status") == "confirmed"
            and actual.get("ruleId") == control.rule_id
            and isinstance(actual.get("primaryLocation"), Mapping)
            and actual["primaryLocation"].get("path") == control.path
        ]
        if hits:
            reasons.append(
                f"reference snapshot triggers safe control {control.id} "
                f"({len(hits)} confirmed findings)"
            )
    return reasons


def verify_reference_snapshot(
    suite: ReferenceSuite, snapshot: ServerSnapshot | None
) -> tuple[Verification, BundleView | None]:
    if snapshot is None:
        return Verification(
            False,
            (
                "no server-owned reference snapshot is available; "
                "pin reference.runId/snapshotId or wire a reference loader",
            ),
            {"source": None},
        ), None
    reasons: list[str] = []
    view: BundleView | None = None
    if snapshot.bundle is None:
        reasons.append("server-owned reference snapshot has no published bundle")
    else:
        try:
            view = parse_bundle(snapshot.bundle)
        except ReferenceCheckError as exc:
            reasons.append(f"reference snapshot bundle is not usable: {exc.code}: {exc.message}")
    if suite.sha != snapshot.commit_sha:
        reasons.append(
            f"pinned reference SHA {suite.sha} differs from server source commit "
            f"{snapshot.commit_sha!r}"
        )
    if suite.snapshot_manifest_hash is None:
        reasons.append("reference manifest does not pin reference.snapshotManifestHash")
    elif suite.snapshot_manifest_hash != snapshot.snapshot_manifest_hash:
        reasons.append(
            "pinned reference.snapshotManifestHash differs from the stored snapshot manifest"
        )
    if suite.artifact_hash is None:
        reasons.append("reference manifest does not pin reference.artifactHash")
    elif suite.artifact_hash != snapshot.artifact_hash:
        reasons.append("pinned reference.artifactHash differs from the stored artifact hash")
    if suite.bundle_content_sha256 is None:
        reasons.append(
            "reference manifest does not pin reference.bundleContentSha256; "
            "raw-export bundleSha256 alone is not canonical"
        )
    elif suite.bundle_content_sha256 != snapshot.bundle_content_sha256:
        reasons.append(
            "pinned reference.bundleContentSha256 differs from the stored reference bundle"
        )
    if suite.bundle_sha256 and snapshot.source_export_sha256:
        if suite.bundle_sha256 != snapshot.source_export_sha256:
            reasons.append("pinned reference.bundleSha256 differs from the stored source export")
    if view is not None:
        reasons.extend(
            _identity_checks(
                identity=view.identity,
                workspace_id=snapshot.workspace_id,
                run_id=snapshot.run_id,
                snapshot_id=snapshot.snapshot_id,
                snapshot_manifest_hash=snapshot.snapshot_manifest_hash,
                artifact_hash=snapshot.artifact_hash,
            )
        )
        reasons.extend(_expected_present(suite, view))
    checks = {
        "source": snapshot.source,
        "sourceCommit": suite.sha == snapshot.commit_sha,
        "snapshotManifestHash": (
            suite.snapshot_manifest_hash is not None
            and suite.snapshot_manifest_hash == snapshot.snapshot_manifest_hash
        ),
        "artifactHash": (
            suite.artifact_hash is not None
            and suite.artifact_hash == snapshot.artifact_hash
        ),
        "bundleContentSha256": (
            suite.bundle_content_sha256 is not None
            and suite.bundle_content_sha256 == snapshot.bundle_content_sha256
        ),
        "sourceExportSha256": (
            None
            if not snapshot.source_export_sha256
            else suite.bundle_sha256 == snapshot.source_export_sha256
        ),
        "expectedFindingsPresent": view is not None,
    }
    return Verification(not reasons, tuple(reasons), checks), view
