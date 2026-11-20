"""Канонические DTO карты: backend findings/facts -> contracts.schema.json.

Здесь нет «пересказа» находок: id, fingerprint, evidence и координаты выводятся
детерминированно. `fingerprint` считается по рецепту
`docs/data-contracts/validate-bundle.mjs`, чтобы карта проходила ту же проверку,
что и illustration, но уже как реальный scanner output.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.app.codefacts.extractor import (
    EXTRACTOR_VERSION,
    RULE_DEFINITIONS,
    RULES_VERSION,
    ExtractionResult,
)
from backend.app.codefacts.identity import (
    bundle_hash,
    canonical_json,
    diagnostic_uuid,
    edge_uuid,
    evidence_uuid,
    finding_uuid,
    node_uuid,
    revision_uuid,
    sha256_hex as _sha,
)
from backend.app.codefacts.source import (
    REDACTION_VERSION,
    SourceFile,
    TEXT_EXTENSIONS,
    context_hash,
    hmac_evidence,
    is_safe_source_path,
    make_evidence,
    make_location,
    mask_text,
)
from backend.app.contracts.schemas import Finding as BackendFinding

PRODUCER_VERSION = "cg-evidence/1.0.0"
ANALYZER_CATEGORY = {"secret": "SECRET", "sca": "SCA", "sast": "SAST", "codefacts": "SAST"}
CONFIDENCE_MAP = {"confirmed": "high", "potential": "medium", "inconclusive": "low"}
RULE_FAMILY = {
    "KOTLIN-SQL-INJECTION": "sql-injection",
    "KOTLIN-PATH-TRAVERSAL": "path-traversal",
    "KOTLIN-COMMAND-INJECTION": "command-injection",
    "KOTLIN-INSECURE-ENDPOINT": "route-authorization",
    "KOTLIN-IDOR": "object-authorization",
    "SCA-VULNERABLE-DEPENDENCY": "dependency",
    "CF-ROUTE-UNAUTH-PII": "route-authorization",
    "CF-SQL-PARAM-INTERPOLATION": "sql-injection",
    "CF-OWNER-CHECK-UNPROVEN": "object-authorization",
}


def _fingerprint(category: str, rule_id: str, anchor: Dict[str, Any]) -> str:
    payload = [
        "fp/v1", category, rule_id,
        anchor["normalizedPath"], anchor["symbolKey"],
        anchor["normalizedContextHash"], anchor["role"], anchor["secretHmac"],
    ]
    return _sha(canonical_json(payload).encode("utf-8"))


def _evidence_id(workspace: str, run_id: str, item: Dict[str, Any]) -> str:
    loc = item["location"]
    snippet_hash = _sha(mask_text(str(item.get("redactedSnippet", ""))).encode("utf-8"))
    return evidence_uuid(
        workspace, run_id, str(item.get("kind", "source")),
        loc["path"], int(loc["byteStart"]), int(loc["byteEnd"]), snippet_hash,
    )


class CanonicalMapBuilder:
    def __init__(self, *, workspace_id: str, run_id: str, snapshot_id: str,
                 artifact_hash: str, snapshot_manifest_hash: str,
                 workflow_revision_id: str, facts: ExtractionResult,
                 sources: Dict[str, SourceFile], snapshot_files: List[Dict[str, Any]],
                 backend_findings: List[BackendFinding],
                 scan_results: Optional[List[Any]] = None):
        self.workspace_id = workspace_id
        self.run_id = run_id
        self.snapshot_id = snapshot_id
        self.artifact_hash = artifact_hash
        self.snapshot_manifest_hash = snapshot_manifest_hash
        self.workflow_revision_id = revision_uuid(workflow_revision_id)
        self.facts = facts
        self.sources = sources
        self.snapshot_files = snapshot_files
        self.backend_findings = backend_findings
        self.scan_results = list(scan_results or [])
        self.file_hash = {str(item["path"]): str(item["sha256"]) for item in snapshot_files}
        self.evidence: Dict[str, Dict[str, Any]] = {}
        self.evidence_by_location: Dict[Tuple[Any, ...], str] = {}
        self.diagnostics: List[Dict[str, Any]] = []
        self._diag_ordinal = 0
        self._publishable = True
        self._anchor_key = (
            os.getenv("CG_ANCHOR_HMAC_KEY", "").strip()
            or os.getenv("CG_SESSION_SECRET", "").strip()
        ).encode("utf-8") or None

    # --- public ----------------------------------------------------------
    def build(self) -> Dict[str, Any]:
        self._add_fact_evidence()
        self._add_analyzer_evidence()
        nodes = self._build_nodes()
        edges = self._build_edges(nodes)
        findings, links, finding_refs = self._build_findings(nodes)
        self._add_map_diagnostics()
        coverage, parser_errors = self._build_coverage()
        signature = self._build_signature()
        identity = {
            "workspaceId": self.workspace_id,
            "runId": self.run_id,
            "snapshotId": self.snapshot_id,
            "artifactHash": self.artifact_hash,
            "snapshotManifestHash": self.snapshot_manifest_hash,
            "workflowRevisionId": self.workflow_revision_id,
        }
        evidence_list = sorted(self.evidence.values(), key=lambda item: item["id"])
        map_dto: Dict[str, Any] = {
            "schemaVersion": "0.1",
            "identity": identity,
            "signature": signature,
            "nodes": nodes,
            "edges": edges,
            "evidence": evidence_list,
            "coverage": coverage,
            "policyInputs": [],
            "diagnostics": self.diagnostics,
            "findingRefs": finding_refs,
            "findingNodeLinks": links,
        }
        map_dto["bundleHash"] = bundle_hash(map_dto)
        self._built_nodes = nodes
        self._built_edges = edges
        result = {"identity": identity, "map": map_dto, "findings": findings,
                  "redactionVersion": REDACTION_VERSION}
        analysis_bundle = self._build_analysis_bundle(identity, signature, findings, coverage)
        if analysis_bundle is not None:
            result["analysisBundle"] = analysis_bundle
        return result

    # --- evidence --------------------------------------------------------
    def _register_evidence(self, item: Dict[str, Any]) -> str:
        loc = item["location"]
        if loc["path"] not in self.file_hash:
            return ""
        if not is_safe_source_path(loc["path"]):
            return ""
        kind = str(item.get("kind", "source"))
        snippet = mask_text(str(item.get("redactedSnippet", "")))
        source_hash = self.file_hash[loc["path"]]
        payload = {
            "workspaceId": self.workspace_id,
            "runId": self.run_id,
            "snapshotId": self.snapshot_id,
            "id": _evidence_id(self.workspace_id, self.run_id, {**item, "redactedSnippet": snippet}),
            "location": {
                "path": loc["path"],
                "line": int(loc["line"]),
                "column": int(loc["column"]),
                "endLine": int(loc["endLine"]),
                "endColumn": int(loc["endColumn"]),
                "byteStart": int(loc["byteStart"]),
                "byteEnd": int(loc["byteEnd"]),
            },
            "kind": kind if kind in {"source", "ast", "dsl", "sql", "manifest"} else "source",
            "redactedSnippet": snippet,
            "sourceHash": source_hash,
            "producerVersion": str(item.get("producerVersion") or PRODUCER_VERSION),
        }
        self.evidence[payload["id"]] = payload
        key = (loc["path"], payload["location"]["byteStart"], payload["location"]["byteEnd"], payload["kind"])
        self.evidence_by_location.setdefault(key, payload["id"])
        return payload["id"]

    def _add_fact_evidence(self) -> None:
        for node in self.facts.nodes.values():
            for item in node.evidence:
                self._register_evidence(item)
        for edge in self.facts.edges:
            for item in edge.evidence:
                self._register_evidence(item)

    def _snippet_for_location(self, finding: BackendFinding, loc: Any) -> str:
        source = self.sources.get(loc.path)
        if source is None:
            return mask_text(str(finding.redactedEvidence))
        try:
            raw = make_evidence(
                source, int(loc.startLine), int(loc.endLine),
                kind="source", start_column=int(loc.startColumn),
                end_column=int(loc.endColumn),
            )
            return raw["redactedSnippet"]
        except (ValueError, IndexError):
            return mask_text(str(finding.redactedEvidence))

    def _location_for_finding(self, finding: BackendFinding, loc: Any) -> Optional[Dict[str, Any]]:
        source = self.sources.get(loc.path)
        if source is None:
            return None
        try:
            return make_location(
                source, int(loc.startLine), int(loc.endLine),
                int(loc.startColumn), int(loc.endColumn),
            )
        except (ValueError, IndexError):
            try:
                return make_location(source, int(loc.startLine), int(loc.startLine))
            except (ValueError, IndexError):
                return None

    def _evidence_for_finding_location(self, finding: BackendFinding, loc: Any,
                                      kind: str = "source") -> Optional[str]:
        contract_loc = self._location_for_finding(finding, loc)
        if contract_loc is None:
            return None
        key = (contract_loc["path"], contract_loc["byteStart"], contract_loc["byteEnd"], kind)
        if key in self.evidence_by_location:
            return self.evidence_by_location[key]
        item = {
            "kind": kind,
            "location": contract_loc,
            "redactedSnippet": self._snippet_for_location(finding, loc),
            "producerVersion": f"{finding.analyzer}/{finding.ruleVersion}",
        }
        return self._register_evidence(item) or None

    def _add_analyzer_evidence(self) -> None:
        for finding in self.backend_findings:
            self._evidence_for_finding_location(finding, finding.primaryLocation, kind="source")
            for loc in list(finding.relatedLocations):
                self._evidence_for_finding_location(finding, loc, kind="source")
            for step in finding.trace:
                if step.location is not None:
                    self._evidence_for_finding_location(finding, step.location, kind="source")

    # --- graph -----------------------------------------------------------
    def _build_nodes(self) -> List[Dict[str, Any]]:
        nodes: List[Dict[str, Any]] = []
        for symbol, node in sorted(self.facts.nodes.items()):
            refs: List[str] = []
            for item in node.evidence:
                evidence_id = self._register_evidence(item)
                if evidence_id and evidence_id not in refs:
                    refs.append(evidence_id)
            if node.kind != "actor" and not refs:
                self._diag("CF-NODE-WITHOUT-EVIDENCE", f"Узел без evidence: {symbol}")
                continue
            nodes.append({
                "workspaceId": self.workspace_id,
                "runId": self.run_id,
                "snapshotId": self.snapshot_id,
                "id": node_uuid(self.workspace_id, self.run_id, symbol),
                "kind": node.kind,
                "symbolKey": symbol,
                "label": node.label,
                "evidenceRefs": refs,
                "producerVersion": EXTRACTOR_VERSION,
            })
        return nodes

    def _build_edges(self, nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        node_ids = {node["symbolKey"]: node["id"] for node in nodes}
        edges: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        for edge in self.facts.edges:
            from_id = node_ids.get(edge.from_key)
            to_id = node_ids.get(edge.to_key)
            if not from_id or not to_id:
                self._diag("CF-EDGE-OUTSIDE-GRAPH", f"Ребро {edge.from_key} -> {edge.to_key} без узлов")
                continue
            refs: List[str] = []
            for item in edge.evidence:
                evidence_id = self._register_evidence(item)
                if evidence_id and evidence_id not in refs:
                    refs.append(evidence_id)
            if edge.basis == "source" and not refs:
                self._diag("CF-EDGE-WITHOUT-EVIDENCE", f"Ребро без evidence: {edge.from_key} -> {edge.to_key}")
                continue
            edge_id = edge_uuid(self.workspace_id, self.run_id, from_id, to_id, edge.kind)
            if edge_id in seen:
                continue
            seen.add(edge_id)
            edges.append({
                "workspaceId": self.workspace_id,
                "runId": self.run_id,
                "snapshotId": self.snapshot_id,
                "id": edge_id,
                "fromId": from_id,
                "toId": to_id,
                "kind": edge.kind,
                "evidenceRefs": refs,
                "resolution": edge.resolution,
                "resolutionReason": edge.reason or "Статическое наблюдение по исходникам.",
                "producerVersion": EXTRACTOR_VERSION,
                "basis": edge.basis,
                "policyRef": edge.policy_ref,
            })
        return edges

    # --- findings --------------------------------------------------------
    def _graph_nodes_for_finding(self, finding: BackendFinding, node_ids: Dict[str, str]) -> List[str]:
        symbols = self.facts.finding_symbols.get(finding.id, [])
        result = [node_ids[symbol] for symbol in symbols if symbol in node_ids]
        if result:
            return result
        # Fallback for analyzer findings: same file, matching function/route/sink nodes.
        path = finding.primaryLocation.path
        for symbol, node_id in node_ids.items():
            node = self.facts.nodes.get(symbol)
            if not node:
                continue
            if node.kind not in {"function", "route", "sink"}:
                continue
            if any(item["location"]["path"] == path for item in node.evidence):
                if node_id not in result:
                    result.append(node_id)
        return result

    def _build_findings(self, nodes: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
        node_ids = {node["symbolKey"]: node["id"] for node in nodes}
        findings: List[Dict[str, Any]] = []
        links: List[Dict[str, Any]] = []
        refs: List[str] = []
        seen_fingerprints: Set[str] = set()
        for finding in self.backend_findings:
            category = ANALYZER_CATEGORY.get(finding.analyzer)
            if category is None:
                continue
            primary_evidence = self._evidence_for_finding_location(
                finding, finding.primaryLocation, kind="source",
            )
            if not primary_evidence:
                self._diag("CF-FINDING-OUTSIDE-SNAPSHOT", f"Нет evidence для {finding.id}: {finding.primaryLocation.path}")
                continue
            evidence = self.evidence[primary_evidence]
            primary_location = dict(evidence["location"])
            snippet = evidence["redactedSnippet"]
            graph_nodes = self._graph_nodes_for_finding(finding, node_ids)
            symbol_key = self._symbol_key(finding, graph_nodes, nodes)
            role = self._role(finding)
            context = context_hash(
                category, finding.ruleId, primary_location["path"],
                primary_location["line"], snippet,
            )
            anchor = {
                "normalizedPath": primary_location["path"],
                "symbolKey": symbol_key,
                "normalizedContextHash": context,
                "role": role,
                "secretHmac": None,
            }
            if category == "SECRET":
                if self._anchor_key is None:
                    self._publishable = False
                    if not any(d["code"] == "CF-ANCHOR-HMAC-UNAVAILABLE" for d in self.diagnostics):
                        self._diag(
                            "CF-ANCHOR-HMAC-UNAVAILABLE",
                            "CG_ANCHOR_HMAC_KEY/CG_SESSION_SECRET не задан: SECRET findings "
                            "не выводятся в canonical map и bundle не публикуется.",
                        )
                    continue
                anchor["secretHmac"] = hmac_evidence(
                    self._anchor_key, "SECRET", finding.ruleId,
                    primary_location["path"], str(primary_location["line"]), context,
                )
            fingerprint = _fingerprint(category, finding.ruleId, anchor)
            if fingerprint in seen_fingerprints:
                continue
            seen_fingerprints.add(fingerprint)
            related_ids: List[str] = []
            for loc in finding.relatedLocations:
                evidence_id = self._evidence_for_finding_location(finding, loc, kind="source")
                if evidence_id and evidence_id not in related_ids:
                    related_ids.append(evidence_id)
            trace_ids: List[str] = []
            for step in finding.trace:
                if step.location is None:
                    continue
                evidence_id = self._evidence_for_finding_location(finding, step.location, kind="source")
                if evidence_id and evidence_id not in trace_ids:
                    trace_ids.append(evidence_id)
            finding_id = finding_uuid(self.workspace_id, self.run_id, fingerprint)
            item = {
                "workspaceId": self.workspace_id,
                "runId": self.run_id,
                "snapshotId": self.snapshot_id,
                "id": finding_id,
                "fingerprint": fingerprint,
                "fingerprintVersion": "fp/v1",
                "category": category,
                "ruleId": finding.ruleId,
                "ruleVersion": finding.ruleVersion,
                "ruleFamily": RULE_FAMILY.get(finding.ruleId, "static-analysis"),
                "severity": finding.severity.value,
                "severityBasis": finding.severityBasis,
                "confidence": CONFIDENCE_MAP.get(finding.confidence.value, "low"),
                "confidenceBasis": finding.confidenceBasis,
                "status": finding.status.value,
                "primaryLocation": primary_location,
                "primaryEvidenceId": primary_evidence,
                "relatedEvidenceIds": related_ids,
                "traceEvidenceIds": trace_ids,
                "graphNodeIds": graph_nodes,
                "anchor": anchor,
                "advisoryIds": list(finding.advisoryIds),
                "coverageWarnings": list(finding.coverageWarnings),
            }
            findings.append(item)
            refs.append(finding_id)
            for node_id in graph_nodes:
                links.append({"findingId": finding_id, "nodeId": node_id})
        return findings, links, refs

    def _symbol_key(self, finding: BackendFinding, graph_nodes: List[str], nodes: List[Dict[str, Any]]) -> str:
        by_id = {node["id"]: node for node in nodes}
        for node_id in graph_nodes:
            node = by_id.get(node_id)
            if node and node["kind"] == "function":
                return node["symbolKey"]
        return f"{finding.primaryLocation.path}#L{finding.primaryLocation.startLine}:{finding.ruleId}"

    def _role(self, finding: BackendFinding) -> str:
        mapping = {
            "KOTLIN-SQL-INJECTION": "sql_sink",
            "KOTLIN-PATH-TRAVERSAL": "file_sink",
            "KOTLIN-COMMAND-INJECTION": "process_sink",
            "KOTLIN-INSECURE-ENDPOINT": "route_authorization",
            "KOTLIN-IDOR": "object_authorization",
            "SCA-VULNERABLE-DEPENDENCY": "dependency",
            "CF-ROUTE-UNAUTH-PII": "route_authorization",
            "CF-SQL-PARAM-INTERPOLATION": "sql_sink",
            "CF-OWNER-CHECK-UNPROVEN": "object_authorization",
        }
        if finding.analyzer == "secret":
            return "secret_literal"
        return mapping.get(finding.ruleId, "static_fact")

    # --- diagnostics / coverage -----------------------------------------
    def _diag(self, code: str, message: str, evidence_id: Optional[str] = None) -> None:
        self._diag_ordinal += 1
        self.diagnostics.append({
            "workspaceId": self.workspace_id,
            "runId": self.run_id,
            "snapshotId": self.snapshot_id,
            "id": diagnostic_uuid(self.workspace_id, self.run_id, code, self._diag_ordinal),
            "category": "SAST",
            "code": code,
            "message": message,
            "evidenceId": evidence_id,
        })

    def _add_map_diagnostics(self) -> None:
        for diag in self.facts.diagnostics:
            evidence_id = None
            if diag.get("evidence"):
                evidence_id = self._register_evidence(diag["evidence"]) or None
            self._diag(str(diag["code"]), str(diag["message"]), evidence_id)
        if self.facts.unresolved_symbols:
            self._diag("CF-UNRESOLVED-CALLS", f"Неразрешённые вызовы: {len(self.facts.unresolved_symbols)}")

    def _build_coverage(self) -> Tuple[Dict[str, Any], List[str]]:
        files_total = len(self.snapshot_files)
        visited = sorted(set(self.facts.files_read))
        scopes: List[Dict[str, Any]] = []
        for path in visited:
            if not is_safe_source_path(path):
                continue
            decode_error = self.sources.get(path).decode_error if path in self.sources else None
            scopes.append({
                "path": path,
                "ruleId": "CF-SPRING-STATIC",
                "category": "SAST",
                "status": "partial" if decode_error else "checked",
                "reason": "Файл прочитан статически; runtime-поведение не проверялось." if not decode_error
                          else f"Частичное чтение: {decode_error}",
            })
        parser_errors = [
            diag["id"] for diag in self.diagnostics
            if diag["code"] in {"CF-DECODE-ERROR", "CF-NODE-WITHOUT-EVIDENCE"}
        ]
        skipped: List[str] = [
            "runtime-configuration",
            "runtime-guards",
            "binaries-and-archives",
        ]
        unresolved = list(dict.fromkeys(self.facts.unresolved_symbols))
        limits = list(self.facts.limits)
        complete = (
            files_total > 0
            and len(visited) == files_total
            and not parser_errors
            and not unresolved
            and not limits
            and all(scope["status"] == "checked" for scope in scopes)
        )
        coverage = {
            "status": "complete" if complete else "partial",
            "filesTotal": files_total,
            "filesVisited": min(len(visited), files_total),
            "rulesApplied": [rule["id"] for rule in RULE_DEFINITIONS],
            "scopes": scopes,
            "skippedScopes": skipped,
            "parserErrors": parser_errors,
            "unresolvedSymbols": unresolved,
            "limitsReached": limits,
        }
        return coverage, parser_errors

    # --- AnalysisBundle --------------------------------------------------
    def _scan_diagnostics(self, scan: Any, category: str) -> Tuple[List[Dict[str, Any]], List[str]]:
        diagnostics: List[Dict[str, Any]] = []
        parser_errors: List[str] = []
        for idx, diag in enumerate(getattr(scan, "diagnostics", []) or [], start=1):
            details = getattr(diag, "details", {}) or {}
            code = str(details.get("code") or getattr(diag, "source", "") or "SCANNER-DIAGNOSTIC")
            code = re.sub(r"[^A-Za-z0-9_.-]+", "-", code).strip("-") or "SCANNER-DIAGNOSTIC"
            diag_id = diagnostic_uuid(
                self.workspace_id, self.run_id,
                f"scan:{getattr(scan, 'analyzer', 'scanner')}:{code}", idx,
            )
            diagnostics.append({
                "workspaceId": self.workspace_id,
                "runId": self.run_id,
                "snapshotId": self.snapshot_id,
                "id": diag_id,
                "category": category,
                "code": code,
                "message": str(getattr(diag, "message", code)),
                "evidenceId": None,
            })
            if getattr(diag, "level", "") == "error":
                parser_errors.append(diag_id)
        return diagnostics, parser_errors

    def _build_analysis_bundle(self, identity: Dict[str, Any], signature: Dict[str, Any],
                               findings: List[Dict[str, Any]],
                               coverage: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.scan_results or not self._publishable:
            return None
        by_category: Dict[str, Any] = {}
        for scan in self.scan_results:
            category = ANALYZER_CATEGORY.get(str(getattr(scan, "analyzer", "")))
            if category and category not in by_category:
                by_category[category] = scan
        if set(by_category) != {"SECRET", "SCA", "SAST"}:
            return None
        total = len(self.snapshot_files)
        scan_results: List[Dict[str, Any]] = []
        for category in ("SECRET", "SCA", "SAST"):
            scan = by_category[category]
            category_findings = [item for item in findings if item["category"] == category]
            refs: List[str] = []
            for finding in category_findings:
                for evidence_id in [finding["primaryEvidenceId"], *finding["relatedEvidenceIds"],
                                    *finding["traceEvidenceIds"]]:
                    if evidence_id not in refs:
                        refs.append(evidence_id)
            evidence = [self.evidence[evidence_id] for evidence_id in refs if evidence_id in self.evidence]
            diagnostics, parser_errors = self._scan_diagnostics(scan, category)
            raw_coverage = getattr(scan, "coverage", None)
            visited = int(getattr(raw_coverage, "files_scanned", 0) or 0) if raw_coverage else 0
            visited = min(max(visited, 0), total)
            rules = list(getattr(raw_coverage, "rules_executed", []) or []) if raw_coverage else []
            producer = f"{getattr(scan, 'analyzer', 'scanner')}/1.0.0"
            if any(str(finding.get("ruleId", "")).startswith("CF-") for finding in category_findings):
                producer = f"{getattr(scan, 'analyzer', 'scanner')}+codefacts/1.0.0"
            scan_results.append({
                "schemaVersion": "0.1",
                "identity": identity,
                "signature": signature,
                "category": category,
                "status": "partial" if visited > 0 else "unsupported",
                "producerVersion": producer,
                "durationMs": int(round(float(getattr(scan, "duration_ms", 0.0) or 0.0))),
                "findings": category_findings,
                "evidence": evidence,
                "diagnostics": diagnostics,
                "coverage": {
                    "status": "partial",
                    "filesTotal": total,
                    "filesVisited": visited,
                    "rulesApplied": rules,
                    "scopes": [],
                    "skippedScopes": ["scanner-scope-only"] if visited < total else [],
                    "parserErrors": parser_errors,
                    "unresolvedSymbols": [],
                    "limitsReached": [],
                },
            })
        code_facts = {
            "schemaVersion": "0.1",
            "identity": identity,
            "signature": signature,
            "nodes": getattr(self, "_built_nodes", []),
            "edges": getattr(self, "_built_edges", []),
            "evidence": sorted(self.evidence.values(), key=lambda item: item["id"]),
            "coverage": coverage,
            "policyInputs": [],
            "diagnostics": self.diagnostics,
        }
        return {
            "schemaVersion": "0.1",
            "identity": identity,
            "signature": signature,
            "scanResults": scan_results,
            "codeFacts": code_facts,
        }

    # --- signature -------------------------------------------------------
    def _build_signature(self) -> Dict[str, Any]:
        root = Path(__file__).resolve().parent
        parser_digest = hashlib.sha256()
        for name in ("extractor.py", "source.py", "canonical.py"):
            parser_digest.update((root / name).read_bytes())
        advisories = Path(__file__).resolve().parents[1] / "analyzers" / "advisories.json"
        advisory_bytes = advisories.read_bytes() if advisories.exists() else b""
        config = {
            "extensions": sorted(TEXT_EXTENSIONS),
            "maxGraphFiles": __import__("os").getenv("CG_MAX_GRAPH_FILES", "2000"),
            "parserVersion": EXTRACTOR_VERSION,
        }
        return {
            "parserVersion": EXTRACTOR_VERSION,
            "rulesVersion": RULES_VERSION,
            "advisoryVersion": "cg-advisories/1" if advisories.exists() else "cg-advisories/0",
            "configVersion": "cg-config/1",
            "hmacKeyVersion": "cg-anchor-hmac/v1",
            "parserHash": parser_digest.hexdigest(),
            "rulesHash": _sha(canonical_json(RULE_DEFINITIONS).encode("utf-8")),
            "advisoryHash": _sha(advisory_bytes),
            "scanConfigHash": _sha(canonical_json(config).encode("utf-8")),
            "domainPolicyRevisionId": None,
            "domainPolicyVersion": None,
            "domainPolicyHash": None,
        }