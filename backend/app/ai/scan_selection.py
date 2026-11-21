"""Bounded, redacted source selection for the AI vulnerability scanner.

Only files from the frozen snapshot are read. Imported project code is treated as
data: it is never imported, executed or written back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Sequence, Set, Tuple

from backend.app.ai.provider import mask_sensitive_text
from backend.app.codefacts.source import SourceFile, mask_text
from backend.app.contracts.schemas import (
    AiScanCoverage,
    AiScanCoverageIssue,
    Finding,
    Severity,
)

SELECTION_VERSION = "ai-scan-selection/1"

_ALLOWED_SUFFIXES = {
    ".kt", ".kts", ".java", ".yml", ".yaml", ".properties", ".xml", ".toml",
}
_SENSITIVE_SUFFIXES = {".env", ".pem", ".key", ".crt", ".cer", ".p12", ".jks"}
_SKIP_PATH_PARTS = {
    "/test/", "/androidtest/", "/generated/", "/build/", "/node_modules/",
    "/.git/", "/__pycache__/",
}
_RUNTIME_CONFIG_NAMES = {
    "application.yml", "application.yaml", "application.properties",
    "bootstrap.yml", "bootstrap.yaml", "build.gradle.kts",
}
_CONFIG_NAMES = _RUNTIME_CONFIG_NAMES | {"settings.gradle.kts"}
_SOURCE_HINTS = (
    "controller", "service", "repository", "route", "router", "security",
    "filter", "config", "endpoint", "handler", "client", "crypto", "cors",
)


@dataclass(frozen=True)
class ScanLimits:
    profile: str = "balanced"
    max_files: int = 6
    max_bytes: int = 24576
    per_file_bytes: int = 4096
    max_observations: int = 12


@dataclass(frozen=True)
class SelectedFile:
    file_ref: str
    path: str
    source: SourceFile
    start_line: int
    end_line: int
    excerpt: str
    truncated: bool

    @property
    def bytes_sent(self) -> int:
        return len(self.excerpt.encode("utf-8"))


@dataclass
class SelectionResult:
    files: List[SelectedFile] = field(default_factory=list)
    coverage: AiScanCoverage = field(default_factory=AiScanCoverage)

    @property
    def by_ref(self) -> Dict[str, SelectedFile]:
        return {item.file_ref: item for item in self.files}


def _severity_rank(severity: Severity) -> int:
    order = {
        Severity.CRITICAL: 0,
        Severity.HIGH: 1,
        Severity.MEDIUM: 2,
        Severity.LOW: 3,
        Severity.INFO: 4,
    }
    return order.get(severity, 5)


def _allowed_path(path: str) -> bool:
    lower = path.lower()
    if any(part in f"/{lower}" for part in _SKIP_PATH_PARTS):
        return False
    suffix = Path(lower).suffix
    if suffix in _SENSITIVE_SUFFIXES:
        return False
    if suffix not in _ALLOWED_SUFFIXES:
        return False
    name = Path(lower).name
    if name.startswith(".env"):
        return False
    return True


def _is_config(path: str) -> bool:
    return Path(path).name.lower() in _CONFIG_NAMES


def _is_runtime_config(path: str) -> bool:
    return Path(path).name.lower() in _RUNTIME_CONFIG_NAMES


def _source_priority(path: str) -> int:
    lower = path.lower()
    if _is_runtime_config(path):
        return 1
    if suffix_source(lower):
        if any(hint in lower for hint in _SOURCE_HINTS):
            return 2
        return 3
    return 4


def suffix_source(lower_path: str) -> bool:
    return lower_path.endswith((".kt", ".kts", ".java"))


def _mask_excerpt(text: str) -> str:
    """Reuse the project's existing redaction without its 600-char truncation.

    `mask_sensitive_text` handles secrets that can span lines (PEM, bearer,
    assignments); `mask_text` handles URI credentials, long tokens, env defaults
    and control characters. Chunking keeps `mask_text` below its truncation
    limit while preserving newlines.
    """
    pre_masked = mask_sensitive_text(text)
    pieces: List[str] = []
    start = 0
    length = len(pre_masked)
    while start < length:
        end = min(start + 500, length)
        if end < length:
            newline = pre_masked.rfind("\n", start, end)
            if newline > start:
                end = newline + 1
        pieces.append(mask_text(pre_masked[start:end]))
        start = end
    return "".join(pieces)


def _truncate_to_line(text: str, max_bytes: int) -> Tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    prefix = encoded[:max_bytes]
    cut = prefix.rfind(b"\n")
    if cut <= 0:
        return "", True
    return prefix[:cut].decode("utf-8", "ignore") + "\n", True


def _normalized_lines(text: str) -> List[str]:
    """Lines without CRLF artifacts; line numbers stay identical."""
    return [line[:-1] if line.endswith("\r") else line for line in text.split("\n")]


def _line_window(source: SourceFile, start_line: int, end_line: int) -> str:
    lines = _normalized_lines(source.text)
    start = max(1, start_line)
    end = min(len(lines), max(start, end_line))
    return "\n".join(lines[start - 1:end])


def _deterministic_windows(findings: Sequence[Finding]) -> Dict[str, Tuple[int, int, int]]:
    windows: Dict[str, Tuple[int, int, int]] = {}
    for finding in findings:
        locations = [finding.primaryLocation, *finding.relatedLocations]
        for location in locations:
            path = location.path
            start = max(1, int(location.startLine) - 30)
            end = max(int(location.endLine) + 40, start)
            rank = _severity_rank(finding.severity)
            current = windows.get(path)
            if current is None:
                windows[path] = (rank, start, end)
            else:
                windows[path] = (
                    min(current[0], rank),
                    min(current[1], start),
                    max(current[2], end),
                )
    return windows


def select_sources(
    sources: Dict[str, SourceFile],
    snapshot_files: Sequence[dict],
    findings: Sequence[Finding],
    limits: ScanLimits,
) -> SelectionResult:
    """Select a deterministic, bounded subset of the frozen snapshot."""
    coverage = AiScanCoverage(
        maxFiles=limits.max_files,
        maxBytes=limits.max_bytes,
        maxObservations=limits.max_observations,
        filesTotal=len(snapshot_files),
    )
    result = SelectionResult(files=[], coverage=coverage)

    failed: List[AiScanCoverageIssue] = []
    candidates: List[Tuple[int, str]] = []
    for path, source in sources.items():
        if not _allowed_path(path):
            continue
        if source.decode_error:
            failed.append(AiScanCoverageIssue(
                path=path, code="decode_error", detail=str(source.decode_error)[:200],
            ))
            continue
        if source.size_bytes > limits.per_file_bytes * 40:
            # A huge generated/minified file is poor AI context under a small budget.
            failed.append(AiScanCoverageIssue(
                path=path, code="file_too_large", detail=f"{source.size_bytes} bytes",
            ))
            continue
        candidates.append((_source_priority(path), path))

    det_windows = _deterministic_windows(findings)
    for path in det_windows:
        if path in sources and _allowed_path(path):
            candidates.append((0, path))
    candidates = [(rank, path) for rank, path in candidates]
    best_rank: Dict[str, int] = {}
    for rank, path in candidates:
        best_rank[path] = min(rank, best_rank.get(path, rank))
    candidates = sorted(
        ((rank, path) for path, rank in best_rank.items()),
        key=lambda item: (item[0], item[1]),
    )

    omitted: List[AiScanCoverageIssue] = []
    omitted_count = 0
    truncated: List[str] = []
    total_bytes = 0
    used_refs: Set[str] = set()

    for rank, path in candidates:
        if len(result.files) >= limits.max_files:
            omitted_count += 1
            if len(omitted) < 20:
                omitted.append(AiScanCoverageIssue(path=path, code="max_files", detail=""))
            continue
        source = sources[path]
        lines = source.text.split("\n")
        line_count = max(1, len(lines))
        det = det_windows.get(path)
        if det is not None:
            start_line = min(det[1], line_count)
            end_line = min(det[2], line_count)
        else:
            start_line = 1
            end_line = min(line_count, 220)

        if start_line > line_count:
            failed.append(AiScanCoverageIssue(path=path, code="line_out_of_range", detail=""))
            continue
        raw_window = _line_window(source, start_line, end_line)
        masked = _mask_excerpt(raw_window)
        remaining = limits.max_bytes - total_bytes
        if remaining <= 0:
            omitted_count += 1
            if len(omitted) < 20:
                omitted.append(AiScanCoverageIssue(path=path, code="max_bytes", detail=""))
            continue
        excerpt, was_truncated = _truncate_to_line(masked, min(limits.per_file_bytes, remaining))
        if not excerpt:
            failed.append(AiScanCoverageIssue(
                path=path, code="line_too_long", detail="no newline inside per-file budget",
            ))
            continue

        consumed_lines = excerpt.count("\n") + (0 if excerpt.endswith("\n") else 1)
        if consumed_lines <= 0:
            failed.append(AiScanCoverageIssue(path=path, code="empty_excerpt", detail=""))
            continue
        sent_end_line = start_line + consumed_lines - 1
        ref_index = 1
        while f"sf_{ref_index}" in used_refs:
            ref_index += 1
        file_ref = f"sf_{ref_index}"
        used_refs.add(file_ref)
        result.files.append(SelectedFile(
            file_ref=file_ref,
            path=path,
            source=source,
            start_line=start_line,
            end_line=sent_end_line,
            excerpt=excerpt,
            truncated=was_truncated,
        ))
        total_bytes += len(excerpt.encode("utf-8"))
        if was_truncated:
            truncated.append(path)

    coverage.filesSelected = len(result.files)
    coverage.filesSent = len(result.files)
    coverage.bytesSelected = total_bytes
    coverage.bytesSent = total_bytes
    coverage.truncatedFiles = truncated
    coverage.omittedCount = omitted_count
    coverage.omitted = omitted
    coverage.failedCount = len(failed)
    coverage.failed = failed[:20]
    if omitted_count or truncated:
        coverage.limitsReached.append("selection_budget")
    if len(result.files) == 0:
        coverage.status = "degraded"
    elif omitted_count or truncated or failed:
        coverage.status = "partial"
    else:
        coverage.status = "complete"
    return result


def payload_for_model(selection: SelectionResult) -> dict:
    """Serialize only what the model needs; no filesystem paths for outputs."""
    return {
        "task": "find_additional_vulnerabilities",
        "outputLanguage": "ru",
        "ruleClasses": [
            "SQL_INJECTION", "PATH_TRAVERSAL", "COMMAND_INJECTION", "IDOR",
            "SSRF", "CRYPTO", "TLS", "CORS", "OPEN_ENDPOINT",
        ],
        "files": [
            {
                "fileRef": item.file_ref,
                "path": item.path,
                "startLine": item.start_line,
                "endLine": item.end_line,
                "truncated": item.truncated,
                "excerpt": item.excerpt,
            }
            for item in selection.files
        ],
        "knownFindings": [],
    }