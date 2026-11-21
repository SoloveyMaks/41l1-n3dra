"""Compare a pinned manifest with one local bundle in raw-fixture mode.

    python -m backend.app.reference_checks MANIFEST BUNDLE [--out report.json]

This is an untrusted fixture comparison, not an attestation. The bundle and
manifest come from the caller, the bundle identity is not resolved to a
canonical published envelope, and no target commit SHA is accepted or claimed.
For an attested check use ``POST /api/reference-checks/compare`` with
``referenceManifestId`` and ``targetRunId``.

Exit codes: 0 pass, 1 fail, 2 inconclusive, 3 rejected input.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .compare import RAW_MODE, compare_reference_suite
from .manifest import ReferenceCheckError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Untrusted raw-fixture comparison of a reference suite with an AnalysisBundle"
    )
    parser.add_argument("manifest", help="reference-suite/v1 manifest")
    parser.add_argument("bundle", help="local AnalysisBundle 0.1 fixture")
    parser.add_argument("--out", default=None, help="write the report here instead of stdout")
    args = parser.parse_args(argv)

    try:
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        bundle = json.loads(Path(args.bundle).read_text(encoding="utf-8"))
        report = compare_reference_suite(
            manifest=manifest,
            bundle=bundle,
            mode=RAW_MODE,
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
        f"UNTRUSTED raw-fixture comparison (not attestation): {report['status']}: "
        f"{report['summary']['matchedFindings']}/"
        f"{report['summary']['expectedFindings']} findings, "
        f"{report['summary']['matchedControls']}/"
        f"{report['summary']['expectedControls']} controls",
        file=sys.stderr,
    )
    return {"pass": 0, "fail": 1, "inconclusive": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
