"""Сборка run-scoped security map и self-check перед сохранением."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.app.artifacts.intake import build_snapshot_from_directory
from backend.app.codefacts.canonical import CanonicalMapBuilder
from backend.app.codefacts.extractor import extract_facts
from backend.app.codefacts.identity import canonical_json, manifest_hash, sha256_hex, snapshot_uuid
from backend.app.codefacts.source import SnapshotIntegrityError, load_snapshot_sources
from backend.app.codefacts.store import CodeFactsStore
from backend.app.contracts.schemas import Finding as BackendFinding


class MapBuildError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_artifact_meta(store: CodeFactsStore, workspace: str, artifact_row: Dict[str, Any]) -> Dict[str, Any]:
    artifact_id = str(artifact_row["id"])
    meta = store.load_artifact_meta(workspace, artifact_id)
    if meta:
        return meta
    extracted_path = artifact_row.get("extracted_path")
    if not extracted_path:
        raise MapBuildError("ARTIFACT_PATH_MISSING", "У артефакта нет extracted_path")
    snapshot = build_snapshot_from_directory(Path(extracted_path), workspace)
    snapshot["artifactHash"] = str(artifact_row.get("hash_sha256") or "")
    store.save_snapshot(workspace, snapshot)
    meta = {
        "artifactId": artifact_id,
        "workspaceId": workspace,
        "snapshotId": snapshot["snapshotId"],
        "snapshotManifestHash": snapshot["manifestHash"],
        "artifactHash": snapshot["artifactHash"],
        "sizeBytes": int(artifact_row.get("size_bytes") or 0),
        "uncompressedSizeBytes": int(artifact_row.get("uncompressed_size_bytes") or 0),
        "fileCount": int(artifact_row.get("file_count") or len(snapshot["files"])),
        "kotlinFileCount": int(artifact_row.get("kotlin_count") or 0),
        "gradleFileCount": int(artifact_row.get("gradle_count") or 0),
        "filename": str(artifact_row.get("filename") or "project.zip"),
        "createdAt": str(artifact_row.get("created_at") or _now()),
        "expiresAt": artifact_row.get("expires_at") or (
            datetime.now(timezone.utc) + timedelta(days=7)
        ).isoformat(),
    }
    store.save_artifact_meta(workspace, artifact_id, meta)
    return meta


def build_run_map(*, workspace: str, internal_run_id: str, run_id: str,
                  workflow_revision_id: Optional[str], artifact_row: Dict[str, Any],
                  backend_findings: List[BackendFinding],
                  store: Optional[CodeFactsStore] = None,
                  meta: Optional[Dict[str, Any]] = None,
                  snapshot: Optional[Dict[str, Any]] = None,
                  sources: Optional[Dict[str, Any]] = None,
                  facts: Optional[Any] = None,
                  scan_results: Optional[List[Any]] = None) -> Dict[str, Any]:
    store = store or CodeFactsStore()
    artifact_id = str(artifact_row.get("id", ""))
    if meta is None:
        meta = ensure_artifact_meta(store, workspace, artifact_row)
    snapshot_id = str(meta.get("snapshotId", ""))
    if snapshot is None:
        snapshot = store.load_snapshot(workspace, snapshot_id)
    if not snapshot:
        raise MapBuildError("SNAPSHOT_MISSING", "Snapshot артефакта не найден")
    extracted_path = artifact_row.get("extracted_path")
    if not extracted_path:
        raise MapBuildError("ARTIFACT_PATH_MISSING", "У артефакта нет extracted_path")
    root = Path(extracted_path)
    if not root.is_dir():
        raise MapBuildError("ARTIFACT_UNAVAILABLE", "Распакованный артефакт недоступен")
    if sources is None or facts is None:
        try:
            loaded_sources, source_diags = load_snapshot_sources(root, snapshot)
        except SnapshotIntegrityError as exc:
            raise MapBuildError("SNAPSHOT_INTEGRITY", str(exc)) from exc
        sources = loaded_sources
        facts = extract_facts(sources, len(snapshot.get("files", [])))
        for diag in source_diags:
            facts.diagnostics.append(diag)

    files = list(snapshot.get("files", []))

    artifact_hash = str(meta.get("artifactHash") or artifact_row.get("hash_sha256") or "")
    manifest_digest = str(snapshot.get("manifestHash") or meta.get("snapshotManifestHash") or "")
    if not artifact_hash or len(artifact_hash) != 64:
        raise MapBuildError("ARTIFACT_HASH_MISSING", "Нет полного sha256 архива")
    if manifest_hash(files) != manifest_digest:
        raise MapBuildError("SNAPSHOT_INTEGRITY", "manifestHash не совпадает с содержимым snapshot")
    if snapshot_uuid(manifest_digest) != snapshot_id:
        raise MapBuildError("SNAPSHOT_INTEGRITY", "snapshotId не соответствует manifestHash")

    builder = CanonicalMapBuilder(
        workspace_id=workspace,
        run_id=run_id,
        snapshot_id=snapshot_id,
        artifact_hash=artifact_hash,
        snapshot_manifest_hash=manifest_digest,
        workflow_revision_id=workflow_revision_id or "unknown-revision",
        facts=facts,
        sources=sources,
        snapshot_files=files,
        backend_findings=backend_findings,
        scan_results=scan_results,
    )
    payload = builder.build()
    self_check(payload["map"], payload["findings"])

    run_artifact = {
        "schemaVersion": "cg-run-facts/1",
        "internalRunId": internal_run_id,
        "workspaceId": workspace,
        "artifactId": artifact_id,
        "identity": payload["identity"],
        "map": payload["map"],
        "findings": payload["findings"],
        "redactionVersion": payload["redactionVersion"],
        "analysisBundle": payload.get("analysisBundle"),
        "createdAt": _now(),
        "limitations": [
            "Статический анализ исходников; код не исполнялся.",
            "Runtime-конфигурация, прокси и внешние фильтры не проверялись.",
            "Owner-check может существовать вне просмотренного пути и не выдумывается.",
        ],
    }
    store.save_run(workspace, run_id, run_artifact)
    return payload


def load_run_artifact(store: CodeFactsStore, workspace: str, run_id: str) -> Optional[Dict[str, Any]]:
    return store.load_run(workspace, run_id)


def _fail(code: str) -> None:
    raise MapBuildError("MAP_SELF_CHECK", f"Каноническая карта не прошла self-check: {code}")


def self_check(map_dto: Dict[str, Any], findings: List[Dict[str, Any]]) -> None:
    """Повторяет ключевые инварианты ui/security-map/src/model.ts."""
    scope = {key: map_dto["identity"][key] for key in ("workspaceId", "runId", "snapshotId")}
    for collection in ("nodes", "edges", "evidence", "diagnostics"):
        for item in map_dto[collection]:
            for key, value in scope.items():
                if item[key] != value:
                    _fail(f"SCOPE_MISMATCH:{collection}.{key}")
        if len({item["id"] for item in map_dto[collection]}) != len(map_dto[collection]):
            _fail(f"DUPLICATE_ID:{collection}")
    if len({item["id"] for item in findings}) != len(findings):
        _fail("DUPLICATE_ID:findings")

    node_ids = {item["id"] for item in map_dto["nodes"]}
    evidence_ids = {item["id"] for item in map_dto["evidence"]}
    finding_ids = {item["id"] for item in findings}
    for node in map_dto["nodes"]:
        if any(ref not in evidence_ids for ref in node["evidenceRefs"]):
            _fail("BROKEN_NODE_EVIDENCE")
        if node["kind"] != "actor" and not node["evidenceRefs"]:
            _fail("NODE_WITHOUT_EVIDENCE")
    for edge in map_dto["edges"]:
        if edge["fromId"] not in node_ids or edge["toId"] not in node_ids:
            _fail("BROKEN_EDGE_ENDPOINT")
        if any(ref not in evidence_ids for ref in edge["evidenceRefs"]):
            _fail("BROKEN_EDGE_EVIDENCE")
        if edge["basis"] == "source" and (not edge["evidenceRefs"] or edge["policyRef"] is not None):
            _fail("EDGE_PROVENANCE")
    evidence_by_id = {item["id"]: item for item in map_dto["evidence"]}
    links: Dict[str, List[str]] = {}
    for link in map_dto["findingNodeLinks"]:
        if link["findingId"] not in finding_ids or link["nodeId"] not in node_ids:
            _fail("BROKEN_FINDING_LINK")
        links.setdefault(link["findingId"], []).append(link["nodeId"])
    for finding in findings:
        if finding["primaryEvidenceId"] not in evidence_ids:
            _fail("BROKEN_PRIMARY_EVIDENCE")
        evidence = evidence_by_id[finding["primaryEvidenceId"]]
        if evidence["location"] != finding["primaryLocation"]:
            _fail("FINDING_LOCATION_MISMATCH")
        for ref in finding["relatedEvidenceIds"] + finding["traceEvidenceIds"]:
            if ref not in evidence_ids:
                _fail("BROKEN_RELATED_EVIDENCE")
        if set(finding["graphNodeIds"]) != set(links.get(finding["id"], [])):
            _fail("FINDING_LINK_MISMATCH")
        if any(node_id not in node_ids for node_id in finding["graphNodeIds"]):
            _fail("FINDING_NODE_OUTSIDE_GRAPH")
        anchor = finding["anchor"]
        if anchor["normalizedPath"] != finding["primaryLocation"]["path"]:
            _fail("ANCHOR_PATH_MISMATCH")
        if (finding["category"] == "SECRET") != (anchor["secretHmac"] is not None):
            _fail("SECRET_ANCHOR_MISMATCH")
    if set(map_dto["findingRefs"]) != finding_ids:
        _fail("FINDING_REFS_MISMATCH")
    coverage = map_dto["coverage"]
    if coverage["filesVisited"] > coverage["filesTotal"]:
        _fail("COVERAGE_OVERFLOW")
    if coverage["status"] == "complete" and (
        coverage["skippedScopes"] or coverage["parserErrors"]
        or coverage["unresolvedSymbols"] or coverage["limitsReached"]
        or any(scope_item["status"] != "checked" for scope_item in coverage["scopes"])
    ):
        _fail("COMPLETE_COVERAGE_WITH_GAPS")
    recomputed = sha256_hex(canonical_json({key: value for key, value in map_dto.items() if key != "bundleHash"}).encode("utf-8"))
    if recomputed != map_dto["bundleHash"]:
        _fail("BUNDLE_HASH_MISMATCH")