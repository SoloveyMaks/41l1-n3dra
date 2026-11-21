"""Common result model shared by every analyzer.

The public JSON export follows ``docs/submission-format.md`` exactly.  The full
export additionally keeps evidence, diagnostics and the evidence graph that the
workflow engine needs for live runs and the Evidence Graph screen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCANNER_VERSION = "0.1.0"


@dataclass(frozen=True)
class Location:
    """A source location. Lines are 1-based and inclusive."""

    path: str
    line: int
    end_line: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"path": self.path, "line": self.line}
        if self.end_line is not None:
            data["endLine"] = self.end_line
        return data

    def overlaps(self, other: "Location") -> bool:
        if self.path != other.path:
            return False
        a_end = self.end_line if self.end_line is not None else self.line
        b_end = other.end_line if other.end_line is not None else other.line
        return self.line <= b_end and other.line <= a_end


@dataclass
class Finding:
    """One reported problem.

    ``class_code`` is required for SECRET/SAST and empty for SCA.
    """

    category: str
    path: str
    severity: str
    description: str
    remediation: str = ""
    class_code: str | None = None
    line: int | None = None
    end_line: int | None = None
    group: str | None = None
    artifact: str | None = None
    version: str | None = None
    advisory_id: str | None = None
    rule_id: str = ""
    rule_version: str = "1"
    confidence: str = "confirmed"
    confidence_basis: str = ""
    severity_basis: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    related: list[Location] = field(default_factory=list)
    # Raw secret material for the contract fingerprint only.  It is never
    # serialized: exports carry a mask, a length and an HMAC digest instead.
    secret_value: str | None = field(default=None, repr=False)

    @property
    def location(self) -> Location:
        return Location(self.path, self.line or 1, self.end_line)

    def identity(self) -> tuple[Any, ...]:
        return (
            self.category,
            self.path,
            self.class_code,
            self.line,
            self.group,
            self.artifact,
            self.version,
            self.advisory_id,
        )

    def sort_key(self) -> tuple[Any, ...]:
        order = {"SECRET": 0, "SCA": 1, "SAST": 2}
        return (
            order.get(self.category, 9),
            self.path,
            self.line or 0,
            self.class_code or "",
            self.advisory_id or "",
        )

    def to_submission(self) -> dict[str, Any]:
        """Minimal export accepted by the organizers' report format."""

        data: dict[str, Any] = {
            "category": self.category,
            "path": self.path,
        }
        if self.class_code is not None:
            data["class"] = self.class_code
            data["line"] = int(self.line or 1)
            if self.end_line is not None and self.end_line != self.line:
                data["endLine"] = int(self.end_line)
        if self.severity:
            data["severity"] = self.severity
        if self.description:
            data["description"] = self.description
        if self.remediation:
            data["remediation"] = self.remediation
        if self.category == "SCA":
            data["group"] = self.group
            data["artifact"] = self.artifact
            data["version"] = self.version
            data["advisoryId"] = self.advisory_id
        return data

    def to_full(self) -> dict[str, Any]:
        data = self.to_submission()
        data.update(
            {
                "ruleId": self.rule_id,
                "ruleVersion": self.rule_version,
                "confidence": self.confidence,
                "confidenceBasis": self.confidence_basis,
                "severityBasis": self.severity_basis,
                "evidence": self.evidence,
                "relatedLocations": [
                    loc.to_dict() if isinstance(loc, Location) else dict(loc) for loc in self.related
                ],
            }
        )
        return data


@dataclass
class Diagnostic:
    """Non-finding information: parser errors, unknown versions, skips."""

    code: str
    message: str
    path: str | None = None
    line: int | None = None
    level: str = "warning"
    category: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"code": self.code, "level": self.level, "message": self.message}
        if self.category:
            data["category"] = self.category
        if self.path is not None:
            data["path"] = self.path
        if self.line is not None:
            data["line"] = self.line
        return data


@dataclass
class RuleInfo:
    rule_id: str
    category: str
    version: str
    description: str
    class_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ruleId": self.rule_id,
            "category": self.category,
            "version": self.version,
            "description": self.description,
            "class": self.class_code,
        }


@dataclass
class Coverage:
    root: str
    files_scanned: int = 0
    files_skipped: list[dict[str, str]] = field(default_factory=list)
    parser_errors: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def skip(self, path: str, reason: str) -> None:
        self.files_skipped.append({"path": path, "reason": reason})

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "filesScanned": self.files_scanned,
            "filesSkipped": self.files_skipped,
            "parserErrors": self.parser_errors,
            "notes": self.notes,
        }


@dataclass
class ScanResult:
    """Uniform result returned by SECRET, SCA and SAST analyzers."""

    findings: list[Finding] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    coverage: Coverage = field(default_factory=lambda: Coverage(root="."))
    graph: dict[str, Any] = field(default_factory=dict)
    rules: list[RuleInfo] = field(default_factory=list)
    durations_ms: dict[str, int] = field(default_factory=dict)
    scanner_version: str = SCANNER_VERSION

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)

    def add_diagnostic(self, diagnostic: Diagnostic) -> None:
        self.diagnostics.append(diagnostic)

    def dedupe(self) -> None:
        seen: set[tuple[Any, ...]] = set()
        unique: list[Finding] = []
        for finding in self.findings:
            key = finding.identity()
            if key in seen:
                continue
            seen.add(key)
            unique.append(finding)
        self.findings = sorted(unique, key=Finding.sort_key)

    def to_submission(self) -> dict[str, Any]:
        self.dedupe()
        return {"findings": [f.to_submission() for f in self.findings]}

    def to_full(self) -> dict[str, Any]:
        self.dedupe()
        return {
            "scannerVersion": self.scanner_version,
            "findings": [f.to_full() for f in self.findings],
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "coverage": self.coverage.to_dict(),
            "rules": [r.to_dict() for r in self.rules],
            "graph": self.graph,
            "durationsMs": self.durations_ms,
        }

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {"SECRET": 0, "SCA": 0, "SAST": 0}
        for finding in self.findings:
            counts[finding.category] = counts.get(finding.category, 0) + 1
        return {
            "total": len(self.findings),
            "byCategory": counts,
            "diagnostics": len(self.diagnostics),
            "filesScanned": self.coverage.files_scanned,
        }
