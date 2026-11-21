"""Export a scan into the shared ``AnalysisBundle`` contract.

Usage::

    python -m analyzers.contract_export <snapshot>/training-project \
        --db <snapshot>/vulnerability-database/vulnerabilities.json \
        --out analyzers/examples/bundle.json

The contract lives in ``docs/data-contracts``.  This runner does not change
scanner results: it runs the same scan and adds contract identifiers,
coordinates, coverage scopes, signature hashes and the evidence graph.

A real run must pass an HMAC key for SECRET fingerprints through
``CG26_HMAC_KEY``; without it the bundle is marked with the local demo key
version so two runs are never mistaken for comparable production data.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:  # pragma: no cover - direct execution
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from analyzers.cg26.contract import build_bundle
    from analyzers.cg26.project import Project
    from analyzers.scan import run
else:
    from .cg26.contract import build_bundle
    from .cg26.project import Project
    from .scan import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export an AnalysisBundle for one scan")
    parser.add_argument("root", help="project directory to scan")
    parser.add_argument("--db", default=None, help="path to vulnerabilities.json")
    parser.add_argument("--out", default="analyzers/examples/bundle.json", help="bundle JSON path")
    parser.add_argument("--workspace-id", default=None, help="workspace UUID from intake")
    parser.add_argument("--run-id", default=None, help="run UUID from intake")
    parser.add_argument("--snapshot-id", default=None, help="snapshot UUID from intake")
    parser.add_argument("--workflow-revision-id", default=None, help="workflow revision UUID")
    args = parser.parse_args(argv)

    project = Project(args.root)
    result = run(args.root, args.db, project=project)
    bundle = build_bundle(
        args.root,
        project,
        result,
        args.db,
        workspace_id=args.workspace_id,
        run_id=args.run_id,
        snapshot_id=args.snapshot_id,
        workflow_revision_id=args.workflow_revision_id,
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    counts = {scan["category"]: len(scan["findings"]) for scan in bundle["scanResults"]}
    print(f"findings: {counts}")
    print(
        "graph: "
        f"{len(bundle['codeFacts']['nodes'])} nodes, "
        f"{len(bundle['codeFacts']['edges'])} edges, "
        f"{len(bundle['codeFacts']['evidence'])} evidence refs"
    )
    print(f"coverage: {[(scan['category'], scan['coverage']['status']) for scan in bundle['scanResults']]}")
    print(f"bundle: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
