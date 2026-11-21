"""Evidence redaction for files that contain a secret finding.

``codefacts`` masks snippets on its own, but a secret can be assembled from
short literals or assigned to a camelCase name that the generic pattern misses.
Lines covered by a SECRET finding get secret-looking assignments and every
string literal masked. Masking keeps the original character count so byte
offsets and UI highlighting stay valid.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Dict, Iterable, List, Set, Tuple

from backend.app.codefacts.source import SourceFile

_STRING_LITERAL = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'')
_SECRET_ASSIGN = re.compile(
    r"(?i)([A-Za-z0-9_.\-]*(?:password|passwd|pwd|secret|token|api[_-]?key|"
    r"credential|private[_-]?key)[A-Za-z0-9_.\-]*\s*[:=]\s*)([\"'])(.*?)(\2)"
)
_ENV_DEFAULT = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):([^}]*)\}")

Ranges = Dict[str, List[Tuple[int, int]]]


def _mask_inner(literal: str) -> str:
    quote = literal[0]
    inner = literal[1:-1]
    return f"{quote}{'*' * len(inner)}{quote}"


def mask_secret_text(text: str) -> str:
    if not text:
        return ""

    def assign(match: re.Match[str]) -> str:
        quote = match.group(2)
        value = match.group(3)
        return f"{match.group(1)}{quote}{'*' * len(value)}{quote}"

    text = _SECRET_ASSIGN.sub(assign, text)
    text = _STRING_LITERAL.sub(lambda match: _mask_inner(match.group(0)), text)
    text = _ENV_DEFAULT.sub(
        lambda match: "${" + match.group(1) + ":" + "*" * len(match.group(2)) + "}",
        text,
    )
    return text


def secret_ranges(findings: Iterable[Any]) -> Ranges:
    ranges: Ranges = {}

    def add(location: Any) -> None:
        if location is None or not getattr(location, "path", ""):
            return
        start = max(1, int(getattr(location, "startLine", 1) or 1))
        end = max(start, int(getattr(location, "endLine", start) or start))
        ranges.setdefault(location.path, []).append((start, end))

    for finding in findings:
        if getattr(finding, "analyzer", "") != "secret":
            continue
        add(getattr(finding, "primaryLocation", None))
        for location in getattr(finding, "relatedLocations", []) or []:
            add(location)
        for step in getattr(finding, "trace", []) or []:
            add(step.location)
    return ranges


def _overlaps(start: int, end: int, spans: List[Tuple[int, int]]) -> bool:
    return any(start <= span_end and span_start <= end for span_start, span_end in spans)


def _mask_lines(text: str, spans: List[Tuple[int, int]]) -> str:
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines, start=1):
        if _overlaps(index, index, spans):
            ending = line[len(line.rstrip("\r\n")):]
            lines[index - 1] = mask_secret_text(line.rstrip("\r\n")) + ending
    return "".join(lines)


def sanitize_sources(sources: Dict[str, SourceFile], ranges: Ranges) -> Dict[str, SourceFile]:
    if not ranges:
        return sources
    return {
        path: (
            replace(source, text=_mask_lines(source.text, ranges[path]))
            if path in ranges else source
        )
        for path, source in sources.items()
    }


def sanitize_fact_evidence(facts: Any, ranges: Ranges) -> None:
    """Mutate the in-memory extraction result before it is serialized."""
    if not ranges:
        return

    def clean(item: Dict[str, Any]) -> None:
        location = item.get("location") or {}
        path = location.get("path")
        if path not in ranges:
            return
        start = int(location.get("line", 1) or 1)
        end = int(location.get("endLine", start) or start)
        if _overlaps(start, end, ranges[path]):
            item["redactedSnippet"] = mask_secret_text(str(item.get("redactedSnippet", "")))

    for node in getattr(facts, "nodes", {}).values():
        for item in getattr(node, "evidence", []) or []:
            clean(item)
    for edge in getattr(facts, "edges", []) or []:
        for item in getattr(edge, "evidence", []) or []:
            clean(item)
    for diagnostic in getattr(facts, "diagnostics", []) or []:
        item = diagnostic.get("evidence")
        if isinstance(item, dict):
            clean(item)