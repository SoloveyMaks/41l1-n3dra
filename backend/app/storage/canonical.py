"""Canonical publish bridge: B envelope -> data_contract published bundle.

This is the only write path from scanner/code-facts results into the canonical
C schema. It does not replace ``data_contract`` rules: the database still owns
snapshot sealing, run guards, bundle hash/counts and publication. The bridge
adds the validation that SQL cannot express plus an immutable copy of the raw
``AnalysisBundle`` for the comparison package.

No raw source bytes are accepted or stored: only paths, source hashes, lengths,
media types and redacted snippets.

See docs/postgres-runtime.md ("Canonical bridge").
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from backend.app.storage.database import DatabaseUnavailable, _workspace_id, get_pool

# Schema constants: canonical ids and envelope fields.
SCHEMA_VERSION = "0.1"
FINGERPRINT_VERSION = "fp/v1"
SCOPE_KEYS = ("workspaceId", "runId", "snapshotId")
POLICY_KEYS = ("domainPolicyRevisionId", "domainPolicyVersion", "domainPolicyHash")
SIGNATURE_KEYS = (
    "parserVersion", "parserHash",
    "rulesVersion", "rulesHash",
    "advisoryVersion", "advisoryHash",
    "configVersion", "scanConfigHash",
    "domainPolicyRevisionId", "domainPolicyVersion", "domainPolicyHash",
    "hmacKeyVersion",
)
CATEGORIES = ("SECRET", "SCA", "SAST")
SEVERITIES = ("info", "low", "medium", "high", "critical")
CONFIDENCES = ("high", "medium", "low")
FINDING_STATUSES = ("confirmed", "potential", "inconclusive")
SCAN_STATUSES = ("complete", "partial", "failed", "timeout", "unsupported")
COVERAGE_STATUSES = ("complete", "partial", "unavailable")
SCOPE_STATUSES = ("checked", "partial", "skipped", "error", "unsupported")
EVIDENCE_KINDS = ("source", "ast", "dsl", "sql", "manifest")
NODE_KINDS = ("actor", "route", "security_chain", "matcher", "guard",
              "function", "source", "sink", "data", "config")
EDGE_KINDS = ("routes_to", "matches", "permits", "requires", "calls",
              "flows_to", "guards", "reads", "writes")
RESOLUTIONS = ("resolved", "partial", "unknown")
BASES = ("source", "policy_assumption")
ORIGINS = ("scanner", "contract_scenario", "manual_fixture")
APPLICABILITY = ("not_applicable", "applied", "unknown")

MAX_SAFE_INTEGER = 9007199254740991


class CanonicalPublishError(ValueError):
    """Envelope or manifest rejected before/while publishing."""


class CanonicalConflictError(CanonicalPublishError):
    """The identity is already published with different content."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CanonicalPublishError(message)


def _obj(value: Any, field: str) -> Dict[str, Any]:
    _require(isinstance(value, dict), f"{field}: expected object")
    return value


def _arr(value: Any, field: str) -> List[Any]:
    _require(isinstance(value, list), f"{field}: expected array")
    return value


def _text(value: Any, field: str, *, allow_empty: bool = False) -> str:
    _require(isinstance(value, str), f"{field}: expected string")
    if not allow_empty:
        _require(len(value) > 0, f"{field}: empty string")
    return value


def _integer(value: Any, field: str, low: int = 0, high: int = MAX_SAFE_INTEGER) -> int:
    _require(isinstance(value, int) and not isinstance(value, bool), f"{field}: expected integer")
    _require(low <= value <= high, f"{field}: out of range")
    return value


def _enum(value: Any, field: str, allowed: Sequence[str]) -> str:
    _require(value in allowed, f"{field}: unexpected value {value!r}")
    return value


def _uuid(value: Any, field: str) -> str:
    _require(isinstance(value, str), f"{field}: expected UUID string")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise CanonicalPublishError(f"{field}: invalid UUID") from exc
    _require(str(parsed) == value.lower(), f"{field}: UUID must be lowercase canonical")
    return str(parsed)


def _sha(value: Any, field: str) -> str:
    _require(isinstance(value, str) and len(value) == 64, f"{field}: expected sha256 hex")
    _require(all(c in "0123456789abcdef" for c in value), f"{field}: expected lowercase sha256 hex")
    return value


def _source_path(value: Any, field: str) -> str:
    _require(isinstance(value, str) and len(value) > 0, f"{field}: expected non-empty path")
    _require(not value.startswith("/"), f"{field}: absolute path forbidden")
    _require("\\" not in value, f"{field}: backslash forbidden")
    _require(not any(ord(c) < 32 or ord(c) == 127 for c in value), f"{field}: control character")
    parts = value.split("/")
    _require(all(p not in ("", ".", "..") for p in parts), f"{field}: dot/empty path segment")
    _require(not (len(value) > 1 and value[1] == ":"), f"{field}: drive prefix forbidden")
    return value


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def canonical_uuid(kind: str, local_id: str, workspace_id: Optional[str] = None) -> str:
    """Stable UUID for a local id: uuid5(NAMESPACE_URL, 'cg-<kind>:<workspace>:<id>').

    Never rewrites an identity already present in an envelope. Runtime text ids
    stay text unless the caller explicitly maps them; new runtime run ids should
    be UUIDs, legacy ids are mapped only for migration.
    """
    _require(kind in ("workflow", "revision", "artifact", "run", "snapshot"), f"unknown id kind {kind}")
    _require(isinstance(local_id, str) and local_id, "local_id must be a non-empty string")
    workspace = str(workspace_id or _workspace_id())
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"cg-{kind}:{workspace}:{local_id}"))


def definition_hash(definition: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(definition)).hexdigest()


def compute_document_hash(manifest_files: Sequence[Mapping[str, Any]], envelope: Mapping[str, Any]) -> str:
    """Transport integrity hash of the trusted manifest + AnalysisBundle."""
    return hashlib.sha256(_canonical_bytes({
        "schemaVersion": SCHEMA_VERSION,
        "manifest": list(manifest_files),
        "envelope": envelope,
    })).hexdigest()



def fingerprint_of(finding: Mapping[str, Any]) -> str:
    anchor = finding["anchor"]
    payload = [
        FINGERPRINT_VERSION,
        finding["category"],
        finding["ruleId"],
        anchor["normalizedPath"],
        anchor["symbolKey"],
        anchor["normalizedContextHash"],
        anchor["role"],
        anchor["secretHmac"],
    ]
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _same(left: Any, right: Any) -> bool:
    return _canonical_bytes(left) == _canonical_bytes(right)


def _scoped(value: Mapping[str, Any], identity: Mapping[str, Any], field: str) -> None:
    for key in SCOPE_KEYS:
        _uuid(value.get(key), f"{field}.{key}")
        _require(value[key] == identity[key], f"{field}: scope mismatch on {key}")


def _validate_signature(signature: Any, field: str) -> Dict[str, Any]:
    sig = _obj(signature, field)
    for key in ("parserVersion", "rulesVersion", "advisoryVersion", "configVersion", "hmacKeyVersion"):
        _text(sig.get(key), f"{field}.{key}")
    for key in ("parserHash", "rulesHash", "advisoryHash", "scanConfigHash"):
        _sha(sig.get(key), f"{field}.{key}")
    policy = [sig.get(key) for key in POLICY_KEYS]
    if any(item is not None for item in policy):
        _require(all(item is not None for item in policy), f"{field}: partial domain policy identity")
        _uuid(policy[0], f"{field}.domainPolicyRevisionId")
        _text(policy[1], f"{field}.domainPolicyVersion")
        _sha(policy[2], f"{field}.domainPolicyHash")
    return sig


def _normalize_location(location: Any, field: str) -> Dict[str, Any]:
    loc = _obj(location, field)
    path = _source_path(loc.get("path"), f"{field}.path")
    line = _integer(loc.get("line"), f"{field}.line", 1, 2147483647)
    column = _integer(loc.get("column"), f"{field}.column", 1, 2147483647)
    end_line = _integer(loc.get("endLine"), f"{field}.endLine", 1, 2147483647)
    end_column = _integer(loc.get("endColumn"), f"{field}.endColumn", 1, 2147483647)
    byte_start = _integer(loc.get("byteStart"), f"{field}.byteStart")
    byte_end = _integer(loc.get("byteEnd"), f"{field}.byteEnd")
    _require(byte_end >= byte_start, f"{field}: byteEnd < byteStart")
    _require(
        end_line > line or (end_line == line and end_column >= column),
        f"{field}: end position before start",
    )
    return {
        "path": path, "line": line, "column": column,
        "endLine": end_line, "endColumn": end_column,
        "byteStart": byte_start, "byteEnd": byte_end,
    }


def _normalize_manifest(manifest_files: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    files: List[Dict[str, Any]] = []
    seen = set()
    for index, raw in enumerate(manifest_files):
        field = f"manifest[{index}]"
        item = _obj(raw, field)
        path = _source_path(item.get("path"), f"{field}.path")
        _require(path not in seen, f"{field}: duplicate path {path}")
        seen.add(path)
        files.append({
            "path": path,
            "sourceHash": _sha(item.get("sourceHash"), f"{field}.sourceHash"),
            "byteLength": _integer(item.get("byteLength"), f"{field}.byteLength"),
            "mediaType": _text(item.get("mediaType"), f"{field}.mediaType"),
        })
    return files


def _collect_evidence(
    parts: Sequence[Tuple[str, Mapping[str, Any]]],
    files: Dict[str, Dict[str, Any]],
    identity: Mapping[str, Any],
) -> Dict[str, Dict[str, Any]]:
    evidence: Dict[str, Dict[str, Any]] = {}
    for name, part in parts:
        for index, raw in enumerate(_arr(part.get("evidence"), f"{name}.evidence")):
            field = f"{name}.evidence[{index}]"
            item = _obj(raw, field)
            _scoped(item, identity, field)
            location = _normalize_location(item.get("location"), f"{field}.location")
            file = files.get(location["path"])
            _require(file is not None, f"{field}: path is outside the trusted manifest")
            source_hash = _sha(item.get("sourceHash"), f"{field}.sourceHash")
            _require(source_hash == file["sourceHash"], f"{field}: sourceHash differs from manifest")
            _require(location["byteEnd"] <= file["byteLength"], f"{field}: byteEnd beyond file length")
            row = {
                "id": _uuid(item.get("id"), f"{field}.id"),
                "location": location,
                "kind": _enum(item.get("kind"), f"{field}.kind", EVIDENCE_KINDS),
                "redactedSnippet": _text(item.get("redactedSnippet"), f"{field}.redactedSnippet", allow_empty=True),
                "sourceHash": source_hash,
                "producerVersion": _text(item.get("producerVersion"), f"{field}.producerVersion"),
            }
            existing = evidence.get(row["id"])
            if existing is not None:
                _require(_same(existing, row), f"{field}: conflicting duplicate evidence id {row['id']}")
            else:
                evidence[row["id"]] = row
    return evidence


def _validate_coverage(
    raw: Any,
    field: str,
    evidence_ids: set,
    known_diagnostics: Mapping[str, Mapping[str, Any]],
    category: Optional[str],
    require_scopes: bool,
) -> Dict[str, Any]:
    coverage = _obj(raw, field)
    status = _enum(coverage.get("status"), f"{field}.status", COVERAGE_STATUSES)
    files_total = _integer(coverage.get("filesTotal"), f"{field}.filesTotal")
    files_visited = _integer(coverage.get("filesVisited"), f"{field}.filesVisited")
    _require(files_visited <= files_total, f"{field}: filesVisited > filesTotal")
    rules = [_text(x, f"{field}.rulesApplied[{i}]") for i, x in enumerate(_arr(coverage.get("rulesApplied"), f"{field}.rulesApplied"))]
    skipped = [_text(x, f"{field}.skippedScopes[{i}]") for i, x in enumerate(_arr(coverage.get("skippedScopes"), f"{field}.skippedScopes"))]
    unresolved = [_text(x, f"{field}.unresolvedSymbols[{i}]") for i, x in enumerate(_arr(coverage.get("unresolvedSymbols"), f"{field}.unresolvedSymbols"))]
    limits = [_text(x, f"{field}.limitsReached[{i}]") for i, x in enumerate(_arr(coverage.get("limitsReached"), f"{field}.limitsReached"))]
    parser_errors = [_uuid(x, f"{field}.parserErrors[{i}]") for i, x in enumerate(_arr(coverage.get("parserErrors"), f"{field}.parserErrors"))]
    for diag_id in parser_errors:
        _require(diag_id in known_diagnostics, f"{field}: parserErrors references unknown diagnostic {diag_id}")
        if category is not None:
            _require(
                known_diagnostics[diag_id]["category"] == category,
                f"{field}: parser diagnostic {diag_id} has a different category",
            )
    scopes: List[Dict[str, Any]] = []
    keys = set()
    for index, raw_scope in enumerate(_arr(coverage.get("scopes"), f"{field}.scopes")):
        scope_field = f"{field}.scopes[{index}]"
        scope = _obj(raw_scope, scope_field)
        row = {
            "path": _source_path(scope.get("path"), f"{scope_field}.path"),
            "ruleId": _text(scope.get("ruleId"), f"{scope_field}.ruleId"),
            "category": _enum(scope.get("category"), f"{scope_field}.category", CATEGORIES),
            "status": _enum(scope.get("status"), f"{scope_field}.status", SCOPE_STATUSES),
            "reason": _text(scope.get("reason"), f"{scope_field}.reason"),
        }
        key = (row["category"], row["path"], row["ruleId"])
        _require(key not in keys, f"{scope_field}: duplicate coverage scope")
        keys.add(key)
        if category is not None:
            _require(row["category"] == category, f"{scope_field}: category differs from scanner")
        scopes.append(row)
    gaps = []
    if status == "complete":
        if not all(scope["status"] == "checked" for scope in scopes):
            gaps.append("non-checked scopes")
        if skipped:
            gaps.append("skipped scopes")
        if parser_errors:
            gaps.append("parser errors")
        if unresolved:
            gaps.append("unresolved symbols")
        if limits:
            gaps.append("limits reached")
        if require_scopes and files_total > 0 and not scopes:
            gaps.append("no scopes on non-empty input")
    if gaps:
        # Не отказываем всему анализу: сохраняем честный partial с причиной,
        # вместо ложного complete.
        status = "partial"
        note = "coverage downgraded to partial: " + ", ".join(gaps)
        limits = list(dict.fromkeys([*limits, note]))
    return {
        "status": status,
        "filesTotal": files_total,
        "filesVisited": files_visited,
        "rulesApplied": rules,
        "scopes": scopes,
        "skippedScopes": skipped,
        "parserErrors": parser_errors,
        "unresolvedSymbols": unresolved,
        "limitsReached": limits,
    }


def validate_bundle(
    envelope: Mapping[str, Any],
    manifest_files: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Validate an AnalysisBundle 0.1 against the trusted manifest.

    Mirrors docs/data-contracts/validate-bundle.mjs plus the SQL invariants.
    Returns normalized rows ready for insertion; raises CanonicalPublishError.
    """
    bundle = _obj(envelope, "envelope")
    _require(bundle.get("schemaVersion") == SCHEMA_VERSION, "envelope: unsupported schemaVersion")

    identity = _obj(bundle.get("identity"), "identity")
    for key in SCOPE_KEYS:
        _uuid(identity.get(key), f"identity.{key}")
    _sha(identity.get("artifactHash"), "identity.artifactHash")
    _sha(identity.get("snapshotManifestHash"), "identity.snapshotManifestHash")
    _uuid(identity.get("workflowRevisionId"), "identity.workflowRevisionId")

    signature = _validate_signature(bundle.get("signature"), "signature")

    scans = _arr(bundle.get("scanResults"), "scanResults")
    _require(len(scans) == 3, "scanResults: exactly three scanner outcomes required")
    categories = [_enum(scan.get("category"), f"scanResults[{i}].category", CATEGORIES) for i, scan in enumerate(scans)]
    _require(len(set(categories)) == 3, "scanResults: SECRET/SCA/SAST must be distinct")
    facts = _obj(bundle.get("codeFacts"), "codeFacts")
    parts: List[Tuple[str, Mapping[str, Any]]] = [
        (f"scanResults[{i}]", scan) for i, scan in enumerate(scans)
    ] + [("codeFacts", facts)]

    for name, part in parts:
        _require(part.get("schemaVersion") == SCHEMA_VERSION, f"{name}: unsupported schemaVersion")
        _scoped(_obj(part.get("identity"), f"{name}.identity"), identity, f"{name}.identity")
        part_signature = _validate_signature(part.get("signature"), f"{name}.signature")
        for key in SIGNATURE_KEYS:
            _require(part_signature[key] == signature[key], f"{name}: signature mismatch on {key}")
        part_identity = part["identity"]
        for key in ("artifactHash", "snapshotManifestHash", "workflowRevisionId"):
            _require(part_identity[key] == identity[key], f"{name}: identity mismatch on {key}")
        _arr(part.get("diagnostics"), f"{name}.diagnostics")
        if name != "codeFacts":
            _arr(part.get("findings"), f"{name}.findings")

    files = {item["path"]: item for item in _normalize_manifest(manifest_files)}
    evidence = _collect_evidence(parts, files, identity)

    # Diagnostics first: coverage.parserErrors must reference them.
    diagnostics: Dict[str, Dict[str, Any]] = {}
    for name, part in parts:
        for index, raw in enumerate(_arr(part.get("diagnostics"), f"{name}.diagnostics")):
            field = f"{name}.diagnostics[{index}]"
            item = _obj(raw, field)
            _scoped(item, identity, field)
            row = {
                "id": _uuid(item.get("id"), f"{field}.id"),
                "category": _enum(item.get("category"), f"{field}.category", CATEGORIES),
                "code": _text(item.get("code"), f"{field}.code"),
                "message": _text(item.get("message"), f"{field}.message"),
                "evidenceId": None,
            }
            evidence_id = item.get("evidenceId")
            if evidence_id is not None:
                row["evidenceId"] = _uuid(evidence_id, f"{field}.evidenceId")
                _require(row["evidenceId"] in evidence, f"{field}: unknown evidence")
            existing = diagnostics.get(row["id"])
            if existing is not None:
                _require(_same(existing, row), f"{field}: conflicting duplicate diagnostic {row['id']}")
            else:
                diagnostics[row["id"]] = row

    scan_outcomes: List[Dict[str, Any]] = []
    coverage_rows: List[Dict[str, Any]] = []
    for index, scan in enumerate(scans):
        name = f"scanResults[{index}]"
        category = categories[index]
        status = _enum(scan.get("status"), f"{name}.status", SCAN_STATUSES)
        producer = _text(scan.get("producerVersion"), f"{name}.producerVersion")
        duration = _integer(scan.get("durationMs"), f"{name}.durationMs")
        coverage = _validate_coverage(
            scan.get("coverage"), f"{name}.coverage", set(evidence), diagnostics, category, True
        )
        if status == "complete" and coverage["status"] != "complete":
            # Coverage уже понижен до честного partial.
            status = "partial"
        for row in coverage["scopes"]:
            coverage_rows.append(dict(row))
        scan_outcomes.append({
            "category": category,
            "status": status,
            "producerVersion": producer,
            "durationMs": duration,
            "coverage": coverage,
        })

    graph_coverage = _validate_coverage(
        facts.get("coverage"), "codeFacts.coverage", set(evidence), diagnostics, None, False
    )

    nodes: Dict[str, Dict[str, Any]] = {}
    for index, raw in enumerate(_arr(facts.get("nodes"), "codeFacts.nodes")):
        field = f"codeFacts.nodes[{index}]"
        item = _obj(raw, field)
        _scoped(item, identity, field)
        refs = [_uuid(x, f"{field}.evidenceRefs[{i}]") for i, x in enumerate(_arr(item.get("evidenceRefs"), f"{field}.evidenceRefs"))]
        refs = list(dict.fromkeys(refs))
        for ref in refs:
            _require(ref in evidence, f"{field}: unknown evidence {ref}")
        kind = _enum(item.get("kind"), f"{field}.kind", NODE_KINDS)
        if kind != "actor":
            _require(refs, f"{field}: source node without evidence")
        row = {
            "id": _uuid(item.get("id"), f"{field}.id"),
            "kind": kind,
            "symbolKey": _text(item.get("symbolKey"), f"{field}.symbolKey"),
            "label": _text(item.get("label"), f"{field}.label"),
            "producerVersion": _text(item.get("producerVersion"), f"{field}.producerVersion"),
            "evidenceRefs": refs,
        }
        _require(row["id"] not in nodes, f"{field}: duplicate node id")
        nodes[row["id"]] = row

    policies: Dict[str, Dict[str, Any]] = {}
    for index, raw in enumerate(_arr(facts.get("policyInputs"), "codeFacts.policyInputs")):
        field = f"codeFacts.policyInputs[{index}]"
        item = _obj(raw, field)
        _require(_uuid(item.get("workspaceId"), f"{field}.workspaceId") == identity["workspaceId"], f"{field}: workspace mismatch")
        approval = _enum(item.get("approvalStatus"), f"{field}.approvalStatus", ("assumption", "approved"))
        approved_by = item.get("approvedBy")
        approved_at = item.get("approvedAt")
        if approval == "assumption":
            _require(approved_by is None and approved_at is None, f"{field}: assumption cannot be approved")
        else:
            _text(approved_by, f"{field}.approvedBy")
            _text(approved_at, f"{field}.approvedAt")
        resources = _arr(item.get("resources"), f"{field}.resources")
        _require(resources, f"{field}: empty resources")
        normalized_resources = []
        for ridx, resource in enumerate(resources):
            rfield = f"{field}.resources[{ridx}]"
            res = _obj(resource, rfield)
            kind = _enum(res.get("kind"), f"{rfield}.kind", ("role_access", "ownership"))
            if kind == "role_access":
                _require(set(res) == {"kind", "resource", "allowedRoles"}, f"{rfield}: unexpected role_access fields")
                roles = _arr(res.get("allowedRoles"), f"{rfield}.allowedRoles")
                _require(roles, f"{rfield}: empty allowedRoles")
                normalized_roles = [_text(role, f"{rfield}.allowedRoles[{i}]") for i, role in enumerate(roles)]
                _require(len(set(normalized_roles)) == len(normalized_roles), f"{rfield}: duplicate roles")
                normalized_resources.append({
                    "kind": "role_access",
                    "resource": _text(res.get("resource"), f"{rfield}.resource"),
                    "allowedRoles": normalized_roles,
                })
            else:
                _require(
                    set(res) == {"kind", "resource", "ownerField", "trustedIdentity", "roleExceptions"},
                    f"{rfield}: unexpected ownership fields",
                )
                exceptions = _arr(res.get("roleExceptions"), f"{rfield}.roleExceptions")
                normalized_exceptions = [_text(role, f"{rfield}.roleExceptions[{i}]") for i, role in enumerate(exceptions)]
                _require(len(set(normalized_exceptions)) == len(normalized_exceptions), f"{rfield}: duplicate roleExceptions")
                normalized_resources.append({
                    "kind": "ownership",
                    "resource": _text(res.get("resource"), f"{rfield}.resource"),
                    "ownerField": _text(res.get("ownerField"), f"{rfield}.ownerField"),
                    "trustedIdentity": _text(res.get("trustedIdentity"), f"{rfield}.trustedIdentity"),
                    "roleExceptions": normalized_exceptions,
                })
        row = {
            "id": _uuid(item.get("id"), f"{field}.id"),
            "version": _text(item.get("version"), f"{field}.version"),
            "hash": _sha(item.get("hash"), f"{field}.hash"),
            "sourceRef": _text(item.get("sourceRef"), f"{field}.sourceRef"),
            "approvedBy": approved_by,
            "approvedAt": approved_at,
            "resources": normalized_resources,
        }
        _require(row["id"] not in policies, f"{field}: duplicate policy id")
        policies[row["id"]] = row

    policy_id = signature["domainPolicyRevisionId"]
    if policy_id is not None:
        _require(policy_id in policies, "signature: domain policy revision has no matching policyInput")
        policy = policies[policy_id]
        _require(policy["version"] == signature["domainPolicyVersion"], "policy: version differs from signature")
        _require(policy["hash"] == signature["domainPolicyHash"], "policy: hash differs from signature")
    else:
        _require(not policies, "policyInputs: present without a domain policy signature")

    edges: Dict[str, Dict[str, Any]] = {}
    for index, raw in enumerate(_arr(facts.get("edges"), "codeFacts.edges")):
        field = f"codeFacts.edges[{index}]"
        item = _obj(raw, field)
        _scoped(item, identity, field)
        refs = [_uuid(x, f"{field}.evidenceRefs[{i}]") for i, x in enumerate(_arr(item.get("evidenceRefs"), f"{field}.evidenceRefs"))]
        refs = list(dict.fromkeys(refs))
        for ref in refs:
            _require(ref in evidence, f"{field}: unknown evidence {ref}")
        from_id = _uuid(item.get("fromId"), f"{field}.fromId")
        to_id = _uuid(item.get("toId"), f"{field}.toId")
        _require(from_id in nodes and to_id in nodes, f"{field}: endpoints outside graph")
        basis = _enum(item.get("basis"), f"{field}.basis", BASES)
        policy_ref = item.get("policyRef")
        if basis == "source":
            _require(policy_ref is None, f"{field}: source edge cannot reference policy")
            _require(refs, f"{field}: source edge without evidence")
        else:
            _require(policy_ref is not None, f"{field}: policy edge without policyRef")
            _require(_uuid(policy_ref, f"{field}.policyRef") in policies, f"{field}: policyRef outside run policies")
            _require(policy_ref == policy_id, f"{field}: policyRef differs from run policy")
        row = {
            "id": _uuid(item.get("id"), f"{field}.id"),
            "fromId": from_id,
            "toId": to_id,
            "kind": _enum(item.get("kind"), f"{field}.kind", EDGE_KINDS),
            "resolution": _enum(item.get("resolution"), f"{field}.resolution", RESOLUTIONS),
            "resolutionReason": _text(item.get("resolutionReason"), f"{field}.resolutionReason"),
            "producerVersion": _text(item.get("producerVersion"), f"{field}.producerVersion"),
            "basis": basis,
            "policyRef": policy_ref,
            "evidenceRefs": refs,
        }
        _require(row["id"] not in edges, f"{field}: duplicate edge id")
        edges[row["id"]] = row

    finding_rows: List[Dict[str, Any]] = []
    finding_evidence_rows: List[Dict[str, Any]] = []
    finding_node_rows: List[Dict[str, Any]] = []
    finding_ids: set = set()
    for index, scan in enumerate(scans):
        name = f"scanResults[{index}]"
        category = categories[index]
        for findex, raw in enumerate(_arr(scan.get("findings"), f"{name}.findings")):
            field = f"{name}.findings[{findex}]"
            item = _obj(raw, field)
            _scoped(item, identity, field)
            _require(_enum(item.get("category"), f"{field}.category", CATEGORIES) == category, f"{field}: category differs from scanner")
            anchor = _obj(item.get("anchor"), f"{field}.anchor")
            anchor_path = _source_path(anchor.get("normalizedPath"), f"{field}.anchor.normalizedPath")
            primary_id = _uuid(item.get("primaryEvidenceId"), f"{field}.primaryEvidenceId")
            _require(primary_id in evidence, f"{field}: unknown primary evidence")
            primary_location = _normalize_location(item.get("primaryLocation"), f"{field}.primaryLocation")
            _require(_same(primary_location, evidence[primary_id]["location"]), f"{field}: primary evidence location differs")
            _require(anchor_path == primary_location["path"], f"{field}: anchor path differs from primary location")
            related = [_uuid(x, f"{field}.relatedEvidenceIds[{i}]") for i, x in enumerate(_arr(item.get("relatedEvidenceIds"), f"{field}.relatedEvidenceIds"))]
            trace = [_uuid(x, f"{field}.traceEvidenceIds[{i}]") for i, x in enumerate(_arr(item.get("traceEvidenceIds"), f"{field}.traceEvidenceIds"))]
            for ref in [primary_id, *related, *trace]:
                _require(ref in evidence, f"{field}: unknown evidence {ref}")
            graph_nodes = [_uuid(x, f"{field}.graphNodeIds[{i}]") for i, x in enumerate(_arr(item.get("graphNodeIds"), f"{field}.graphNodeIds"))]
            graph_nodes = list(dict.fromkeys(graph_nodes))
            for node_id in graph_nodes:
                _require(node_id in nodes, f"{field}: graph node outside bundle")
            secret_hmac = anchor.get("secretHmac")
            if secret_hmac is not None:
                _sha(secret_hmac, f"{field}.anchor.secretHmac")
            _require((category == "SECRET") == (secret_hmac is not None), f"{field}: SECRET/HMAC rule violated")
            _require(_text(item.get("fingerprintVersion"), f"{field}.fingerprintVersion") == FINGERPRINT_VERSION, f"{field}: unsupported fingerprintVersion")
            fingerprint = _sha(item.get("fingerprint"), f"{field}.fingerprint")
            _require(fingerprint == fingerprint_of(item), f"{field}: fingerprint recipe mismatch")
            advisory = [_text(x, f"{field}.advisoryIds[{i}]") for i, x in enumerate(_arr(item.get("advisoryIds"), f"{field}.advisoryIds"))]
            warnings = [_text(x, f"{field}.coverageWarnings[{i}]") for i, x in enumerate(_arr(item.get("coverageWarnings"), f"{field}.coverageWarnings"))]
            row = {
                "id": _uuid(item.get("id"), f"{field}.id"),
                "fingerprint": fingerprint,
                "fingerprintVersion": FINGERPRINT_VERSION,
                "category": category,
                "ruleId": _text(item.get("ruleId"), f"{field}.ruleId"),
                "ruleVersion": _text(item.get("ruleVersion"), f"{field}.ruleVersion"),
                "ruleFamily": _text(item.get("ruleFamily"), f"{field}.ruleFamily"),
                "severity": _enum(item.get("severity"), f"{field}.severity", SEVERITIES),
                "severityBasis": _text(item.get("severityBasis"), f"{field}.severityBasis"),
                "confidence": _enum(item.get("confidence"), f"{field}.confidence", CONFIDENCES),
                "confidenceBasis": _text(item.get("confidenceBasis"), f"{field}.confidenceBasis"),
                "status": _enum(item.get("status"), f"{field}.status", FINDING_STATUSES),
                "primaryEvidenceId": primary_id,
                "symbolKey": _text(anchor.get("symbolKey"), f"{field}.anchor.symbolKey"),
                "normalizedContextHash": _sha(anchor.get("normalizedContextHash"), f"{field}.anchor.normalizedContextHash"),
                "anchorRole": _text(anchor.get("role"), f"{field}.anchor.role"),
                "secretHmac": secret_hmac,
                "advisoryIds": advisory,
                "coverageWarnings": warnings,
            }
            _require(row["id"] not in finding_ids, f"{field}: duplicate finding id {row['id']}")
            finding_ids.add(row["id"])
            finding_rows.append(row)
            for ordinal, ref in enumerate(related):
                finding_evidence_rows.append({"findingId": row["id"], "role": "related", "ordinal": ordinal, "evidenceId": ref})
            for ordinal, ref in enumerate(trace):
                finding_evidence_rows.append({"findingId": row["id"], "role": "trace", "ordinal": ordinal, "evidenceId": ref})
            for node_id in graph_nodes:
                finding_node_rows.append({"findingId": row["id"], "nodeId": node_id})

    node_evidence_rows = [
        {"nodeId": node["id"], "evidenceId": ref}
        for node in nodes.values() for ref in node["evidenceRefs"]
    ]
    edge_evidence_rows = [
        {"edgeId": edge["id"], "evidenceId": ref}
        for edge in edges.values() for ref in edge["evidenceRefs"]
    ]

    counts = {
        "scan_outcomes": len(scan_outcomes),
        "coverage": len(coverage_rows),
        "graph_coverage": 1,
        "evidence": len(evidence),
        "findings": len(finding_rows),
        "code_nodes": len(nodes),
        "code_edges": len(edges),
        "finding_evidence": len(finding_evidence_rows),
        "node_evidence": len(node_evidence_rows),
        "edge_evidence": len(edge_evidence_rows),
        "finding_nodes": len(finding_node_rows),
        "diagnostics": len(diagnostics),
    }
    return {
        "identity": {key: identity[key] for key in SCOPE_KEYS + ("artifactHash", "snapshotManifestHash", "workflowRevisionId")},
        "signature": signature,
        "manifest": list(files.values()),
        "scan_outcomes": scan_outcomes,
        "coverage": coverage_rows,
        "graph_coverage": graph_coverage,
        "evidence": list(evidence.values()),
        "findings": finding_rows,
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
        "policies": list(policies.values()),
        "diagnostics": list(diagnostics.values()),
        "finding_evidence": finding_evidence_rows,
        "node_evidence": node_evidence_rows,
        "edge_evidence": edge_evidence_rows,
        "finding_nodes": finding_node_rows,
        "counts": counts,
    }


def _lock(conn, *parts: Any) -> None:
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (_advisory_key("canonical", *parts),))


def _advisory_key(*parts: Any) -> int:
    raw = "|".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big", signed=True)


def _downgrade_unverified(data: Dict[str, Any], reason: str) -> None:
    """Complete coverage without verified expected scopes becomes honest partial."""
    for outcome in data["scan_outcomes"]:
        coverage = outcome["coverage"]
        if coverage["status"] == "complete":
            coverage["status"] = "partial"
            coverage["limitsReached"] = list(dict.fromkeys([*coverage["limitsReached"], reason]))
        if outcome["status"] == "complete":
            outcome["status"] = "partial"
    graph = data["graph_coverage"]
    if graph["status"] == "complete":
        graph["status"] = "partial"
        graph["limitsReached"] = list(dict.fromkeys([*graph["limitsReached"], reason]))


def _canonical_bundle(envelope: Mapping[str, Any], normalized: Mapping[str, Any]) -> Dict[str, Any]:
    """Copy B's envelope and apply exactly the coverage/status stored in SQL.

    This is explicit, not a silent substitution: the source envelope and its
    document hash stay immutable in bundle_envelopes.source_envelope, while the
    returned/canonical copy carries the honest partial coverage.
    """
    canonical = json.loads(json.dumps(envelope))
    for index, outcome in enumerate(normalized["scan_outcomes"]):
        canonical["scanResults"][index]["status"] = outcome["status"]
        canonical["scanResults"][index]["coverage"] = outcome["coverage"]
    canonical["codeFacts"]["coverage"] = normalized["graph_coverage"]
    return canonical


def _jsonb(value: Any) -> Any:
    try:
        from psycopg.types.json import Jsonb
    except ModuleNotFoundError as exc:  # pragma: no cover - get_pool raises first
        raise DatabaseUnavailable("Нет драйвера PostgreSQL (psycopg)") from exc
    return Jsonb(value)


def _as_text(value: Any, field: str) -> str:
    _require(isinstance(value, (str, datetime)), f"{field}: expected timestamp string")
    if isinstance(value, datetime):
        _require(value.tzinfo is not None, f"{field}: naive datetime")
        return value.isoformat()
    return value


class CanonicalPublisher:
    """Publish one AnalysisBundle into data_contract, atomically and idempotently."""

    def __init__(self, workspace_id: Optional[str] = None):
        self.workspace_id = _uuid(str(workspace_id or _workspace_id()), "workspaceId")

    # -- public API ---------------------------------------------------------
    def publish(
        self,
        envelope: Mapping[str, Any],
        *,
        manifest_files: Sequence[Mapping[str, Any]],
        document_hash: str,
        provenance: Mapping[str, Any],
        artifact: Mapping[str, Any],
        workflow: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> Dict[str, Any]:
        normalized = validate_bundle(envelope, manifest_files)
        identity = normalized["identity"]
        _require(identity["workspaceId"] == self.workspace_id, "identity: workspace differs from publisher")

        expected_signature = compute_document_hash(normalized["manifest"], envelope)
        _sha(document_hash, "document_hash")
        if document_hash != expected_signature:
            raise CanonicalPublishError("document_hash: does not match trusted manifest + envelope")

        prov = self._validate_provenance(provenance)
        if not prov["expectedScopesVerified"]:
            _downgrade_unverified(
                normalized,
                "expected scopes were not verified (expectedScopesVerified=false)",
            )
        artifact_row = self._validate_artifact(artifact, identity)
        workflow_row = self._validate_workflow(workflow, identity)
        run_row = self._validate_run(run, identity)

        with get_pool().connection() as conn:
            # Serialize concurrent publish attempts for the same immutable identity.
            _lock(conn, "run", self.workspace_id, identity["runId"], identity["snapshotId"])
            existing = self._select_existing(conn, identity)
            if existing is not None:
                if existing["document_hash"] != document_hash:
                    raise CanonicalConflictError(
                        "identity is already published with a different document_hash"
                    )
                return self._result(identity, existing, normalized["counts"], idempotent=True)

            self._ensure_workspace(conn)
            self._ensure_artifact(conn, artifact_row, identity)
            self._ensure_snapshot(conn, normalized["manifest"], identity, artifact_row["artifactId"])
            self._ensure_workflow(conn, workflow_row, identity)
            self._insert_policies(conn, normalized["policies"])
            self._insert_run(conn, run_row, workflow_row, normalized["signature"], identity)
            conn.execute(
                "INSERT INTO data_contract.analysis_bundles (workspace_id, run_id, snapshot_id) "
                "VALUES (%s, %s, %s)",
                (self.workspace_id, identity["runId"], identity["snapshotId"]),
            )
            self._insert_members(conn, normalized, identity)

            expected_hash = conn.execute(
                "SELECT data_contract.bundle_document_hash(%s, %s, %s) AS hash",
                (self.workspace_id, identity["runId"], identity["snapshotId"]),
            ).fetchone()["hash"]
            conn.execute(
                "SELECT data_contract.publish_bundle(%s, %s, %s, %s, %s)",
                (
                    self.workspace_id, identity["runId"], identity["snapshotId"],
                    expected_hash, _jsonb(normalized["counts"]),
                ),
            )
            canonical_envelope = _canonical_bundle(envelope, normalized)
            hashes = conn.execute(
                """
                INSERT INTO data_contract.bundle_envelopes (
                    workspace_id, run_id, snapshot_id,
                    source_envelope, canonical_envelope, document_hash,
                    manifest_hash, normalization_version, origin,
                    expected_scopes_verified, domain_policy_applicability
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING envelope_hash, canonical_hash
                """,
                (
                    self.workspace_id, identity["runId"], identity["snapshotId"],
                    _jsonb(envelope), _jsonb(canonical_envelope), document_hash,
                    identity["snapshotManifestHash"], prov["normalizationVersion"],
                    prov["origin"], prov["expectedScopesVerified"],
                    prov["domainPolicyApplicability"],
                ),
            ).fetchone()
            conn.execute(
                """
                UPDATE data_contract.runs
                   SET execution_status = %s, security_verdict = %s, finished_at = now()
                 WHERE workspace_id = %s AND run_id = %s
                """,
                (run_row["executionStatus"], run_row["securityVerdict"],
                 self.workspace_id, identity["runId"]),
            )
            row = conn.execute(
                """
                SELECT bundle_hash, published_at
                  FROM data_contract.analysis_bundles
                 WHERE workspace_id = %s AND run_id = %s AND snapshot_id = %s
                """,
                (self.workspace_id, identity["runId"], identity["snapshotId"]),
            ).fetchone()
            result = {
                "workspaceId": self.workspace_id,
                "runId": identity["runId"],
                "snapshotId": identity["snapshotId"],
                "bundleHash": row["bundle_hash"],
                "publishedAt": row["published_at"].isoformat(),
                "envelopeHash": hashes["envelope_hash"],
                "canonicalHash": hashes["canonical_hash"],
                "documentHash": document_hash,
                "idempotent": False,
                "counts": normalized["counts"],
            }
            return result

    def get_published_envelope(self, run_id: str, snapshot_id: str) -> Optional[Dict[str, Any]]:
        run_uuid = _uuid(str(run_id), "runId")
        snapshot_uuid = _uuid(str(snapshot_id), "snapshotId")
        with get_pool().connection() as conn:
            row = conn.execute(
                """
                SELECT be.canonical_envelope, be.envelope_hash, be.canonical_hash,
                       be.document_hash, be.manifest_hash,
                       be.normalization_version, be.origin,
                       be.expected_scopes_verified, be.domain_policy_applicability,
                       ab.bundle_hash, ab.published_at
                  FROM data_contract.analysis_bundles ab
                  JOIN data_contract.bundle_envelopes be
                    USING (workspace_id, run_id, snapshot_id)
                 WHERE ab.workspace_id = %s AND ab.run_id = %s AND ab.snapshot_id = %s
                   AND ab.published_at IS NOT NULL
                """,
                (self.workspace_id, run_uuid, snapshot_uuid),
            ).fetchone()
            if row is None:
                return None
            files = conn.execute(
                """
                SELECT path, source_hash, byte_length, media_type
                  FROM data_contract.snapshot_files
                 WHERE workspace_id = %s AND snapshot_id = %s
                 ORDER BY path COLLATE "C"
                """,
                (self.workspace_id, snapshot_uuid),
            ).fetchall()
        return {
            "bundle": row["canonical_envelope"],
            "manifest": {
                "complete": True,
                "workspaceId": self.workspace_id,
                "snapshotId": snapshot_uuid,
                "hash": row["manifest_hash"],
                "files": [
                    {
                        "path": file["path"],
                        "sourceHash": file["source_hash"],
                        "byteLength": file["byte_length"],
                        "mediaType": file["media_type"],
                    }
                    for file in files
                ],
            },
            "provenance": {
                "published": True,
                "bundleHash": row["bundle_hash"],
                "normalizationVersion": row["normalization_version"],
                "origin": row["origin"],
                "expectedScopesVerified": row["expected_scopes_verified"],
                "domainPolicyApplicability": row["domain_policy_applicability"],
            },
            "envelopeHash": row["envelope_hash"],
            "canonicalHash": row["canonical_hash"],
            "documentHash": row["document_hash"],
            "publishedAt": row["published_at"].isoformat(),
        }

    def get_source_envelope(self, run_id: str, snapshot_id: str) -> Optional[Dict[str, Any]]:
        """Raw immutable B envelope plus its hashes, separate from canonical coverage."""
        run_uuid = _uuid(str(run_id), "runId")
        snapshot_uuid = _uuid(str(snapshot_id), "snapshotId")
        with get_pool().connection() as conn:
            row = conn.execute(
                """
                SELECT be.source_envelope, be.envelope_hash, be.canonical_hash,
                       be.document_hash, be.manifest_hash, ab.bundle_hash, ab.published_at
                  FROM data_contract.analysis_bundles ab
                  JOIN data_contract.bundle_envelopes be
                    USING (workspace_id, run_id, snapshot_id)
                 WHERE ab.workspace_id = %s AND ab.run_id = %s AND ab.snapshot_id = %s
                   AND ab.published_at IS NOT NULL
                """,
                (self.workspace_id, run_uuid, snapshot_uuid),
            ).fetchone()
            if row is None:
                return None
        return {
            "bundle": row["source_envelope"],
            "manifestHash": row["manifest_hash"],
            "documentHash": row["document_hash"],
            "envelopeHash": row["envelope_hash"],
            "canonicalHash": row["canonical_hash"],
            "bundleHash": row["bundle_hash"],
            "publishedAt": row["published_at"].isoformat(),
        }

    def list_published_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        limit = _integer(limit, "limit", 1, 1000)
        with get_pool().connection() as conn:
            rows = conn.execute(
                """
                SELECT ab.run_id, ab.snapshot_id, ab.bundle_hash, ab.published_at,
                       be.envelope_hash, be.canonical_hash, be.canonical_envelope->'identity'->>'workflowRevisionId' AS workflow_revision_id
                  FROM data_contract.analysis_bundles ab
                  JOIN data_contract.bundle_envelopes be
                    USING (workspace_id, run_id, snapshot_id)
                 WHERE ab.workspace_id = %s AND ab.published_at IS NOT NULL
                 ORDER BY ab.published_at DESC
                 LIMIT %s
                """,
                (self.workspace_id, limit),
            ).fetchall()
        return [
            {
                "runId": str(row["run_id"]),
                "snapshotId": str(row["snapshot_id"]),
                "bundleHash": row["bundle_hash"],
                "publishedAt": row["published_at"].isoformat(),
                "envelopeHash": row["envelope_hash"],
                "canonicalHash": row["canonical_hash"],
                "workflowRevisionId": row["workflow_revision_id"],
            }
            for row in rows
        ]

    # -- validation of bridge-only inputs ----------------------------------
    def _validate_provenance(self, provenance: Mapping[str, Any]) -> Dict[str, Any]:
        item = _obj(provenance, "provenance")
        verified = item.get("expectedScopesVerified")
        _require(isinstance(verified, bool), "provenance.expectedScopesVerified: expected boolean")
        return {
            "origin": _enum(item.get("origin"), "provenance.origin", ORIGINS),
            "normalizationVersion": _text(item.get("normalizationVersion"), "provenance.normalizationVersion"),
            "expectedScopesVerified": verified,
            "domainPolicyApplicability": _enum(
                item.get("domainPolicyApplicability"),
                "provenance.domainPolicyApplicability",
                APPLICABILITY,
            ),
        }

    def _validate_artifact(self, artifact: Mapping[str, Any], identity: Mapping[str, Any]) -> Dict[str, Any]:
        item = _obj(artifact, "artifact")
        artifact_id = _uuid(item.get("artifactId", item.get("artifact_id")), "artifact.artifactId")
        artifact_hash = _sha(item.get("artifactHash", item.get("artifact_hash")), "artifact.artifactHash")
        _require(artifact_hash == identity["artifactHash"], "artifact: hash differs from envelope identity")
        return {
            "artifactId": artifact_id,
            "artifactHash": artifact_hash,
            "storageRef": _text(item.get("storageRef", item.get("storage_ref")), "artifact.storageRef"),
            "compressedBytes": _integer(
                item.get("compressedBytes", item.get("compressed_bytes")), "artifact.compressedBytes"
            ),
            "createdAt": item.get("createdAt", item.get("created_at")),
            "expiresAt": item.get("expiresAt", item.get("expires_at")),
        }

    def _validate_workflow(self, workflow: Mapping[str, Any], identity: Mapping[str, Any]) -> Dict[str, Any]:
        item = _obj(workflow, "workflow")
        revision_id = _uuid(
            item.get("revisionId", item.get("revision_id", item.get("workflowRevisionId"))),
            "workflow.revisionId",
        )
        _require(revision_id == identity["workflowRevisionId"], "workflow: revisionId differs from identity")
        definition = _obj(item.get("definition"), "workflow.definition")
        definition_hash_value = item.get("definitionHash", item.get("definition_hash"))
        if definition_hash_value is None:
            definition_hash_value = definition_hash(definition)
        return {
            "workflowId": _uuid(item.get("workflowId", item.get("workflow_id")), "workflow.workflowId"),
            "name": _text(item.get("name"), "workflow.name"),
            "draftVersion": _integer(item.get("draftVersion", item.get("draft_version", 1)), "workflow.draftVersion", 1),
            "definition": definition,
            "definitionHash": _sha(definition_hash_value, "workflow.definitionHash"),
            "revisionId": revision_id,
            "revisionNumber": _integer(
                item.get("revisionNumber", item.get("revision_number", 1)), "workflow.revisionNumber", 1
            ),
        }

    def _validate_run(self, run: Mapping[str, Any], identity: Mapping[str, Any]) -> Dict[str, Any]:
        item = _obj(run, "run")
        run_id = _uuid(item.get("runId", item.get("run_id")), "run.runId")
        _require(run_id == identity["runId"], "run: runId differs from identity")
        status = item.get("executionStatus", item.get("execution_status", "succeeded"))
        verdict = item.get("securityVerdict", item.get("security_verdict", "inconclusive"))
        _enum(status, "run.executionStatus", ("queued", "running", "succeeded", "failed", "interrupted", "cancelled"))
        _enum(verdict, "run.securityVerdict", ("pending", "pass", "fail", "inconclusive"))
        return {
            "runId": run_id,
            "executionStatus": status,
            "securityVerdict": verdict,
            "createdAt": item.get("createdAt", item.get("created_at")),
        }

    # -- database steps -----------------------------------------------------
    def _select_existing(self, conn, identity: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        return conn.execute(
            """
            SELECT ab.bundle_hash, ab.published_at, be.document_hash, be.envelope_hash, be.canonical_hash
              FROM data_contract.analysis_bundles ab
              LEFT JOIN data_contract.bundle_envelopes be
                USING (workspace_id, run_id, snapshot_id)
             WHERE ab.workspace_id = %s AND ab.run_id = %s AND ab.snapshot_id = %s
               AND ab.published_at IS NOT NULL
            """,
            (self.workspace_id, identity["runId"], identity["snapshotId"]),
        ).fetchone()

    def _ensure_workspace(self, conn) -> None:
        conn.execute(
            "INSERT INTO data_contract.workspaces (workspace_id, name) VALUES (%s, %s) "
            "ON CONFLICT (workspace_id) DO NOTHING",
            (self.workspace_id, f"canonical:{self.workspace_id}"),
        )

    def _ensure_artifact(self, conn, artifact: Mapping[str, Any], identity: Mapping[str, Any]) -> None:
        _lock(conn, "artifact", self.workspace_id, artifact["artifactId"])
        created_at = _as_text(artifact["createdAt"], "artifact.createdAt") if artifact["createdAt"] else None
        expires_at = _as_text(artifact["expiresAt"], "artifact.expiresAt")
        conn.execute(
            """
            INSERT INTO data_contract.artifacts (
                workspace_id, artifact_id, artifact_hash, storage_ref,
                compressed_bytes, created_at, expires_at
            ) VALUES (%s, %s, %s, %s, %s, COALESCE(%s::timestamptz, now()), %s::timestamptz)
            ON CONFLICT (workspace_id, artifact_id) DO NOTHING
            """,
            (
                self.workspace_id, artifact["artifactId"], artifact["artifactHash"],
                artifact["storageRef"], artifact["compressedBytes"], created_at, expires_at,
            ),
        )
        row = conn.execute(
            "SELECT artifact_hash, storage_ref, compressed_bytes FROM data_contract.artifacts "
            "WHERE workspace_id = %s AND artifact_id = %s",
            (self.workspace_id, artifact["artifactId"]),
        ).fetchone()
        if row is None:  # pragma: no cover - insert above guarantees it
            raise CanonicalPublishError("artifact: insert was not persisted")
        if (row["artifact_hash"], row["storage_ref"], row["compressed_bytes"]) != (
            artifact["artifactHash"], artifact["storageRef"], artifact["compressedBytes"]
        ):
            raise CanonicalConflictError("artifact: id already exists with different immutable metadata")

    def _ensure_snapshot(self, conn, files: Sequence[Mapping[str, Any]],
                         identity: Mapping[str, Any], artifact_id: str) -> None:
        snapshot_id = identity["snapshotId"]
        _lock(conn, "snapshot", self.workspace_id, snapshot_id)
        row = conn.execute(
            "SELECT artifact_id, artifact_hash, manifest_hash, sealed_at "
            "FROM data_contract.snapshots WHERE workspace_id = %s AND snapshot_id = %s",
            (self.workspace_id, snapshot_id),
        ).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO data_contract.snapshots (
                    workspace_id, snapshot_id, artifact_id, artifact_hash
                ) VALUES (%s, %s, %s, %s)
                ON CONFLICT (workspace_id, snapshot_id) DO NOTHING
                """,
                (self.workspace_id, snapshot_id, artifact_id, identity["artifactHash"]),
            )
            row = conn.execute(
                "SELECT artifact_id, artifact_hash, manifest_hash, sealed_at "
                "FROM data_contract.snapshots WHERE workspace_id = %s AND snapshot_id = %s",
                (self.workspace_id, snapshot_id),
            ).fetchone()
            if row is None:  # pragma: no cover
                raise CanonicalPublishError("snapshot: insert was not persisted")
            if str(row["artifact_id"]) != artifact_id or row["artifact_hash"] != identity["artifactHash"]:
                raise CanonicalConflictError("snapshot: exists with a different artifact identity")
            sealed = row["sealed_at"] is not None
        else:
            if str(row["artifact_id"]) != artifact_id or row["artifact_hash"] != identity["artifactHash"]:
                raise CanonicalConflictError("snapshot: exists with a different artifact identity")
            sealed = row["sealed_at"] is not None
        if not sealed:
            for file in files:
                conn.execute(
                    """
                    INSERT INTO data_contract.snapshot_files (
                        workspace_id, snapshot_id, path, source_hash, byte_length, media_type
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (workspace_id, snapshot_id, path) DO NOTHING
                    """,
                    (
                        self.workspace_id, snapshot_id, file["path"],
                        file["sourceHash"], file["byteLength"], file["mediaType"],
                    ),
                )
        stored = conn.execute(
            "SELECT path, source_hash, byte_length, media_type FROM data_contract.snapshot_files "
            "WHERE workspace_id = %s AND snapshot_id = %s",
            (self.workspace_id, snapshot_id),
        ).fetchall()
        stored_by_path = {item["path"]: item for item in stored}
        for file in files:
            current = stored_by_path.get(file["path"])
            if current is None:
                raise CanonicalConflictError(f"snapshot: sealed snapshot is missing {file['path']}")
            if (current["source_hash"], current["byte_length"], current["media_type"]) != (
                file["sourceHash"], file["byteLength"], file["mediaType"]
            ):
                raise CanonicalConflictError(f"snapshot: file metadata differs for {file['path']}")
        if len(stored_by_path) != len(files):
            raise CanonicalConflictError("snapshot: sealed snapshot has files outside the trusted manifest")
        computed = conn.execute(
            "SELECT data_contract.snapshot_manifest_hash(%s, %s) AS hash",
            (self.workspace_id, snapshot_id),
        ).fetchone()["hash"]
        if computed != identity["snapshotManifestHash"]:
            raise CanonicalPublishError("identity.snapshotManifestHash differs from SQL snapshot_manifest_hash")
        if not sealed:
            conn.execute(
                "UPDATE data_contract.snapshots SET manifest_hash = %s, sealed_at = clock_timestamp() "
                "WHERE workspace_id = %s AND snapshot_id = %s AND sealed_at IS NULL",
                (computed, self.workspace_id, snapshot_id),
            )

    def _ensure_workflow(self, conn, workflow: Mapping[str, Any], identity: Mapping[str, Any]) -> None:
        _lock(conn, "workflow", self.workspace_id, workflow["workflowId"])
        conn.execute(
            """
            INSERT INTO data_contract.workflows (
                workspace_id, workflow_id, name, draft_version, draft
            ) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (workspace_id, workflow_id) DO NOTHING
            """,
            (
                self.workspace_id, workflow["workflowId"], workflow["name"],
                workflow["draftVersion"], _jsonb(workflow["definition"]),
            ),
        )
        conn.execute(
            """
            INSERT INTO data_contract.workflow_revisions (
                workspace_id, workflow_revision_id, workflow_id, revision_number,
                definition_hash, definition
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (workspace_id, workflow_revision_id) DO NOTHING
            """,
            (
                self.workspace_id, workflow["revisionId"], workflow["workflowId"],
                workflow["revisionNumber"], workflow["definitionHash"],
                _jsonb(workflow["definition"]),
            ),
        )
        stored = conn.execute(
            "SELECT definition_hash FROM data_contract.workflow_revisions "
            "WHERE workspace_id = %s AND workflow_revision_id = %s",
            (self.workspace_id, workflow["revisionId"]),
        ).fetchone()
        if stored is None or stored["definition_hash"] != workflow["definitionHash"]:
            raise CanonicalConflictError("workflow: revision exists with a different definition hash")

    def _insert_policies(self, conn, policies: Sequence[Mapping[str, Any]]) -> None:
        for policy in policies:
            _lock(conn, "policy", self.workspace_id, policy["id"])
            conn.execute(
                """
                INSERT INTO data_contract.domain_policy_revisions (
                    workspace_id, policy_revision_id, version, policy_hash,
                    source_ref, approved_by, approved_at, policy
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (workspace_id, policy_revision_id) DO NOTHING
                """,
                (
                    self.workspace_id, policy["id"], policy["version"], policy["hash"],
                    policy["sourceRef"], policy["approvedBy"],
                    _as_text(policy["approvedAt"], "policy.approvedAt") if policy["approvedAt"] else None,
                    _jsonb({"resources": policy["resources"]}),
                ),
            )
            stored = conn.execute(
                "SELECT version, policy_hash FROM data_contract.domain_policy_revisions "
                "WHERE workspace_id = %s AND policy_revision_id = %s",
                (self.workspace_id, policy["id"]),
            ).fetchone()
            if stored is None or stored["version"] != policy["version"] or stored["policy_hash"] != policy["hash"]:
                raise CanonicalConflictError("policy: revision exists with different immutable content")

    def _insert_run(
        self,
        conn,
        run: Mapping[str, Any],
        workflow: Mapping[str, Any],
        signature: Mapping[str, Any],
        identity: Mapping[str, Any],
    ) -> None:
        created_at = _as_text(run["createdAt"], "run.createdAt") if run["createdAt"] else None
        conn.execute(
            """
            INSERT INTO data_contract.runs (
                workspace_id, run_id, snapshot_id, artifact_hash,
                workflow_id, workflow_revision_id,
                parser_version, parser_hash, rules_version, rules_hash,
                advisory_version, advisory_hash, config_version, scan_config_hash,
                domain_policy_revision_id, domain_policy_version, domain_policy_hash,
                hmac_key_version, execution_status, security_verdict, created_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, 'queued', 'pending', COALESCE(%s::timestamptz, now())
            )
            """,
            (
                self.workspace_id, identity["runId"], identity["snapshotId"], identity["artifactHash"],
                workflow["workflowId"], workflow["revisionId"],
                signature["parserVersion"], signature["parserHash"],
                signature["rulesVersion"], signature["rulesHash"],
                signature["advisoryVersion"], signature["advisoryHash"],
                signature["configVersion"], signature["scanConfigHash"],
                signature["domainPolicyRevisionId"], signature["domainPolicyVersion"],
                signature["domainPolicyHash"], signature["hmacKeyVersion"], created_at,
            ),
        )

    def _insert_members(self, conn, data: Mapping[str, Any], identity: Mapping[str, Any]) -> None:
        ws, run_id, snapshot_id = self.workspace_id, identity["runId"], identity["snapshotId"]

        for outcome in data["scan_outcomes"]:
            conn.execute(
                """
                INSERT INTO data_contract.scan_outcomes (
                    workspace_id, run_id, snapshot_id, category, status,
                    producer_version, coverage_summary, duration_ms
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    ws, run_id, snapshot_id, outcome["category"], outcome["status"],
                    outcome["producerVersion"], _jsonb(outcome["coverage"]), outcome["durationMs"],
                ),
            )
        for scope in data["coverage"]:
            conn.execute(
                """
                INSERT INTO data_contract.coverage (
                    workspace_id, run_id, snapshot_id, path, rule_id,
                    category, status, reason
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    ws, run_id, snapshot_id, scope["path"], scope["ruleId"],
                    scope["category"], scope["status"], scope["reason"],
                ),
            )
        conn.execute(
            "INSERT INTO data_contract.graph_coverage (workspace_id, run_id, snapshot_id, coverage) "
            "VALUES (%s, %s, %s, %s)",
            (ws, run_id, snapshot_id, _jsonb(data["graph_coverage"])),
        )
        for evidence in data["evidence"]:
            location = evidence["location"]
            conn.execute(
                """
                INSERT INTO data_contract.evidence (
                    workspace_id, run_id, snapshot_id, evidence_id, path, source_hash,
                    line, column_number, end_line, end_column, byte_start, byte_end,
                    kind, redacted_snippet, producer_version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    ws, run_id, snapshot_id, evidence["id"], location["path"],
                    evidence["sourceHash"], location["line"], location["column"],
                    location["endLine"], location["endColumn"], location["byteStart"],
                    location["byteEnd"], evidence["kind"], evidence["redactedSnippet"],
                    evidence["producerVersion"],
                ),
            )
        for finding in data["findings"]:
            conn.execute(
                """
                INSERT INTO data_contract.findings (
                    workspace_id, run_id, snapshot_id, finding_id, fingerprint,
                    fingerprint_version, category, rule_id, rule_version, rule_family,
                    severity, severity_basis, confidence, confidence_basis, status,
                    primary_evidence_id, symbol_key, normalized_context_hash,
                    anchor_role, secret_hmac, advisory_ids, coverage_warnings
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    ws, run_id, snapshot_id, finding["id"], finding["fingerprint"],
                    finding["fingerprintVersion"], finding["category"], finding["ruleId"],
                    finding["ruleVersion"], finding["ruleFamily"], finding["severity"],
                    finding["severityBasis"], finding["confidence"], finding["confidenceBasis"],
                    finding["status"], finding["primaryEvidenceId"], finding["symbolKey"],
                    finding["normalizedContextHash"], finding["anchorRole"],
                    finding["secretHmac"], finding["advisoryIds"], finding["coverageWarnings"],
                ),
            )
        for node in data["nodes"]:
            conn.execute(
                """
                INSERT INTO data_contract.code_nodes (
                    workspace_id, run_id, snapshot_id, node_id, kind,
                    symbol_key, label, producer_version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    ws, run_id, snapshot_id, node["id"], node["kind"],
                    node["symbolKey"], node["label"], node["producerVersion"],
                ),
            )
        for edge in data["edges"]:
            conn.execute(
                """
                INSERT INTO data_contract.code_edges (
                    workspace_id, run_id, snapshot_id, edge_id, from_id, to_id,
                    kind, resolution, resolution_reason, producer_version,
                    basis, policy_revision_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    ws, run_id, snapshot_id, edge["id"], edge["fromId"], edge["toId"],
                    edge["kind"], edge["resolution"], edge["resolutionReason"],
                    edge["producerVersion"], edge["basis"], edge["policyRef"],
                ),
            )
        for row in data["finding_evidence"]:
            conn.execute(
                """
                INSERT INTO data_contract.finding_evidence (
                    workspace_id, run_id, snapshot_id, finding_id, evidence_id, role, ordinal
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (ws, run_id, snapshot_id, row["findingId"], row["evidenceId"], row["role"], row["ordinal"]),
            )
        for row in data["node_evidence"]:
            conn.execute(
                "INSERT INTO data_contract.node_evidence (workspace_id, run_id, snapshot_id, node_id, evidence_id) "
                "VALUES (%s, %s, %s, %s, %s)",
                (ws, run_id, snapshot_id, row["nodeId"], row["evidenceId"]),
            )
        for row in data["edge_evidence"]:
            conn.execute(
                "INSERT INTO data_contract.edge_evidence (workspace_id, run_id, snapshot_id, edge_id, evidence_id) "
                "VALUES (%s, %s, %s, %s, %s)",
                (ws, run_id, snapshot_id, row["edgeId"], row["evidenceId"]),
            )
        for row in data["finding_nodes"]:
            conn.execute(
                "INSERT INTO data_contract.finding_nodes (workspace_id, run_id, snapshot_id, finding_id, node_id) "
                "VALUES (%s, %s, %s, %s, %s)",
                (ws, run_id, snapshot_id, row["findingId"], row["nodeId"]),
            )
        for diagnostic in data["diagnostics"]:
            conn.execute(
                """
                INSERT INTO data_contract.diagnostics (
                    workspace_id, run_id, snapshot_id, diagnostic_id,
                    category, code, message, evidence_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    ws, run_id, snapshot_id, diagnostic["id"], diagnostic["category"],
                    diagnostic["code"], diagnostic["message"], diagnostic["evidenceId"],
                ),
            )

    @staticmethod
    def _result(identity: Mapping[str, Any], existing: Mapping[str, Any],
                counts: Mapping[str, int], *, idempotent: bool) -> Dict[str, Any]:
        return {
            "workspaceId": identity["workspaceId"],
            "runId": identity["runId"],
            "snapshotId": identity["snapshotId"],
            "bundleHash": existing["bundle_hash"],
            "publishedAt": existing["published_at"].isoformat(),
            "envelopeHash": existing["envelope_hash"],
            "canonicalHash": existing["canonical_hash"],
            "documentHash": existing["document_hash"],
            "idempotent": idempotent,
            "counts": counts,
        }