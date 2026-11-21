"""Map a completed scan onto the shared ``AnalysisBundle`` contract (v0.1).

The contract in ``docs/data-contracts`` is a team proposal, not an approved
interface.  This module adds the identifiers, coordinates, hashes and
provenance the map/comparison packages need without changing scanner results:

* every finding keeps its scanner rule, class and evidence coordinates;
* a node or edge exists only when a declaration or call justifies it;
* a link the scanner could not resolve stays ``unknown`` instead of becoming
  an arrow;
* SECRET findings carry an HMAC digest and a masked snippet, never the value.

Raw secrets never reach the bundle: snippets from files with SECRET findings
have their string literals masked, and known secret values are replaced
everywhere.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .model import Finding, ScanResult
from .policy import OBJECT_ACCESS_POLICY, OWNER_COLUMNS
from .project import Project
from .textfiles import EXCLUDED_DIRS, MAX_FILE_BYTES, SELF_DIR_NAMES, TEXT_SUFFIXES

SCHEMA_VERSION = "0.1"
PRODUCER_VERSION = "cg26-analyzers/0.1.0"
NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL,
    "https://github.com/SoloveyMaks/41l1-n3dra/analyzers/cg26",
)

# The contract requires an HMAC key that never lives in the database.  A real
# run must pass one in; the demo key exists only so local bundles can be built.
DEMO_HMAC_KEY = b"cg26-local-demo-key-not-for-production"
DEMO_HMAC_KEY_VERSION = "local-demo-1"

CATEGORIES = ("SECRET", "SCA", "SAST")

# A family is responsible only for the file kinds its rules can read.  This is
# the "expected scope set" B defines for coverage, not the whole repository.
FAMILY_SUFFIXES: dict[str, set[str] | None] = {
    "SECRET": None,  # every scanned text file: code, YAML, properties, README
    "SCA": {".kts", ".gradle", ".lockfile", ".properties", ".toml"},
    "SAST": {".kt", ".kts"},
}

_MEDIA_TYPES = {
    ".kt": "text/x-kotlin",
    ".kts": "text/x-kotlin",
    ".gradle": "text/x-gradle",
    ".properties": "text/plain",
    ".lockfile": "text/plain",
    ".yml": "application/yaml",
    ".yaml": "application/yaml",
    ".json": "application/json",
    ".xml": "application/xml",
    ".sql": "application/sql",
    ".md": "text/markdown",
}

_EVIDENCE_KINDS = {
    ".kt": "ast",
    ".kts": "dsl",
    ".gradle": "dsl",
    ".lockfile": "manifest",
    ".properties": "source",
    ".yml": "source",
    ".yaml": "source",
    ".sql": "sql",
}

_ROLE_BY_CLASS = {
    "password": "declaration",
    "api-key": "declaration",
    "configuration-token": "declaration",
    "secret-fallback": "declaration",
    "recoverable-token": "declaration",
    "known-vulnerable-dependency": "manifest",
    "sql-injection": "sink",
    "tls-verification": "sink",
    "credentialed-cors": "config",
    "route-authorization": "guard",
    "object-authorization": "sink",
}

_STRING_LITERAL = re.compile(r'"(?:[^"\\\n]|\\.)*"')
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"//[^\n]*")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

_POLICY_ID = str(uuid.uuid5(NAMESPACE, "policy:object-access:v1"))
_POLICY_VERSION = "cg26-policy/1"
_POLICY_HASH = hashlib.sha256(
    (OBJECT_ACCESS_POLICY + "\n" + "\n".join(OWNER_COLUMNS)).encode("utf-8")
).hexdigest()

PARSER_FILES = (
    "kotlin_lexer.py",
    "kotlin_index.py",
    "project.py",
    "textfiles.py",
    "yaml_lite.py",
    "versions.py",
)

RULES_FILES = (
    "secrets.py",
    "sca.py",
    "gradle.py",
    "policy.py",
    "sast/common.py",
    "sast/sql_injection.py",
    "sast/tls_verification.py",
    "sast/cors.py",
    "sast/route_authorization.py",
    "sast/object_authorization.py",
)


# --------------------------------------------------------------------- helpers


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _compact(payload: Any) -> str:
    """Compact JSON identical to ``JSON.stringify`` for our value shapes."""

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _uuid(name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, name))


def _suffix(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _media_type(path: str) -> str:
    return _MEDIA_TYPES.get(_suffix(path), "text/plain")


def _evidence_kind(path: str) -> str:
    return _EVIDENCE_KINDS.get(_suffix(path), "source")


def _line_starts(text: str) -> tuple[int, ...]:
    starts = [0]
    for index, char in enumerate(text):
        if char == "\n":
            starts.append(index + 1)
    return tuple(starts)


def _byte_offset(text: str, char_index: int) -> int:
    return len(text[:char_index].encode("utf-8"))


@dataclass(frozen=True)
class SourceFile:
    path: str
    raw: bytes
    text: str
    digest: str
    line_starts: tuple[int, ...]


def load_sources(project: Project) -> dict[str, SourceFile]:
    """Read the original bytes of every file the project index accepted."""

    sources: dict[str, SourceFile] = {}
    for rel_path, text_file in project.text_files.items():
        try:
            raw = Path(text_file.abs_path).read_bytes()
        except OSError:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            # The contract needs UTF-8 coordinates; a non-UTF-8 file gets no
            # invented text positions.
            continue
        sources[rel_path] = SourceFile(rel_path, raw, text, _sha256(raw), _line_starts(text))
    return sources


# ------------------------------------------------------------------- evidence


class EvidenceBook:
    """Computes contract locations, evidence refs and redacted snippets."""

    def __init__(
        self,
        scope: dict[str, str],
        sources: dict[str, SourceFile],
        secret_values: Iterable[str],
        mask_paths: set[str],
    ) -> None:
        self.scope = scope
        self.sources = sources
        self.secret_values = tuple(sorted({v for v in secret_values if v}, key=len, reverse=True))
        self.mask_paths = mask_paths
        self._items: dict[str, dict[str, Any]] = {}
        self._by_key: dict[tuple[Any, ...], str] = {}

    # -- geometry ---------------------------------------------------------

    def _span(self, source: SourceFile, line: int | None, end_line: int | None):
        count = len(source.line_starts)
        line = max(1, min(int(line or 1), count))
        end_line = max(line, min(int(end_line or line), count))
        start_char = source.line_starts[line - 1]
        end_start = source.line_starts[end_line - 1]
        row_end = source.text.find("\n", end_start)
        if row_end == -1:
            row_end = len(source.text)
        row = source.text[end_start:row_end]
        if row.endswith("\r"):
            row = row[:-1]
        return line, end_line, start_char, end_start + len(row), len(row) + 1

    def location(self, path: str, line: int | None, end_line: int | None = None) -> dict[str, Any] | None:
        source = self.sources.get(path)
        if source is None:
            return None
        line, end_line, start_char, end_char, end_column = self._span(source, line, end_line)
        return {
            "path": path,
            "line": line,
            "column": 1,
            "endLine": end_line,
            "endColumn": end_column,
            "byteStart": _byte_offset(source.text, start_char),
            "byteEnd": _byte_offset(source.text, end_char),
        }

    def raw_snippet(self, path: str, line: int | None, end_line: int | None = None) -> str:
        source = self.sources.get(path)
        if source is None:
            return ""
        _, _, start_char, end_char, _ = self._span(source, line, end_line)
        return source.text[start_char:end_char]

    def _redact(self, text: str, path: str, mask_literals: bool) -> str:
        for value in self.secret_values:
            if len(value) >= 6 and value in text:
                text = text.replace(value, "*" * min(len(value), 16))
        if mask_literals or path in self.mask_paths:
            text = _STRING_LITERAL.sub('"[redacted]"', text)
        return text or "…"

    # -- refs -------------------------------------------------------------

    def ref(
        self,
        path: str,
        line: int | None,
        end_line: int | None = None,
        mask_literals: bool = False,
    ) -> str | None:
        location = self.location(path, line, end_line)
        if location is None:
            return None
        key = (location["path"], location["line"], location["endLine"], mask_literals)
        existing = self._by_key.get(key)
        if existing is not None:
            return existing
        identifier = _uuid("evidence:" + ":".join(str(part) for part in key))
        source = self.sources[path]
        _, _, start_char, end_char, _ = self._span(source, location["line"], location["endLine"])
        snippet = self._redact(source.text[start_char:end_char], path, mask_literals)
        self._items[identifier] = {
            **self.scope,
            "id": identifier,
            "location": location,
            "kind": _evidence_kind(path),
            "redactedSnippet": snippet,
            "sourceHash": source.digest,
            "producerVersion": PRODUCER_VERSION,
        }
        self._by_key[key] = identifier
        return identifier

    def items(self) -> list[dict[str, Any]]:
        return [self._items[key] for key in sorted(self._items)]

    def subset(self, identifiers: Iterable[str]) -> list[dict[str, Any]]:
        return [self._items[key] for key in sorted(set(identifiers)) if key in self._items]

    def get(self, identifier: str) -> dict[str, Any] | None:
        return self._items.get(identifier)


# ------------------------------------------------------------------ identity


def _manifest(sources: dict[str, SourceFile]) -> list[dict[str, Any]]:
    return [
        {
            "path": path,
            "sourceHash": sources[path].digest,
            "byteLength": len(sources[path].raw),
            "mediaType": _media_type(path),
        }
        for path in sorted(sources)
    ]


def _identity(
    sources: dict[str, SourceFile],
    workspace_id: str | None,
    run_id: str | None,
    snapshot_id: str | None,
    workflow_revision_id: str | None,
) -> dict[str, str]:
    manifest = _manifest(sources)
    manifest_hash = _sha256(_compact(manifest).encode("utf-8"))
    # Local directory digest, not the hash of an uploaded ZIP.  A real run
    # passes the artifact hash from intake; the prefix keeps the two apart.
    artifact_hash = _sha256(b"local-directory/v1\n" + manifest_hash.encode("ascii"))

    def pick(value: str | None, fallback_name: str) -> str:
        if value is None:
            return _uuid(fallback_name)
        if not _UUID.match(value):
            raise ValueError(f"not a lowercase UUID: {value}")
        return value

    return {
        "workspaceId": pick(workspace_id, "workspace:local"),
        "runId": pick(run_id, f"run:{manifest_hash}"),
        "snapshotId": pick(snapshot_id, f"snapshot:{manifest_hash}"),
        "artifactHash": artifact_hash,
        "snapshotManifestHash": manifest_hash,
        "workflowRevisionId": pick(workflow_revision_id, "workflow:manual"),
    }


def _hash_files(names: Iterable[str]) -> str:
    base = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for name in sorted(names):
        digest.update(name.encode("utf-8"))
        digest.update(b"\x00")
        digest.update((base / name).read_bytes())
        digest.update(b"\x00")
    return digest.hexdigest()


def _signature(database_bytes: bytes | None, database_snapshot: dict[str, Any] | None) -> dict[str, Any]:
    config = {
        "families": {name: sorted(suffixes) if suffixes else None for name, suffixes in FAMILY_SUFFIXES.items()},
        "excludedDirs": sorted(EXCLUDED_DIRS),
        "selfDirNames": sorted(SELF_DIR_NAMES),
        "maxFileBytes": MAX_FILE_BYTES,
        "suffixes": sorted(TEXT_SUFFIXES),
    }
    if database_snapshot:
        advisory_version = "osv-maven/" + str(database_snapshot.get("date") or "unknown")
    else:
        advisory_version = "unavailable"
    return {
        "parserVersion": "cg26-parser/0.1.0",
        "rulesVersion": "cg26-rules/0.1.0",
        "advisoryVersion": advisory_version,
        "configVersion": "cg26-config/1",
        "hmacKeyVersion": os.environ.get("CG26_HMAC_KEY_VERSION") or DEMO_HMAC_KEY_VERSION,
        "parserHash": _hash_files(PARSER_FILES),
        "rulesHash": _hash_files(RULES_FILES),
        "advisoryHash": _sha256(database_bytes) if database_bytes is not None else _sha256(b""),
        "scanConfigHash": _sha256(_compact(config).encode("utf-8")),
        "domainPolicyRevisionId": _POLICY_ID,
        "domainPolicyVersion": _POLICY_VERSION,
        "domainPolicyHash": _POLICY_HASH,
    }


def _policy_input(identity: dict[str, str]) -> dict[str, Any]:
    return {
        "workspaceId": identity["workspaceId"],
        "id": _POLICY_ID,
        "version": _POLICY_VERSION,
        "hash": _POLICY_HASH,
        "sourceRef": "analyzers/cg26/policy.py#OBJECT_ACCESS_POLICY",
        "approvalStatus": "assumption",
        "approvedBy": None,
        "approvedAt": None,
        "resources": [
            {
                "kind": "ownership",
                "resource": "repair-ticket",
                "ownerField": "customer_id",
                "trustedIdentity": "Principal.id",
                "roleExceptions": [],
            }
        ],
    }


# ------------------------------------------------------------------ symbols


def _property_at(project: Project, path: str, line: int, name: str | None = None):
    best = None
    for prop in project.properties:
        if prop.file != path:
            continue
        if not (prop.line <= line <= prop.end_line):
            continue
        if name and prop.name != name:
            continue
        if best is None or prop.line >= best.line:
            best = prop
    return best


def _function_at(project: Project, path: str, line: int):
    best = None
    for function in project.functions:
        if function.file != path:
            continue
        if function.line <= line <= function.end_line:
            if best is None or function.line >= best.line:
                best = function
    return best


def _route_id(finding: Finding) -> str | None:
    route = finding.evidence.get("route")
    if isinstance(route, str):
        return route
    if isinstance(route, dict):
        return route.get("id")
    return None


def _symbol_for(finding: Finding, project: Project) -> str:
    if finding.category == "SCA":
        return f"{finding.group}:{finding.artifact}"
    if finding.category == "SECRET":
        name = finding.evidence.get("name") or finding.evidence.get("property")
        declaration = finding.evidence.get("declaration")
        anchor_line = finding.line or 1
        if isinstance(declaration, dict) and declaration.get("line"):
            anchor_line = int(declaration["line"])
        prop = _property_at(project, finding.path, anchor_line, name if isinstance(name, str) else None)
        if prop is not None:
            return f"{prop.owner}.{prop.name}" if prop.owner else prop.name
        return str(name) if name else f"{finding.path}:{anchor_line}"
    function = _function_at(project, finding.path, finding.line or 1)
    if function is not None:
        return f"{function.owner}.{function.name}" if function.owner else function.name
    route = _route_id(finding)
    return route or f"{finding.path}:{finding.line}"


def _normalized_context(text: str, secret_values: Iterable[str], mask_literals: bool) -> str:
    cleaned = _BLOCK_COMMENT.sub(" ", text)
    cleaned = _LINE_COMMENT.sub(" ", cleaned)
    for value in secret_values:
        if value:
            cleaned = cleaned.replace(value, "<secret>")
    if mask_literals:
        cleaned = _STRING_LITERAL.sub('"<literal>"', cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _secret_hmac(key: bytes, workspace: str, family: str, value: str) -> str:
    payload = _compact(["secret/v1", workspace, family, value]).encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def _fingerprint(payload: dict[str, Any]) -> str:
    anchor = payload["anchor"]
    return _sha256(
        _compact(
            [
                "fp/v1",
                payload["category"],
                payload["ruleId"],
                anchor["normalizedPath"],
                anchor["symbolKey"],
                anchor["normalizedContextHash"],
                anchor["role"],
                anchor["secretHmac"],
            ]
        ).encode("utf-8")
    )


# ------------------------------------------------------------------ findings


def _severity(value: str | None) -> str:
    lowered = (value or "").lower()
    return lowered if lowered in {"info", "low", "medium", "high", "critical"} else "medium"


def _node_ids_for_finding(finding: Finding, nodes: list[dict[str, Any]]) -> list[str]:
    if finding.category == "SCA":
        return []
    first = finding.line or 1
    last = finding.end_line or first
    found: list[str] = []
    route = _route_id(finding)
    sink_line = None
    sink = finding.evidence.get("sink")
    if isinstance(sink, dict):
        sink_line = sink.get("line")
    if sink_line is None:
        sql = finding.evidence.get("sql")
        if isinstance(sql, dict):
            sink_line = sql.get("line")
    for node in nodes:
        if node.get("path") != finding.path:
            continue
        line = node.get("line")
        if line is None:
            continue
        end = node.get("endLine") or line
        if node["kind"] == "route":
            if route and node.get("label") == route:
                found.append(node["id"])
            continue
        if node["kind"] not in {"handler", "function", "sink"}:
            continue
        if line <= last and first <= end:
            found.append(node["id"])
        elif node["kind"] == "sink" and sink_line and line == sink_line:
            found.append(node["id"])
    return found


def _related_refs(finding: Finding, book: EvidenceBook, primary: str | None) -> list[str]:
    refs: list[str] = []
    for location in finding.related:
        if isinstance(location, dict):
            path, line, end_line = location.get("path"), location.get("line"), location.get("endLine")
        else:
            path, line, end_line = location.path, location.line, location.end_line
        ref = book.ref(path, line, end_line)
        if ref and ref != primary:
            refs.append(ref)
    for key in ("declaration", "fallback", "sink", "reader", "sql"):
        value = finding.evidence.get(key)
        if not isinstance(value, dict) or "line" not in value:
            continue
        ref = book.ref(value.get("path") or finding.path, value["line"], value.get("endLine"))
        if ref and ref != primary and ref not in refs:
            refs.append(ref)
    for part in finding.evidence.get("parts") or []:
        if not isinstance(part, dict) or "line" not in part:
            continue
        ref = book.ref(part.get("path") or finding.path, part["line"], part.get("endLine"))
        if ref and ref != primary and ref not in refs:
            refs.append(ref)
    return refs


def _trace_refs(finding: Finding, book: EvidenceBook) -> list[str]:
    refs: list[str] = []
    for source in finding.evidence.get("source") or []:
        if not isinstance(source, dict) or "line" not in source:
            continue
        ref = book.ref(source.get("path") or finding.path, source["line"], source.get("endLine"))
        if ref and ref not in refs:
            refs.append(ref)
    sink = finding.evidence.get("sink")
    if isinstance(sink, dict) and "line" in sink:
        ref = book.ref(finding.path, sink["line"], sink.get("endLine"))
        if ref and ref not in refs:
            refs.append(ref)
    return refs


def _finding_payload(
    finding: Finding,
    book: EvidenceBook,
    project: Project,
    scope: dict[str, str],
    secret_key: bytes,
    nodes: list[dict[str, Any]],
    node_index: dict[str, str],
) -> tuple[dict[str, Any] | None, str | None]:
    primary = book.ref(finding.path, finding.line, finding.end_line, mask_literals=finding.category == "SECRET")
    location = book.location(finding.path, finding.line, finding.end_line)
    if primary is None or location is None:
        return None, f"Нет UTF-8 координат для {finding.path}:{finding.line}"

    class_code = finding.class_code or ""
    role = _ROLE_BY_CLASS.get(class_code, "declaration")
    symbol = _symbol_for(finding, project)
    secret_value = finding.secret_value if finding.category == "SECRET" else None
    secret_hmac = (
        _secret_hmac(secret_key, scope["workspaceId"], class_code or "secret", secret_value)
        if finding.category == "SECRET" and secret_value
        else None
    )
    context = _normalized_context(
        book.raw_snippet(finding.path, finding.line, finding.end_line),
        [secret_value] if secret_value else [],
        mask_literals=finding.category == "SECRET",
    )
    anchor = {
        "normalizedPath": finding.path,
        "symbolKey": symbol,
        "normalizedContextHash": _sha256(context.encode("utf-8")),
        "role": role,
        "secretHmac": secret_hmac,
    }
    related = _related_refs(finding, book, primary)
    trace = [ref for ref in _trace_refs(finding, book) if ref != primary and ref not in related]
    payload: dict[str, Any] = {
        **scope,
        "id": _uuid(f"finding:{finding.category}:{finding.rule_id}:{finding.path}:{finding.line}:{class_code}:{finding.advisory_id or ''}"),
        "fingerprintVersion": "fp/v1",
        "category": finding.category,
        "ruleId": finding.rule_id,
        "ruleVersion": finding.rule_version,
        "ruleFamily": class_code or finding.rule_id,
        "severity": _severity(finding.severity),
        "severityBasis": finding.severity_basis or finding.description,
        "confidence": "high" if finding.confidence == "confirmed" else "medium",
        "confidenceBasis": finding.confidence_basis or finding.description,
        "status": "confirmed" if finding.confidence == "confirmed" else "potential",
        "primaryLocation": location,
        "primaryEvidenceId": primary,
        "relatedEvidenceIds": related,
        "traceEvidenceIds": trace,
        "graphNodeIds": [node_index[nid] for nid in _node_ids_for_finding(finding, nodes) if nid in node_index],
        "anchor": anchor,
        "advisoryIds": [finding.advisory_id] if finding.advisory_id else [],
        "coverageWarnings": [],
    }
    payload["fingerprint"] = _fingerprint(payload)
    return payload, None


# -------------------------------------------------------------------- coverage


def _family_files(family: str, sources: dict[str, SourceFile]) -> list[str]:
    suffixes = FAMILY_SUFFIXES[family]
    if suffixes is None:
        return sorted(sources)
    return sorted(path for path in sources if _suffix(path) in suffixes)


def _coverage(
    family: str,
    files: list[str],
    rules: list[Any],
    unresolved: list[str],
    parser_errors: list[str],
) -> dict[str, Any]:
    scopes = [
        {
            "path": path,
            "ruleId": rule.rule_id,
            "category": family,
            "status": "checked",
            "reason": "Файл прочитан, правило применено к его содержимому.",
        }
        for path in files
        for rule in rules
    ]
    status = "complete" if not unresolved and not parser_errors else "partial"
    if not files:
        status = "unavailable"
    return {
        "status": status,
        "filesTotal": len(files),
        "filesVisited": len(files),
        "rulesApplied": [rule.rule_id for rule in rules],
        "scopes": scopes,
        "skippedScopes": [],
        "parserErrors": parser_errors,
        "unresolvedSymbols": unresolved,
        "limitsReached": [],
    }


# ---------------------------------------------------------------------- graph


_NODE_KINDS = {
    "route": "route",
    "authorization-rule": "guard",
    "handler": "function",
    "function": "function",
    "parameter-source": "source",
    "sink": "sink",
    "cors-registration": "config",
}


def _node_kind(node: dict[str, Any]) -> str:
    kind = _NODE_KINDS.get(node["kind"], "function")
    if node["kind"] == "sink" and (node.get("attributes") or {}).get("sinkType") == "cors":
        return "config"
    return kind


def _node_symbol(node: dict[str, Any]) -> str:
    attributes = node.get("attributes") or {}
    if node["kind"] == "route":
        return str(node.get("label"))
    if node["kind"] == "authorization-rule":
        return f"authorizeHttpRequests[{attributes.get('order')}]"
    if node["kind"] == "parameter-source":
        return f"{attributes.get('handler')}.{node.get('label')}"
    if node["kind"] == "sink":
        return f"{attributes.get('sinkType')}:{node.get('path')}:{node.get('line')}"
    return f"{node.get('label')}@{node.get('path')}"


def _map_edge_kind(edge: dict[str, Any], nodes_by_id: dict[str, dict[str, Any]]) -> str | None:
    kind = edge["kind"]
    if kind == "handled-by":
        return "routes_to"
    if kind == "authorizes":
        effect = (nodes_by_id.get(edge["from"], {}).get("attributes") or {}).get("effect")
        if effect == "permitAll":
            return "permits"
        if effect == "denyAll":
            return "guards"
        return "requires"
    if kind == "reads-source":
        return "reads"
    if kind == "calls":
        return "calls"
    if kind == "contains-sink":
        sink = nodes_by_id.get(edge["to"], {})
        sink_type = (sink.get("attributes") or {}).get("sinkType")
        return "calls" if sink_type in {"sql", "http-header"} else None
    return None


def _code_facts(
    graph: dict[str, Any],
    book: EvidenceBook,
    identity: dict[str, str],
    scope: dict[str, str],
    project: Project,
    findings: list[Finding],
    files: list[str],
) -> tuple[dict[str, Any], dict[str, str], list[dict[str, Any]]]:
    raw_nodes = graph.get("nodes") or []
    nodes: list[dict[str, Any]] = []
    node_index: dict[str, str] = {}
    for node in raw_nodes:
        path = node.get("path")
        line = node.get("line")
        if not path or line is None:
            continue
        ref = book.ref(path, line, node.get("endLine"))
        if ref is None:
            continue
        identifier = _uuid("node:" + node["id"])
        node_index[node["id"]] = identifier
        nodes.append(
            {
                **scope,
                "id": identifier,
                "kind": _node_kind(node),
                "symbolKey": _node_symbol(node),
                "label": node.get("label") or node["id"],
                "evidenceRefs": [ref],
                "producerVersion": PRODUCER_VERSION,
            }
        )

    nodes_by_id = {node["id"]: node for node in raw_nodes}
    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def add_edge(
        source_id: str,
        target_id: str,
        kind: str,
        path: str | None,
        line: int | None,
        end_line: int | None,
        reason: str,
        resolution: str = "resolved",
    ) -> None:
        if source_id not in node_index or target_id not in node_index:
            return
        key = (node_index[source_id], node_index[target_id], kind)
        if key in seen:
            return
        ref = book.ref(path, line, end_line) if path and line else None
        if ref is None:
            return
        seen.add(key)
        edges.append(
            {
                **scope,
                "id": _uuid(f"edge:{node_index[source_id]}:{node_index[target_id]}:{kind}"),
                "fromId": node_index[source_id],
                "toId": node_index[target_id],
                "kind": kind,
                "evidenceRefs": [ref],
                "resolution": resolution,
                "resolutionReason": reason,
                "producerVersion": PRODUCER_VERSION,
                "basis": "source",
                "policyRef": None,
            }
        )

    for edge in graph.get("edges") or []:
        kind = _map_edge_kind(edge, nodes_by_id)
        if kind is None:
            continue
        evidence = edge.get("evidence") or {}
        add_edge(
            edge["from"],
            edge["to"],
            kind,
            evidence.get("path"),
            evidence.get("line"),
            evidence.get("endLine"),
            edge.get("basis") or "Объявление найдено в исходниках.",
            "resolved" if edge.get("status") == "established" else "unknown",
        )

    # Data flow from a request parameter to the SQL sink, only for findings
    # whose evidence names both ends.
    for finding in findings:
        if finding.class_code not in {"sql-injection", "object-authorization"}:
            continue
        first = finding.line or 1
        last = finding.end_line or first
        sink_node = None
        for node in raw_nodes:
            if node["kind"] != "sink" or node.get("path") != finding.path:
                continue
            if (node.get("attributes") or {}).get("sinkType") != "sql":
                continue
            line = node.get("line") or 0
            if first <= line <= last:
                sink_node = node
        if sink_node is None:
            continue
        sources: list[dict[str, Any]] = []
        if finding.class_code == "sql-injection":
            for source in finding.evidence.get("source") or []:
                if not isinstance(source, dict):
                    continue
                for node in raw_nodes:
                    if node["kind"] != "parameter-source" or node.get("path") != finding.path:
                        continue
                    if node.get("line") == source.get("line") and node.get("label") == source.get("name"):
                        sources.append(node)
        else:
            variables = set(finding.evidence.get("pathVariables") or [])
            route = _route_id(finding)
            handler = None
            for node in raw_nodes:
                if node["kind"] == "route" and node.get("label") == route:
                    handler = (node.get("attributes") or {}).get("handler")
            for node in raw_nodes:
                if node["kind"] != "parameter-source" or node.get("label") not in variables:
                    continue
                if handler and (node.get("attributes") or {}).get("handler") != handler:
                    continue
                sources.append(node)
        for source in sources:
            add_edge(
                source["id"],
                sink_node["id"],
                "flows_to",
                sink_node.get("path"),
                sink_node.get("line"),
                sink_node.get("endLine"),
                "Параметр запроса интерполируется в SQL-литерал того же тела функции.",
            )

    unresolved = [entry.get("reason", "unknown") for entry in graph.get("unresolved") or []]
    diagnostics = [
        {
            **scope,
            "id": _uuid(f"diagnostic:graph:{index}:{reason}"),
            "category": "SAST",
            "code": "graph.unresolved",
            "message": reason,
            "evidenceId": None,
        }
        for index, reason in enumerate(unresolved)
    ]
    coverage = _coverage("SAST", files, [_GRAPH_RULE], unresolved, [])
    referenced = {
        ref
        for node in nodes
        for ref in node["evidenceRefs"]
    } | {
        ref
        for edge in edges
        for ref in edge["evidenceRefs"]
    }
    return (
        {
            "schemaVersion": SCHEMA_VERSION,
            "identity": identity,
            "signature": {},  # filled by the caller
            "nodes": nodes,
            "edges": edges,
            "evidence": book.subset(referenced),
            "coverage": coverage,
            "policyInputs": [_policy_input(identity)],
            "diagnostics": diagnostics,
        },
        node_index,
        diagnostics,
    )


class _GraphRule:
    rule_id = "CG26-GRAPH"


_GRAPH_RULE = _GraphRule()


# --------------------------------------------------------------------- public


def build_bundle(
    root: str | Path,
    project: Project,
    result: ScanResult,
    database: str | Path | None = None,
    *,
    workspace_id: str | None = None,
    run_id: str | None = None,
    snapshot_id: str | None = None,
    workflow_revision_id: str | None = None,
) -> dict[str, Any]:
    """Return an ``AnalysisBundle`` for one completed scan."""

    sources = load_sources(project)
    identity = _identity(sources, workspace_id, run_id, snapshot_id, workflow_revision_id)
    scope = {
        "workspaceId": identity["workspaceId"],
        "runId": identity["runId"],
        "snapshotId": identity["snapshotId"],
    }

    database_path = None
    database_bytes = None
    database_snapshot = None
    try:
        from .sca import locate_database

        candidate = locate_database(database, project)
        if candidate is not None:
            database_path = candidate
            database_bytes = Path(candidate).read_bytes()
            database_snapshot = json.loads(database_bytes.decode("utf-8")).get("snapshot")
    except (OSError, ValueError, json.JSONDecodeError):
        database_path = None

    signature = _signature(database_bytes, database_snapshot)
    secret_key = (os.environ.get("CG26_HMAC_KEY") or "").encode("utf-8") or DEMO_HMAC_KEY

    secret_values = [finding.secret_value for finding in result.findings if finding.secret_value]
    mask_paths = {finding.path for finding in result.findings if finding.category == "SECRET"}
    book = EvidenceBook(scope, sources, secret_values, mask_paths)

    nodes = (result.graph or {}).get("nodes") or []
    node_index = {node["id"]: _uuid("node:" + node["id"]) for node in nodes if node.get("path")}

    findings_by_category: dict[str, list[dict[str, Any]]] = {name: [] for name in CATEGORIES}
    category_diagnostics: dict[str, list[dict[str, Any]]] = {name: [] for name in CATEGORIES}
    used_evidence: dict[str, set[str]] = {name: set() for name in CATEGORIES}
    parser_diagnostics: list[dict[str, Any]] = []

    for error in project.parser_errors:
        message = f"{error.get('path')}: {error.get('message')}"
        parser_diagnostics.append(
            {
                **scope,
                "id": _uuid(f"diagnostic:parser:{message}"),
                "category": "SAST",
                "code": "parser.error",
                "message": message,
                "evidenceId": None,
            }
        )

    for index, diagnostic in enumerate(result.diagnostics):
        category = getattr(diagnostic, "category", "") or ("SCA" if diagnostic.code.startswith("sca.") else "SAST")
        if category not in CATEGORIES:
            category = "SAST"
        evidence_id = book.ref(diagnostic.path, diagnostic.line) if diagnostic.path else None
        if evidence_id:
            used_evidence[category].add(evidence_id)
        category_diagnostics[category].append(
            {
                **scope,
                "id": _uuid(f"diagnostic:{category}:{index}:{diagnostic.code}:{diagnostic.path or ''}:{diagnostic.line or 0}"),
                "category": category,
                "code": diagnostic.code,
                "message": diagnostic.message,
                "evidenceId": evidence_id,
            }
        )

    for finding in result.findings:
        payload, problem = _finding_payload(finding, book, project, scope, secret_key, nodes, node_index)
        if payload is None:
            category = finding.category if finding.category in CATEGORIES else "SAST"
            category_diagnostics[category].append(
                {
                    **scope,
                    "id": _uuid(f"diagnostic:contract:{finding.path}:{finding.line}:{finding.class_code}"),
                    "category": category,
                    "code": "contract.unsupported-location",
                    "message": problem or "Находка без координат в контракте.",
                    "evidenceId": None,
                }
            )
            continue
        findings_by_category[finding.category].append(payload)
        used_evidence[finding.category].update(
            [payload["primaryEvidenceId"], *payload["relatedEvidenceIds"], *payload["traceEvidenceIds"]]
        )

    rules_by_category: dict[str, list[Any]] = {name: [] for name in CATEGORIES}
    for rule in result.rules:
        rules_by_category.setdefault(rule.category, []).append(rule)

    code_facts, _, graph_diagnostics = _code_facts(
        result.graph or {},
        book,
        identity,
        scope,
        project,
        result.findings,
        _family_files("SAST", sources),
    )
    code_facts["signature"] = signature
    code_facts["diagnostics"].extend(parser_diagnostics)
    category_diagnostics["SAST"].extend(parser_diagnostics)
    parser_ids = [item["id"] for item in parser_diagnostics]

    scan_results: list[dict[str, Any]] = []
    for family in CATEGORIES:
        files = _family_files(family, sources)
        rules = rules_by_category.get(family) or []
        unresolved: list[str] = []
        if family == "SCA":
            unresolved = [
                item["message"] for item in category_diagnostics["SCA"] if item["code"].startswith("sca.")
            ]
        if family == "SAST":
            unresolved = [entry.get("reason", "unknown") for entry in (result.graph or {}).get("unresolved") or []]
        coverage = _coverage(family, files, rules, unresolved, parser_ids if family == "SAST" else [])
        scan_results.append(
            {
                "schemaVersion": SCHEMA_VERSION,
                "identity": identity,
                "signature": signature,
                "category": family,
                "status": "complete" if coverage["status"] == "complete" else "partial",
                "producerVersion": PRODUCER_VERSION,
                "durationMs": int(result.durations_ms.get(family, 0)),
                "findings": findings_by_category[family],
                "evidence": book.subset(used_evidence[family]),
                "diagnostics": category_diagnostics[family],
                "coverage": coverage,
            }
        )

    return {
        "schemaVersion": SCHEMA_VERSION,
        "identity": identity,
        "signature": signature,
        "scanResults": scan_results,
        "codeFacts": code_facts,
    }
