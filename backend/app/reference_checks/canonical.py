"""Server-side canonical loaders for reference checks.

The request carries only IDs. This module resolves:
* the target run/artifact through the workspace-scoped runtime tables and the
  canonical published envelope;
* an optional frozen reference snapshot from the server-owned registry, or a
  live reference run pinned in the manifest;
* the source commit/branch from server-owned repository provenance.

When canonical storage or repository provenance lacks a fact, the loader
returns ``None`` for that fact. The comparator turns it into an explicit
inconclusive reason. Nothing here fabricates a commit, branch or hash.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from backend.app.codefacts.identity import run_uuid, workspace_scope
from backend.app.codefacts.publish import PublishedHashMissing, get_published_envelope
from backend.app.codefacts.store import CodeFactsStore
from backend.app.storage.database import DatabaseRepository, get_pool

from .manifest import ReferenceCheckError, ReferenceSuite
from .provenance import ServerSnapshot, canonical_content_sha256
from .registry import ReferenceSuiteRegistry

logger = logging.getLogger("ReferenceChecks")

_ARTIFACT_META_COMMIT_KEYS = ("commitSha", "commit_sha", "sourceCommit", "source_commit")
_ARTIFACT_META_BRANCH_KEYS = ("branch", "sourceBranch", "source_branch")


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _mapping(value: Any, field: str, code: str = "TARGET_CANONICAL_INCOMPLETE") -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReferenceCheckError(code, f"{field} is not an object")
    return value


def _source_envelope(workspace: str, run_key: str, snapshot_id: str) -> Mapping[str, Any] | None:
    try:
        from backend.app.storage.canonical import CanonicalPublisher  # type: ignore

        result = CanonicalPublisher(workspace).get_source_envelope(run_key, snapshot_id)
    except Exception:  # noqa: BLE001 - canonical bridge is optional
        return None
    bundle = result.get("bundle") if isinstance(result, Mapping) else None
    return bundle if isinstance(bundle, Mapping) else None


def _artifact_meta_provenance(meta: Mapping[str, Any] | None) -> tuple[str | None, str | None]:
    if not meta:
        return None, None
    repository = meta.get("repository")
    if isinstance(repository, Mapping):
        commit = _text(repository.get("commit_sha")) or _text(repository.get("commitSha"))
        branch = _text(repository.get("branch"))
        if commit or branch:
            return commit, branch
    commit = next((_text(meta.get(key)) for key in _ARTIFACT_META_COMMIT_KEYS if _text(meta.get(key))), None)
    branch = next((_text(meta.get(key)) for key in _ARTIFACT_META_BRANCH_KEYS if _text(meta.get(key))), None)
    return commit, branch


def _repository_provenance(
    workspace: str, run_id: str, meta: Mapping[str, Any] | None
) -> tuple[str | None, str | None, str | None]:
    """Return (commit, branch, note) from server-owned provenance rows."""

    commit, branch = _artifact_meta_provenance(meta)
    notes: list[str] = []
    rows: list[tuple[str, Optional[str]]] = []
    try:
        with get_pool().connection() as conn:
            for table, alias in (
                ("runtime.repository_watch_commits", "c"),
                ("runtime.repository_hook_deliveries", "d"),
            ):
                result = conn.execute(
                    f"""
                    SELECT {alias}.commit_sha, w.branch
                      FROM {table} {alias}
                      JOIN runtime.repository_watches w
                        ON w.workspace_id = {alias}.workspace_id
                       AND w.id = {alias}.watch_id
                     WHERE {alias}.workspace_id = %s
                       AND {alias}.run_id = %s
                       AND {alias}.commit_sha IS NOT NULL
                    """,
                    (workspace, run_id),
                ).fetchall()
                rows.extend((row["commit_sha"], row["branch"]) for row in result)
    except Exception as exc:  # noqa: BLE001 - database may be unavailable in raw mode
        logger.debug("repository provenance lookup failed: %s", exc)
        notes.append("server repository provenance store is unavailable")

    commits = {str(item[0]).strip().lower() for item in rows if item[0]}
    branches = {_text(item[1]) for item in rows if _text(item[1])}
    if commit:
        commits.add(commit.lower())
    if branch:
        branches.add(branch)
    if len(commits) > 1:
        notes.append("server repository provenance has conflicting commit SHAs")
        commit = None
    elif len(commits) == 1:
        commit = next(iter(commits))
    if len(branches) > 1:
        notes.append("server repository provenance has conflicting branches")
        branch = None
    elif len(branches) == 1:
        branch = next(iter(branches))
    note = "; ".join(notes) if notes else None
    return commit, branch, note


def _resolved_snapshot_id(
    *, workspace: str, run_id: str, artifact_row: Mapping[str, Any] | None,
    store: CodeFactsStore,
) -> str | None:
    payload = store.load_run(workspace, run_uuid(run_id, workspace))
    if payload:
        snapshot = payload.get("identity", {}).get("snapshotId")
        if snapshot:
            return str(snapshot)
    if artifact_row:
        artifact_id = str(artifact_row.get("id"))
        meta = store.load_artifact_meta(workspace, artifact_id)
        if meta and meta.get("snapshotId"):
            return str(meta["snapshotId"])
    return None


def load_canonical_snapshot(
    workspace: str,
    run_id: str,
    *,
    artifact_id: str | None = None,
    expected_snapshot_id: str | None = None,
    source: str = "target-run",
) -> ServerSnapshot:
    """Load one published run and its authoritative hashes/provenance."""

    with workspace_scope(workspace):
        db = DatabaseRepository()
        run_row = db.get_run(run_id)
        if not run_row:
            raise ReferenceCheckError(
                "TARGET_RUN_UNKNOWN", f"run {run_id!r} is not visible in this workspace"
            )
        run_artifact_id = str(run_row.get("artifact_id") or "") or None
        if artifact_id is not None and artifact_id != run_artifact_id:
            raise ReferenceCheckError(
                "TARGET_RUN_ARTIFACT_MISMATCH",
                f"run {run_id!r} does not belong to artifact {artifact_id!r}",
            )
        artifact_row: Mapping[str, Any] | None = None
        if run_artifact_id:
            artifact_row = db.get_artifact(run_artifact_id)
            if not artifact_row:
                raise ReferenceCheckError(
                    "TARGET_ARTIFACT_UNKNOWN",
                    f"artifact {run_artifact_id!r} is not visible in this workspace",
                )
        store = CodeFactsStore()
        snapshot_id = _resolved_snapshot_id(
            workspace=workspace, run_id=run_id, artifact_row=artifact_row, store=store
        )
        if not snapshot_id:
            raise ReferenceCheckError(
                "TARGET_SNAPSHOT_UNKNOWN", f"run {run_id!r} has no snapshot"
            )
        if expected_snapshot_id is not None and expected_snapshot_id != snapshot_id:
            raise ReferenceCheckError(
                "TARGET_SNAPSHOT_MISMATCH",
                f"run {run_id!r} snapshot {snapshot_id!r} differs from the pinned snapshot",
            )
        run_key = run_uuid(run_id, workspace)
        try:
            published = get_published_envelope(workspace, run_key, snapshot_id)
        except PublishedHashMissing as exc:
            raise ReferenceCheckError("TARGET_CANONICAL_INCOMPLETE", str(exc)) from exc
        if not published:
            raise ReferenceCheckError(
                "TARGET_NOT_PUBLISHED",
                f"run {run_id!r} has no published canonical envelope",
            )
        manifest = _mapping(published.get("manifest"), "published.manifest")
        provenance = _mapping(published.get("provenance"), "published.provenance")
        bundle = _mapping(published.get("bundle"), "published.bundle")
        identity = _mapping(bundle.get("identity"), "bundle.identity")

        expected_identity = {
            "workspaceId": workspace,
            "runId": run_key,
            "snapshotId": snapshot_id,
        }
        for key, expected in expected_identity.items():
            if str(identity.get(key)) != expected:
                raise ReferenceCheckError(
                    "TARGET_IDENTITY_MISMATCH",
                    f"published bundle identity.{key} does not match the server-selected value",
                )
        artifact_hash = _text(artifact_row.get("hash_sha256")) if artifact_row else None
        if artifact_hash and str(identity.get("artifactHash")) != artifact_hash:
            raise ReferenceCheckError(
                "TARGET_IDENTITY_MISMATCH",
                "published bundle artifactHash differs from the stored artifact row",
            )
        manifest_hash = _text(manifest.get("hash"))
        if manifest_hash and str(identity.get("snapshotManifestHash")) != manifest_hash:
            raise ReferenceCheckError(
                "TARGET_IDENTITY_MISMATCH",
                "published bundle snapshotManifestHash differs from the stored manifest",
            )

        meta = store.load_artifact_meta(workspace, run_artifact_id) if run_artifact_id else None
        commit, branch, note = _repository_provenance(workspace, run_id, meta)
        source_bundle = _source_envelope(workspace, run_key, snapshot_id)
        details = {
            "artifactId": run_artifact_id,
            "bundleHash": published.get("bundleHash"),
            "publishedAt": provenance.get("published"),
            "provenanceNote": note,
        }
        return ServerSnapshot(
            source=source,
            workspace_id=workspace,
            run_id=run_key,
            snapshot_id=snapshot_id,
            commit_sha=commit,
            branch=branch,
            snapshot_manifest_hash=manifest_hash,
            artifact_hash=artifact_hash,
            bundle_content_sha256=canonical_content_sha256(source_bundle or bundle),
            source_export_sha256=None,
            artifact_row_hash=artifact_hash,
            manifest_hash=manifest_hash,
            bundle=bundle,
            details=details,
        )


def load_reference_snapshot(
    workspace: str,
    suite: ReferenceSuite,
    registry: ReferenceSuiteRegistry,
    manifest_id: str,
) -> ServerSnapshot | None:
    """Load the frozen reference snapshot, or the live reference run if pinned."""

    sidecar = registry.load_reference_snapshot(manifest_id)
    if sidecar is not None:
        record = _mapping(sidecar, "reference snapshot", "REFERENCE_UNREADABLE")
        bundle = _mapping(
            record.get("bundle"), "reference snapshot bundle", "REFERENCE_UNREADABLE"
        )
        identity = _mapping(
            bundle.get("identity"), "reference snapshot bundle.identity", "REFERENCE_UNREADABLE"
        )
        return ServerSnapshot(
            source="reference-registry-sidecar",
            workspace_id=workspace,
            run_id=_text(record.get("runId")),
            snapshot_id=str(identity.get("snapshotId")),
            commit_sha=_text(record.get("sourceCommit")),
            branch=_text(record.get("sourceBranch")),
            snapshot_manifest_hash=str(identity.get("snapshotManifestHash")),
            artifact_hash=str(identity.get("artifactHash")),
            bundle_content_sha256=canonical_content_sha256(bundle),
            source_export_sha256=_text(record.get("sourceExportSha256")),
            artifact_row_hash=None,
            manifest_hash=str(identity.get("snapshotManifestHash")),
            bundle=bundle,
            details={"referenceManifestId": manifest_id},
        )
    if suite.reference_run_id and suite.reference_snapshot_id:
        return load_canonical_snapshot(
            workspace,
            suite.reference_run_id,
            expected_snapshot_id=suite.reference_snapshot_id,
            source="reference-run",
        )
    return None
