"""Compare analyzer output with the training ground truth.

The ground truth is read here only.  Scanner modules never import this file or
the ground-truth dataset.

Usage::

    python -m analyzers.compare artifacts/submission.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _overlap(left: dict, right: dict) -> bool:
    if left.get("path") != right.get("path"):
        return False
    left_line = int(left.get("line") or 1)
    left_end = int(left.get("endLine") or left_line)
    right_line = int(right.get("line") or 1)
    right_end = int(right.get("endLine") or right_line)
    return left_line <= right_end and right_line <= left_end


def _matches(finding: dict, truth: dict) -> bool:
    if finding.get("category") != truth.get("category"):
        return False
    if truth.get("category") == "SCA":
        if finding.get("artifact") != truth.get("artifact"):
            return False
        if finding.get("group") != truth.get("group"):
            return False
        advisory = finding.get("advisoryId")
        return advisory in (truth.get("advisoryIds") or [])
    if finding.get("class") != truth.get("class"):
        return False
    return _overlap(finding, truth)


def _matches_control(finding: dict, control: dict) -> bool:
    if finding.get("category") != control.get("category"):
        return False
    if control.get("category") == "SCA":
        return (
            finding.get("group") == control.get("group")
            and finding.get("artifact") == control.get("artifact")
        )
    return _overlap(finding, control)


def compare(submission_path: str | Path, truth_path: str | Path = "ground-truth/training-findings.json") -> dict:
    submission = json.loads(Path(submission_path).read_text(encoding="utf-8"))
    truth = json.loads(Path(truth_path).read_text(encoding="utf-8"))
    findings = submission.get("findings", [])
    expected = [item for item in truth.get("findings", []) if item.get("confirmation") != "rejected"]
    controls = truth.get("controls", [])

    matched_truth: set[str] = set()
    matched_findings: set[int] = set()
    rows: list[dict] = []
    for item in expected:
        hit_index = next(
            (
                index
                for index, finding in enumerate(findings)
                if index not in matched_findings and _matches(finding, item)
            ),
            None,
        )
        status = "correct" if hit_index is not None else "missed"
        if hit_index is not None:
            matched_findings.add(hit_index)
            matched_truth.add(item["id"])
        rows.append(
            {
                "id": item["id"],
                "category": item["category"],
                "class": item.get("class"),
                "path": item["path"],
                "line": item["line"],
                "endLine": item.get("endLine", item["line"]),
                "status": status,
                "reported": findings[hit_index] if hit_index is not None else None,
            }
        )

    control_rows: list[dict] = []
    for control in controls:
        hits = [
            finding
            for finding in findings
            if _matches_control(finding, control)
        ]
        control_rows.append(
            {
                "id": control["id"],
                "category": control["category"],
                "class": control.get("class"),
                "path": control["path"],
                "line": control["line"],
                "status": "clean" if not hits else "false-positive",
                "reported": hits,
            }
        )

    false_positives = [
        finding
        for index, finding in enumerate(findings)
        if index not in matched_findings
    ]

    by_category: dict[str, dict[str, int]] = {}
    for row in rows:
        bucket = by_category.setdefault(row["category"], {"expected": 0, "correct": 0, "missed": 0})
        bucket["expected"] += 1
        bucket["correct" if row["status"] == "correct" else "missed"] += 1
    for row in control_rows:
        bucket = by_category.setdefault(row["category"], {"expected": 0, "correct": 0, "missed": 0, "controls": 0, "controlHits": 0})
        bucket["controls"] = bucket.get("controls", 0) + 1
        bucket["controlHits"] = bucket.get("controlHits", 0) + (1 if row["status"] == "false-positive" else 0)
    for finding in false_positives:
        bucket = by_category.setdefault(finding["category"], {"expected": 0, "correct": 0, "missed": 0})
        bucket["falsePositives"] = bucket.get("falsePositives", 0) + 1

    return {
        "rows": rows,
        "controls": control_rows,
        "falsePositives": false_positives,
        "byCategory": by_category,
        "summary": {
            "expected": len(expected),
            "correct": len(matched_truth),
            "missed": len(expected) - len(matched_truth),
            "falsePositives": len(false_positives),
            "controls": len(control_rows),
            "controlHits": sum(1 for row in control_rows if row["status"] == "false-positive"),
        },
    }


def _table(rows: list[dict], key: str) -> str:
    lines = [
        f"| {key} | класс | путь:строка | статус |",
        "| --- | --- | --- | --- |",
    ]
    for row in rows:
        location = f"{row['path']}:{row['line']}"
        if row.get("endLine") and row["endLine"] != row["line"]:
            location += f"-{row['endLine']}"
        lines.append(f"| {row['id']} | {row.get('class') or '-'} | {location} | {row['status']} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare submission with training ground truth")
    parser.add_argument("submission", nargs="?", default="artifacts/submission.json")
    parser.add_argument("--truth", default="ground-truth/training-findings.json")
    parser.add_argument("--out", default="artifacts/comparison.json")
    args = parser.parse_args(argv)

    report = compare(args.submission, args.truth)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = report["summary"]
    print(f"expected: {summary['expected']}, correct: {summary['correct']}, "
          f"missed: {summary['missed']}, false positives: {summary['falsePositives']}")
    print(f"controls: {summary['controls']}, control hits: {summary['controlHits']}")
    print()
    print(_table(report["rows"], "finding"))
    print()
    print(_table(report["controls"], "control"))
    if report["falsePositives"]:
        print()
        print("False positives:")
        for finding in report["falsePositives"]:
            print(f"- {finding['category']} {finding.get('class') or finding.get('artifact')} "
                  f"{finding['path']}:{finding.get('line')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
