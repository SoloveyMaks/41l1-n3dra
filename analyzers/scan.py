"""Command line runner for the Cyber Garden 2026 analyzers.

Usage::

    python -m analyzers.scan training-project --out artifacts/submission.json

The runner writes two files:

* the submission JSON required by ``docs/submission-format.md``;
* a full report with evidence, diagnostics, coverage, rules and the graph.

Raw secret values are never written or printed; only masks and lengths.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:  # pragma: no cover - direct execution
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from analyzers.cg26 import sast, sca, secrets
    from analyzers.cg26.graph import build_graph
    from analyzers.cg26.model import Diagnostic, ScanResult
    from analyzers.cg26.project import Project
else:
    from .cg26 import sast, sca, secrets
    from .cg26.graph import build_graph
    from .cg26.model import Diagnostic, ScanResult
    from .cg26.project import Project


def run(root: str | Path, database: str | Path | None = None) -> ScanResult:
    project = Project(root)
    result = ScanResult()
    result.rules = [*secrets.RULES, *sca.RULES, *sast.RULES]

    for module in (secrets, sca, sast):
        if module is sca:
            findings, diagnostics = sca.analyze(project, database)
        else:
            findings, diagnostics = module.analyze(project)
        result.findings.extend(findings)
        result.diagnostics.extend(diagnostics)

    result.coverage.root = str(root)
    result.coverage.files_scanned = project.files_scanned
    result.coverage.files_skipped = list(project.files_skipped)
    result.coverage.parser_errors = list(project.parser_errors)
    result.coverage.notes = [
        "Область анализа: Kotlin/Kotlin DSL, YAML и properties внутри указанного каталога.",
        "Ground truth не читается анализаторами; сравнение выполняется отдельным скриптом.",
    ]
    result.graph = build_graph(project)
    result.dedupe()
    return result


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cyber Garden 2026 SECRET/SCA/SAST analyzers")
    parser.add_argument("root", nargs="?", default="training-project", help="project directory to scan")
    parser.add_argument("--db", default=None, help="path to vulnerabilities.json")
    parser.add_argument("--out", default="artifacts/submission.json", help="submission JSON path")
    parser.add_argument("--full", default="artifacts/scan-full.json", help="full report JSON path")
    args = parser.parse_args(argv)

    result = run(args.root, args.db)
    _write_json(Path(args.out), result.to_submission())
    _write_json(Path(args.full), result.to_full())

    summary = result.summary()
    print(f"findings: {summary['total']} ({summary['byCategory']})")
    print(f"diagnostics: {summary['diagnostics']}, files scanned: {summary['filesScanned']}")
    print(f"submission: {Path(args.out)}")
    print(f"full report: {Path(args.full)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
