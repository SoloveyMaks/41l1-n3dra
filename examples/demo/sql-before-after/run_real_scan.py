#!/usr/bin/env python3
"""Run the real backend analyzers on the committed SQL demo ZIPs.

The output is an adapter document, not a backend change. It contains:

* real SAST/SECRET/SCA findings and aggregate analyzer coverage;
* one observed SAST scope for each file where SastAnalyzer actually invoked
  the KOTLIN-SQL-INJECTION rule (the positive/negative rule outcome);
* real code-facts nodes/edges/evidence and source-graph coverage.

The observation wrapper is local to this demo. Production backend files are
not modified. See docs/sql-before-after.md for the production gap.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]
sys.path.insert(0, str(REPO))

from backend.app.analyzers.sca_analyzer import ScaAnalyzer  # noqa: E402
from backend.app.analyzers.sast_analyzer import SastAnalyzer  # noqa: E402
from backend.app.analyzers.secret_analyzer import SecretAnalyzer  # noqa: E402
from backend.app.artifacts.intake import build_snapshot_from_directory  # noqa: E402
from backend.app.codefacts.canonical import CanonicalMapBuilder  # noqa: E402
from backend.app.codefacts.extractor import RULE_DEFINITIONS, extract_facts  # noqa: E402
from backend.app.codefacts.source import load_snapshot_sources  # noqa: E402
from backend.app.codefacts.identity import revision_uuid  # noqa: E402

CATEGORY = {"sast": "SAST", "secret": "SECRET", "sca": "SCA"}
WORKSPACE_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "sql-before-after/workspace"))
WORKFLOW_REVISION_ID = revision_uuid("sql-before-after/rev")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract_zip(zip_path: Path, target: Path) -> None:
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            rel = Path(member.filename)
            if rel.is_absolute() or ".." in rel.parts:
                raise RuntimeError(f"unsafe ZIP member: {member.filename}")
        archive.extractall(target)


def finding_evidence_ids(findings: List[Dict[str, Any]]) -> List[str]:
    refs: List[str] = []
    for finding in findings:
        for evidence_id in [
            finding.get("primaryEvidenceId"),
            *(finding.get("relatedEvidenceIds") or []),
            *(finding.get("traceEvidenceIds") or []),
        ]:
            if evidence_id and evidence_id not in refs:
                refs.append(evidence_id)
    return refs


def build_side(side: str) -> Dict[str, Any]:
    archive_path = ROOT / f"{side}.zip"
    if not archive_path.exists():
        raise SystemExit(f"missing {archive_path}; run prepare_snapshots.py first")
    zip_bytes = archive_path.read_bytes()
    artifact_hash = sha256_bytes(zip_bytes)
    run_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"sql-before-after/run/{side}/{artifact_hash}"))

    with tempfile.TemporaryDirectory(prefix=f"sql-before-after-{side}-") as temp:
        root = Path(temp)
        extract_zip(archive_path, root)
        snapshot = build_snapshot_from_directory(root, WORKSPACE_ID)
        sources, _ = load_snapshot_sources(root, snapshot)
        facts = extract_facts(sources, len(snapshot["files"]))

        sast_result = SastAnalyzer().analyze(str(root))
        secret_result = SecretAnalyzer().analyze(str(root))
        sca_result = ScaAnalyzer().analyze(str(root))

        builder = CanonicalMapBuilder(
            workspace_id=WORKSPACE_ID,
            run_id=run_id,
            snapshot_id=snapshot["snapshotId"],
            artifact_hash=artifact_hash,
            snapshot_manifest_hash=snapshot["manifestHash"],
            workflow_revision_id=WORKFLOW_REVISION_ID,
            facts=facts,
            sources=sources,
            snapshot_files=snapshot["files"],
            backend_findings=sast_result.findings,
        )
        payload = builder.build()

    code_facts = dict(payload["map"])
    code_facts.pop("bundleHash", None)
    all_evidence = list(code_facts.get("evidence") or [])
    evidence_by_id = {item["id"]: item for item in all_evidence}

    # Source-graph coverage is built only from files the real extractor read and
    # only when all produced edges are resolved. Runtime scopes are deliberately
    # outside this demo boundary (see docs/sql-before-after.md).
    source_paths = sorted(set(facts.files_read))
    parser_error_ids = [
        diag["id"] for diag in code_facts.get("diagnostics", [])
        if diag.get("code") == "CF-DECODE-ERROR"
    ]
    unresolved = list(dict.fromkeys(facts.unresolved_symbols))
    limits = list(facts.limits)
    edges_resolved = all(edge.get("resolution") == "resolved" for edge in code_facts.get("edges", []))
    graph_complete = (
        len(source_paths) == len(snapshot["files"])
        and not parser_error_ids
        and not unresolved
        and not limits
        and edges_resolved
    )
    code_facts["coverage"] = {
        "status": "complete" if graph_complete else "partial",
        "filesTotal": len(snapshot["files"]),
        "filesVisited": len(source_paths),
        "rulesApplied": [rule["id"] for rule in RULE_DEFINITIONS],
        "scopes": [
            {
                "path": path,
                "ruleId": "CF-SPRING-STATIC",
                "category": "SAST",
                "status": "checked",
                "reason": "Source graph read; runtime scopes are outside this demo boundary.",
            }
            for path in source_paths
        ],
        "skippedScopes": [],
        "parserErrors": parser_error_ids,
        "unresolvedSymbols": unresolved,
        "limitsReached": limits,
    }

    contract_findings = payload["findings"]
    by_category = {
        category: [finding for finding in contract_findings if finding["category"] == category]
        for category in ("SECRET", "SCA", "SAST")
    }

    # Expected SQL scope: files that expose an HTTP input parameter to the real
    # analyzer. This is a fixture-level expectation, verified against the
    # actually observed rule invocation below.
    expected_sql_paths = sorted(
        path for path, source in sources.items()
        if any(token in source.text for token in ("@RequestParam", "@PathVariable", "@RequestBody"))
    )
    sql_observations = [
        observation.model_dump()
        for observation in sast_result.coverage.rule_observations
        if observation.rule_id == "KOTLIN-SQL-INJECTION"
    ]
    checked_sql = [item for item in sql_observations if item["status"] == "checked"]
    observed_sql_paths = sorted({item["path"] for item in checked_sql})
    sast_parser_ok = sast_result.coverage.parser_errors == 0
    expected_sql_verified = (
        expected_sql_paths == observed_sql_paths
        and sast_parser_ok
        and "KOTLIN-SQL-INJECTION" in sast_result.coverage.rules_executed
        and sum(item["findings"] for item in checked_sql) == len(sast_result.findings)
        and sast_result.coverage.files_scanned == sum(1 for path in snapshot["files"] if path["path"].endswith(".kt"))
    )
    sast_scopes = [
        {
            "path": item["path"],
            "ruleId": "KOTLIN-SQL-INJECTION",
            "category": "SAST",
            "status": "checked",
            "reason": item["reason"],
        }
        for item in checked_sql
    ]

    def scan_part(analyzer, scan_result, status: str, coverage: Dict[str, Any]) -> Dict[str, Any]:
        findings = by_category[CATEGORY[analyzer]]
        evidence_ids = set(finding_evidence_ids(findings))
        return {
            "schemaVersion": "0.1",
            "identity": payload["identity"],
            "signature": payload["map"]["signature"],
            "category": CATEGORY[analyzer],
            "status": status,
            "findings": findings,
            "evidence": [evidence_by_id[item] for item in evidence_ids if item in evidence_by_id],
            "coverage": coverage,
            "diagnostics": [],
        }

    sast_status = "complete" if expected_sql_verified else "partial"
    scan_results = [
        scan_part("secret", secret_result, "partial", {
            "status": "partial",
            "filesTotal": len(snapshot["files"]),
            "filesVisited": secret_result.coverage.files_scanned,
            "rulesApplied": list(secret_result.coverage.rules_executed),
            "scopes": [],
            "skippedScopes": ["not-instrumented-in-sql-demo"],
            "parserErrors": [],
            "unresolvedSymbols": [],
            "limitsReached": [],
        }),
        scan_part("sca", sca_result, "partial", {
            "status": "partial",
            "filesTotal": len(snapshot["files"]),
            "filesVisited": sca_result.coverage.files_scanned,
            "rulesApplied": list(sca_result.coverage.rules_executed),
            "scopes": [],
            "skippedScopes": ["not-instrumented-in-sql-demo"],
            "parserErrors": [],
            "unresolvedSymbols": [],
            "limitsReached": [],
        }),
        scan_part("sast", sast_result, sast_status, {
            "status": sast_status,
            "filesTotal": sum(1 for path in snapshot["files"] if path["path"].endswith(".kt")),
            "filesVisited": sast_result.coverage.files_scanned,
            "rulesApplied": list(sast_result.coverage.rules_executed),
            "scopes": sast_scopes,
            "skippedScopes": [] if expected_sql_verified else ["sql-rule-not-observed"],
            "parserErrors": [],
            "unresolvedSymbols": [],
            "limitsReached": [],
        }),
    ]

    return {
        "side": side,
        "zip": {
            "name": archive_path.name,
            "sha256": artifact_hash,
            "sizeBytes": len(zip_bytes),
        },
        "manifestFiles": [
            {
                "path": item["path"],
                "sha256": item["sha256"],
                "sizeBytes": item["sizeBytes"],
                "mediaType": "text/x-kotlin",
            }
            for item in snapshot["files"]
        ],
        "identity": payload["identity"],
        "signature": payload["map"]["signature"],
        "scanResults": scan_results,
        "codeFacts": code_facts,
        "observations": {
            "sastRuleObservations": sql_observations,
            "expectedSqlPaths": expected_sql_paths,
            "observedSqlPaths": observed_sql_paths,
            "sastSkipped": [
                item for item in sql_observations
                if item["status"] in ("skipped", "error")
            ],
            "sastNotApplicable": [
                item for item in sql_observations
                if item["status"] == "not_applicable"
            ],
            "sastFindings": len(sast_result.findings),
            "sastParserErrors": sast_result.coverage.parser_errors,
            "sastFilesScanned": sast_result.coverage.files_scanned,
            "secretFindings": len(secret_result.findings),
            "scaFindings": len(sca_result.findings),
            "codeFactsUnresolved": unresolved,
            "codeFactsLimits": limits,
            "codeFactsEdgesResolved": edges_resolved,
        },
        "expectedScopesVerified": bool(expected_sql_verified and graph_complete),
    }


def _sql_observation(result, rel_path: str):
    for observation in result.coverage.rule_observations:
        if observation.rule_id == "KOTLIN-SQL-INJECTION" and observation.path == rel_path:
            return observation.model_dump()
    return None


def run_observation_negatives() -> None:
    """Prove parser-error, unreadable and not-applicable are not checked."""
    cases = []
    with tempfile.TemporaryDirectory(prefix="sql-observation-parser-") as temp:
        root = Path(temp)
        (root / "Broken.kt").write_text(
            "package demo\n\nclass Broken {\n    fun broken( {\n}\n",
            encoding="utf-8",
        )
        result = SastAnalyzer().analyze(str(root))
        cases.append({
            "case": "parser-error",
            "parserErrors": result.coverage.parser_errors,
            "observation": _sql_observation(result, "Broken.kt"),
        })

    with tempfile.TemporaryDirectory(prefix="sql-observation-unreadable-") as temp:
        root = Path(temp)
        path = root / "Unreadable.kt"
        path.write_text(
            "package demo\n\nclass Unreadable {\n"
            "    fun find(@RequestParam name: String): String = name\n"
            "}\n",
            encoding="utf-8",
        )
        os.chmod(path, 0o000)
        try:
            result = SastAnalyzer().analyze(str(root))
            cases.append({
                "case": "unreadable-file",
                "parserErrors": result.coverage.parser_errors,
                "observation": _sql_observation(result, "Unreadable.kt"),
            })
        finally:
            os.chmod(path, 0o600)

    with tempfile.TemporaryDirectory(prefix="sql-observation-not-applicable-") as temp:
        root = Path(temp)
        (root / "Plain.kt").write_text(
            "package demo\n\nclass Plain {\n    fun ping(): String = \"pong\"\n}\n",
            encoding="utf-8",
        )
        result = SastAnalyzer().analyze(str(root))
        cases.append({
            "case": "not-applicable",
            "parserErrors": result.coverage.parser_errors,
            "observation": _sql_observation(result, "Plain.kt"),
        })

    for case in cases:
        observation = case["observation"]
        case["checked"] = bool(observation and observation["status"] == "checked")
    (ROOT / "sast-observation-probes.json").write_text(
        json.dumps(cases, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for case in cases:
        observation = case["observation"] or {}
        print(
            f"observation {case['case']}: status={observation.get('status')} "
            f"parserErrors={case['parserErrors']} checked={case['checked']}"
        )
    assert cases[0]["observation"]["status"] == "skipped" and not cases[0]["checked"]
    assert cases[1]["observation"]["status"] == "skipped" and not cases[1]["checked"]
    assert cases[2]["observation"]["status"] == "not_applicable" and not cases[2]["checked"]


def main() -> None:
    for side in ("before", "after"):
        raw = build_side(side)
        (ROOT / f"raw-{side}.json").write_text(
            json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        obs = raw["observations"]
        print(
            f"{side}: SAST findings={obs['sastFindings']} observed_sql={obs['observedSqlPaths']} "
            f"secret={obs['secretFindings']} sca={obs['scaFindings']} "
            f"expectedScopesVerified={raw['expectedScopesVerified']}"
        )
    run_observation_negatives()


if __name__ == "__main__":
    main()