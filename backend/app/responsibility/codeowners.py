"""Bounded CODEOWNERS reading and matching from the immutable snapshot.

No regular expressions and no host filesystem discovery: the matcher is a
polynomial wildcard DP with hard pattern/path/entry limits. Unsupported syntax
or a truncated rule set never yields a confident owner.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.app.codefacts.source import is_safe_source_path
from backend.app.responsibility.models import CodeownersInfo

MAX_CODEOWNERS_BYTES = 128 * 1024
MAX_ENTRIES = 400
MAX_PATTERN_LEN = 256
MAX_PATH_LEN = 1024
_UNSUPPORTED_PATTERN_CHARS = set("[]{}()\\|!")


@dataclass(frozen=True)
class Rule:
    pattern: str
    owners: Tuple[str, ...]
    line: int


def _tokens(pattern: str) -> List[str]:
    tokens: List[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            tokens.append("**/")
            index += 3
        elif pattern.startswith("**", index):
            tokens.append("**")
            index += 2
        elif pattern[index] == "*":
            tokens.append("*")
            index += 1
        elif pattern[index] == "?":
            tokens.append("?")
            index += 1
        else:
            tokens.append(pattern[index])
            index += 1
    return tokens


def _normalise_pattern(raw: str) -> Optional[List[str]]:
    pattern = raw.strip()
    if not pattern or len(pattern) > MAX_PATTERN_LEN:
        return None
    if any(char in _UNSUPPORTED_PATTERN_CHARS for char in pattern):
        return None
    anchored = pattern.startswith("/") or "/" in pattern
    directory = pattern.endswith("/")
    pattern = pattern.strip("/")
    if not pattern:
        return None
    if not anchored and not pattern.startswith("**"):
        pattern = f"**/{pattern}"
    if directory:
        pattern = f"{pattern}/**"
    return _tokens(pattern)


def _matches(pattern: str, path: str) -> bool:
    if not path or len(path) > MAX_PATH_LEN:
        return False
    tokens = _normalise_pattern(pattern)
    if tokens is None:
        return False
    # 2D DP from the end. Each state is O(1), including *, ** and **/.
    width = len(path)
    previous = [False] * (width + 1)
    previous[width] = True
    for token in reversed(tokens):
        current = [False] * (width + 1)
        for index in range(width, -1, -1):
            if token == "**":
                current[index] = previous[index] or (
                    index < width and current[index + 1]
                )
            elif token == "**/":
                if previous[index]:
                    current[index] = True
                elif index < width:
                    if path[index] == "/":
                        current[index] = previous[index + 1] or current[index + 1]
                    else:
                        current[index] = current[index + 1]
            elif token == "*":
                current[index] = previous[index] or (
                    index < width and path[index] != "/" and current[index + 1]
                )
            elif token == "?":
                current[index] = (
                    index < width and path[index] != "/" and previous[index + 1]
                )
            else:
                current[index] = (
                    index < width and path[index] == token and previous[index + 1]
                )
        previous = current
    return previous[0]


def _read_file(root: Path, relative: str, expected_sha: str) -> Optional[str]:
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        return None
    if not target.is_file():
        return None
    raw = target.read_bytes()
    if len(raw) > MAX_CODEOWNERS_BYTES:
        return None
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        return None
    return raw.decode("utf-8", errors="replace")


def _parse_rules(text: str) -> Tuple[List[Rule], Optional[str], bool]:
    """Return (rules, unsupported_reason, incomplete)."""
    rules: List[Rule] = []
    incomplete = False
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) < 1:
            continue
        pattern = parts[0]
        owners = tuple(
            part for part in parts[1:]
            if part and not part.startswith(("#", "!"))
        )
        if len(pattern) > MAX_PATTERN_LEN or any(
            char in _UNSUPPORTED_PATTERN_CHARS for char in pattern
        ):
            return rules, f"Неподдерживаемый pattern в строке {line_number}; owner не подтверждён.", True
        if len(rules) >= MAX_ENTRIES:
            incomplete = True
            continue
        rules.append(Rule(pattern=pattern, owners=owners, line=line_number))
    return rules, None, incomplete


def _load_snapshot_codeowners(
    store: Any,
    workspace: str,
    artifact_row: Optional[Dict[str, Any]],
    meta: Optional[Dict[str, Any]],
) -> Tuple[Optional[str], Optional[str], Optional[str], bool]:
    """Return (relative_file, text, unsupported_reason, incomplete)."""
    if not meta or not meta.get("snapshotId"):
        return None, None, "Снимок проекта недоступен, CODEOWNERS не проверялся.", False
    if not artifact_row or not artifact_row.get("extracted_path"):
        return None, None, "Распакованный снимок недоступен, CODEOWNERS не проверялся.", False
    try:
        snapshot = store.load_snapshot(workspace, str(meta["snapshotId"]))
    except (ValueError, OSError):
        return None, None, "Manifest снимка не найден, CODEOWNERS не проверялся.", False
    if not snapshot or not isinstance(snapshot.get("files"), list):
        return None, None, "Manifest снимка не найден, CODEOWNERS не проверялся.", False
    by_path = {
        str(item.get("path")): str(item.get("sha256"))
        for item in snapshot["files"]
        if isinstance(item, dict) and item.get("path") and item.get("sha256")
    }
    for candidate in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"):
        if candidate in by_path:
            if not is_safe_source_path(candidate):
                return candidate, None, "Путь CODEOWNERS не прошёл проверку снимка.", False
            try:
                text = _read_file(
                    Path(str(artifact_row["extracted_path"])),
                    candidate,
                    by_path[candidate],
                )
            except OSError:
                text = None
            if text is None:
                return candidate, None, "CODEOWNERS есть в manifest, но не совпал с hash или недоступен.", False
            return candidate, text, None, False
    return None, None, None, False


def load_and_match_codeowners(
    *,
    store: Any,
    workspace: str,
    artifact_row: Optional[Dict[str, Any]],
    meta: Optional[Dict[str, Any]],
    path: str,
) -> CodeownersInfo:
    limitations = [
        "CODEOWNERS читается только из immutable snapshot; отсутствие файла не означает отсутствие владельца.",
        "Поддержан bounded glob subset; regex/unsupported syntax не угадывается.",
    ]
    relative, text, problem, file_incomplete = _load_snapshot_codeowners(
        store, workspace, artifact_row, meta
    )
    if problem:
        return CodeownersInfo(
            status="unavailable",
            file=relative,
            basis=problem,
            limitations=limitations,
        )
    if relative is None or text is None:
        return CodeownersInfo(
            status="absent",
            basis="В снимке нет .github/CODEOWNERS, CODEOWNERS или docs/CODEOWNERS.",
            limitations=limitations,
        )
    rules, unsupported, incomplete = _parse_rules(text)
    if unsupported:
        return CodeownersInfo(
            status="unavailable",
            file=relative,
            basis=unsupported,
            limitations=limitations,
        )
    if incomplete or file_incomplete:
        return CodeownersInfo(
            status="incomplete",
            file=relative,
            basis=f"CODEOWNERS содержит больше {MAX_ENTRIES} правил; более поздние правила не применены, owner не подтверждён.",
            limitations=limitations,
        )
    if not rules:
        return CodeownersInfo(
            status="absent",
            file=relative,
            basis=f"{relative} найден, но не содержит валидных правил.",
            limitations=limitations,
        )
    matched: Optional[Rule] = None
    for rule in rules:
        if _matches(rule.pattern, path):
            matched = rule
    if matched is None:
        return CodeownersInfo(
            status="available",
            file=relative,
            basis=f"CODEOWNERS из снимка: {relative}. Ни одно правило не совпало с путём находки.",
            limitations=limitations,
        )
    if not matched.owners:
        return CodeownersInfo(
            status="available",
            file=relative,
            matchedPattern=matched.pattern,
            line=matched.line,
            owners=[],
            basis=f"CODEOWNERS: последнее правило {matched.pattern} (строка {matched.line}) без owners отменяет fallback.",
            limitations=limitations,
        )
    return CodeownersInfo(
        status="available",
        file=relative,
        matchedPattern=matched.pattern,
        line=matched.line,
        owners=list(matched.owners),
        basis=f"CODEOWNERS из снимка: {relative}. Последнее совпавшее правило: {matched.pattern} (строка {matched.line}).",
        limitations=limitations,
    )
