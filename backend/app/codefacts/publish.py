"""Мост в storage canonical publish/get.

Storage владеет финальной записью immutable bundle. Этот модуль только собирает
контрактный payload из уже проверенных codefacts и вызывает
`backend.app.storage.canonical.CanonicalPublisher`, если он есть в сборке.
Пока моста нет, результат честно помечается `published=false`; API не называет
локальный хэш опубликованным.
"""

from __future__ import annotations

import mimetypes
import re
from typing import Any, Dict, List, Optional, Tuple

from backend.app.codefacts.extractor import EXTRACTOR_VERSION
from backend.app.codefacts.identity import (
    artifact_scope_uuid,
    canonical_json,
    revision_uuid,
    sha256_hex,
    workflow_uuid,
)
from backend.app.codefacts.identity import bundle_hash as content_hash


def _sorted_manifest(files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for item in files:
        path = str(item.get("path", ""))
        rows.append({
            "path": path,
            "sourceHash": str(item.get("sha256", "")),
            "byteLength": int(item.get("sizeBytes", 0)),
            "mediaType": mimetypes.guess_type(path)[0] or "application/octet-stream",
        })
    rows.sort(key=lambda item: tuple(item["path"].split("/")))
    return rows


def document_hash(envelope: Dict[str, Any], manifest: List[Dict[str, Any]]) -> str:
    payload = {"schemaVersion": "0.1", "manifest": manifest, "envelope": envelope}
    return sha256_hex(canonical_json(payload).encode("utf-8"))


def _definition_without_secrets(definition: Any) -> Any:
    if isinstance(definition, dict):
        return {
            key: _definition_without_secrets(value)
            for key, value in definition.items()
            if key not in {"secretToken", "secret_token", "webhookSecret", "webhook_secret"}
        }
    if isinstance(definition, list):
        return [_definition_without_secrets(item) for item in definition]
    return definition


def _positive_int(value: Any) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


def _revision_number(revision_id: str) -> int:
    match = re.search(r"_(\d+)$", revision_id or "")
    return int(match.group(1)) if match else 1


def build_publish_kwargs(*, workspace: str, run_artifact: Dict[str, Any],
                         snapshot: Dict[str, Any], artifact: Dict[str, Any],
                         workflow: Dict[str, Any], run: Dict[str, Any],
                         revision_id: str) -> Optional[Dict[str, Any]]:
    envelope = run_artifact.get("analysisBundle")
    if not envelope:
        return None
    manifest = _sorted_manifest(list(snapshot.get("files", [])))
    signature = document_hash(envelope, manifest)
    definition = _definition_without_secrets(workflow.get("draft") or {})
    identity = run_artifact["identity"]
    artifact_hash = str(artifact.get("hash_sha256") or identity["artifactHash"])
    artifact_id = artifact_scope_uuid(workspace, str(artifact.get("id", "")))
    return {
        "envelope": envelope,
        "manifest_files": manifest,
        "document_hash": signature,
        "provenance": {
            "origin": "scanner",
            "normalizationVersion": EXTRACTOR_VERSION,
            "expectedScopesVerified": False,
            "domainPolicyApplicability": "not_applicable",
        },
        "artifact": {
            "artifact_id": artifact_id,
            "artifact_hash": artifact_hash,
            "storage_ref": f"snapshot:{identity['snapshotId']}",
            "compressed_bytes": int(artifact.get("size_bytes") or 0),
            "expires_at": artifact.get("expires_at") or artifact.get("expiresAt"),
        },
        "workflow": {
            "workflow_id": workflow_uuid(str(workflow.get("id") or ""), workspace),
            "name": str(workflow.get("name") or "Security Scan"),
            "draft_version": _positive_int((workflow.get("draft") or {}).get("schemaVersion", 1)),
            "definition": definition,
            "definition_hash": sha256_hex(canonical_json(definition).encode("utf-8")),
            "revision_id": revision_uuid(revision_id, workspace),
            "revision_number": _revision_number(revision_id),
        },
        "run": {
            "run_id": identity["runId"],
            "execution_status": str(run.get("execution_status") or "unknown"),
            "security_verdict": str(run.get("security_verdict") or "unknown"),
        },
    }


def _load_publisher(workspace: str) -> Tuple[Any, Optional[str]]:
    try:
        from backend.app.storage.canonical import CanonicalPublisher  # type: ignore
    except Exception as exc:  # noqa: BLE001 - мост может быть ещё не смёржен
        return None, f"storage canonical bridge unavailable: {exc}"
    try:
        return CanonicalPublisher(workspace), None
    except Exception as exc:  # noqa: BLE001
        return None, f"CanonicalPublisher init failed: {exc}"


def publish_run_artifact(*, workspace: str, run_artifact: Dict[str, Any],
                         snapshot: Dict[str, Any], artifact: Dict[str, Any],
                         workflow: Dict[str, Any], run: Dict[str, Any],
                         revision_id: str) -> Dict[str, Any]:
    kwargs = build_publish_kwargs(
        workspace=workspace, run_artifact=run_artifact, snapshot=snapshot,
        artifact=artifact, workflow=workflow, run=run, revision_id=revision_id,
    )
    if kwargs is None:
        return {"published": False, "reason": "analysisBundle has no three scanner categories"}
    publisher, error = _load_publisher(workspace)
    if publisher is None:
        return {"published": False, "reason": error, "documentHash": kwargs["document_hash"]}
    try:
        result = publisher.publish(**kwargs)
    except Exception as exc:  # noqa: BLE001
        return {"published": False, "reason": f"publish failed: {exc}", "documentHash": kwargs["document_hash"]}
    if result is None:
        return {"published": False, "reason": "publish returned no record", "documentHash": kwargs["document_hash"]}
    if isinstance(result, dict) and (result.get("published") is False or result.get("error")):
        return {
            "published": False,
            "reason": str(result.get("error") or result.get("reason") or "publish rejected"),
            "documentHash": kwargs["document_hash"],
        }
    if isinstance(result, dict):
        bundle_hash = result.get("bundleHash") or result.get("bundle_hash") or result.get("bundleDocumentHash")
        published_at = result.get("publishedAt") or result.get("published_at")
        returned_signature = result.get("documentHash") or result.get("document_hash")
        envelope_hash = result.get("envelopeHash") or result.get("envelope_hash")
        idempotent = result.get("idempotent")
        counts = result.get("counts")
    else:
        bundle_hash = getattr(result, "bundleHash", None) or getattr(result, "bundle_hash", None)
        published_at = getattr(result, "publishedAt", None)
        returned_signature = getattr(result, "documentHash", None) or getattr(result, "document_hash", None)
        envelope_hash = getattr(result, "envelopeHash", None)
        idempotent = getattr(result, "idempotent", None)
        counts = getattr(result, "counts", None)
    return {
        "published": True,
        "bundleHash": str(bundle_hash) if bundle_hash else None,
        "documentHash": str(returned_signature or kwargs["document_hash"]),
        "envelopeHash": str(envelope_hash) if envelope_hash else None,
        "idempotent": idempotent,
        "counts": counts,
        "publishedAt": published_at,
    }


def get_published_envelope(workspace: str, run_id: str, snapshot_id: str) -> Optional[Dict[str, Any]]:
    publisher, error = _load_publisher(workspace)
    if publisher is None:
        return None
    try:
        result = publisher.get_published_envelope(run_id, snapshot_id)
    except Exception:  # noqa: BLE001
        try:
            from backend.app.storage.canonical import get_published_envelope as getter  # type: ignore

            result = getter(run_id, snapshot_id)
        except Exception:  # noqa: BLE001
            return None
    if not isinstance(result, dict):
        return None
    bundle = result.get("bundle") or result.get("envelope")
    if not bundle:
        return None
    provenance = result.get("provenance") or {}
    if provenance.get("published") is False:
        return None
    bundle_hash = provenance.get("bundleHash") or provenance.get("bundle_hash")
    if not bundle_hash:
        bundle_hash = content_hash(bundle)
    return {
        "bundle": bundle,
        "manifest": result.get("manifest"),
        "provenance": provenance,
        "bundleHash": str(bundle_hash),
    }


def map_from_analysis_bundle(bundle: Dict[str, Any], published_hash: Optional[str] = None) -> Optional[Dict[str, Any]]:
    try:
        code_facts = bundle["codeFacts"]
        findings = [finding for scan in bundle["scanResults"] for finding in scan["findings"]]
        links = [
            {"findingId": finding["id"], "nodeId": node_id}
            for finding in findings
            for node_id in finding.get("graphNodeIds", [])
        ]
        map_dto = {
            "schemaVersion": "0.1",
            "identity": bundle["identity"],
            "signature": bundle["signature"],
            "nodes": code_facts["nodes"],
            "edges": code_facts["edges"],
            "evidence": code_facts["evidence"],
            "coverage": code_facts["coverage"],
            "policyInputs": code_facts.get("policyInputs", []),
            "diagnostics": code_facts.get("diagnostics", []),
            "bundleHash": published_hash or content_hash(bundle),
            "findingRefs": [finding["id"] for finding in findings],
            "findingNodeLinks": links,
        }
    except (KeyError, TypeError):
        return None
    return {"identity": map_dto["identity"], "map": map_dto, "findings": findings}
