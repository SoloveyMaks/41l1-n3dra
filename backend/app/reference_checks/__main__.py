"""Compare a pinned manifest with one published bundle from the command line.

    python -m backend.app.reference_checks MANIFEST BUNDLE \\
        --target-sha SHA --target-project-id ID [--target-branch NAME] [--out report.json]

Exit codes: 0 pass, 1 fail, 2 inconclusive, 3 rejected input.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .compare import compare_reference_suite
from .manifest import ReferenceCheckError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare a reference suite with an AnalysisBundle")
    parser.add_argument("manifest", help="reference-suite/v1 manifest")
    parser.add_argument("bundle", help="published AnalysisBundle 0.1")
    parser.add_argument("--target-sha", required=True, help="commit SHA of the scanned snapshot")
    parser.add_argument("--target-project-id", required=True, help="project id of the scanned snapshot")
    parser.add_argument("--target-branch", default=None, help="branch or snapshot label")
    parser.add_argument("--out", default=None, help="write the report here instead of stdout")
    args = parser.parse_args(argv)

    try:
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        bundle = json.loads(Path(args.bundle).read_text(encoding="utf-8"))
        report = compare_reference_suite(
            manifest=manifest,
            bundle=bundle,
            target_sha=args.target_sha,
            target_project_id=args.target_project_id,
            target_branch=args.target_branch,
        )
    except (OSError, json.JSONDecodeError) as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 3
    except ReferenceCheckError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 3

    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    print(
        f"{report['status']}: {report['summary']['matchedFindings']}/"
        f"{report['summary']['expectedFindings']} findings, "
        f"{report['summary']['matchedControls']}/"
        f"{report['summary']['expectedControls']} controls",
        file=sys.stderr,
    )
    return {"pass": 0, "fail": 1, "inconclusive": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())