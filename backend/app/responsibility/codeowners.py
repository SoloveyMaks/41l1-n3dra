"""CODEOWNERS from the same immutable snapshot, never from the host FS root."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.app.codefacts.source import is_safe_source_path
from backend.app.responsibility.models import CodeownersInfo

MAX_CODEOWNERS_BYTES = 128 * 1024
MAX_ENTRIES = 400


def _glob_regex(pattern: str) -> str:
    out: List[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                out.append(".*")
                index += 2
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        index += 1
    return "".join(out)


def _matches(pattern: str, path: str) -> bool:
    raw = pattern.strip()
    if not raw or raw.startswith("#"):
        return False
    directory = raw.endswith("/")
    raw = raw.strip("/")
    if not raw or ".." in raw.split("/"):
        return False
    body = _glob_regex(raw)
    if "/" in raw:
        regex = f"^{body}"
    else:
        regex = f"(^|.*/){body}"
    if directory:
        regex += r"(/.*)?"
    regex += "$"
    return re.match(regex, path) is not None


def _read_codeowners_file(
    root: Path,
    relative: str,
    expected_sha: str,
) -> Optional[str]:
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


def read_codeowners(
    *,
    store: Any,
    workspace: str,
    artifact_row: Optional[Dict[str, Any]],
    meta: Optional[Dict[str, Any]],
) -> CodeownersInfo:
    limitations = [
        "CODEOWNERS читается только из immutable snapshot; отсутствие файла не означает отсутствие владельца.",
    ]
    if not meta or not meta.get("snapshotId"):
        return CodeownersInfo(
            status="unavailable",
            basis="Снимок проекта недоступен, CODEOWNERS не проверялся.",
            limitations=limitations,
        )
    if not artifact_row or not artifact_row.get("extracted_path"):
        return CodeownersInfo(
            status="unavailable",
            basis="Распакованный снимок недоступен, CODEOWNERS не проверялся.",
            limitations=limitations,
        )
    try:
        snapshot = store.load_snapshot(workspace, str(meta["snapshotId"]))
    except (ValueError, OSError):
        snapshot = None
    if not snapshot or not isinstance(snapshot.get("files"), list):
        return CodeownersInfo(
            status="unavailable",
            basis="Manifest снимка не найден, CODEOWNERS не проверялся.",
            limitations=limitations,
        )

    candidates = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")
    by_path = {
        str(item.get("path")): str(item.get("sha256"))
        for item in snapshot["files"]
        if isinstance(item, dict) and item.get("path") and item.get("sha256")
    }
    chosen: Optional[Tuple[str, str]] = None
    for candidate in candidates:
        if candidate in by_path:
            chosen = (candidate, by_path[candidate])
            break
    if chosen is None:
        return CodeownersInfo(
            status="absent",
            basis="В снимке нет .github/CODEOWNERS, CODEOWNERS или docs/CODEOWNERS.",
            limitations=limitations,
        )

    relative, expected_sha = chosen
    if not is_safe_source_path(relative):
        return CodeownersInfo(
            status="unavailable",
            basis="Путь CODEOWNERS не прошёл проверку снимка.",
            limitations=limitations,
        )
    try:
        text = _read_codeowners_file(Path(str(artifact_row["extracted_path"])), relative, expected_sha)
    except OSError:
        text = None
    if text is None:
        return CodeownersInfo(
            status="unavailable",
            file=relative,
            basis="CODEOWNERS есть в manifest, но файл не совпал с hash или недоступен.",
            limitations=limitations,
        )

    entries: List[Tuple[str, List[str], int]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if len(entries) >= MAX_ENTRIES:
            break
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        pattern = parts[0]
        owners = [part for part in parts[1:] if part and not part.startswith("!")]
        if owners:
            entries.append((pattern, owners, line_number))

    if not entries:
        return CodeownersInfo(
            status="absent",
            file=relative,
            basis=f"{relative} найден, но не содержит валидных правил.",
            limitations=limitations,
        )

    return CodeownersInfo(
        status="available",
        file=relative,
        basis=f"CODEOWNERS из снимка: {relative}.",
        limitations=limitations,
    )


def match_codeowners(info: CodeownersInfo, path: str, entries: List[Tuple[str, List[str], int]]) -> CodeownersInfo:
    """Last matching rule wins, matching CODEOWNERS semantics."""
    if info.status != "available":
        return info
    matched = None
    for pattern, owners, line_number in entries:
        if _matches(pattern, path):
            matched = (pattern, owners, line_number)
    if matched is None:
        return info.model_copy(
            update={
                "matchedPattern": None,
                "owners": [],
                "line": None,
                "basis": info.basis + " Ни одно правило не совпало с путём находки.",
            }
        )
    pattern, owners, line_number = matched
    return info.model_copy(
        update={
            "matchedPattern": pattern,
            "owners": owners,
            "line": line_number,
            "basis": f"{info.basis} Последнее совпавшее правило: {pattern} (строка {line_number}).",
        }
    )


def load_and_match_codeowners(
    *,
    store: Any,
    workspace: str,
    artifact_row: Optional[Dict[str, Any]],
    meta: Optional[Dict[str, Any]],
    path: str,
) -> CodeownersInfo:
    info = read_codeowners(store=store, workspace=workspace, artifact_row=artifact_row, meta=meta)
    if info.status != "available" or not info.file:
        return info
    if not meta or not artifact_row:
        return info
    try:
        snapshot = store.load_snapshot(workspace, str(meta["snapshotId"]))
        entry = next(
            item for item in snapshot["files"]
            if item.get("path") == info.file
        )
        text = _read_codeowners_file(
            Path(str(artifact_row["extracted_path"])),
            info.file,
            str(entry["sha256"]),
        )
    except (StopIteration, KeyError, TypeError, ValueError, OSError):
        return info.model_copy(update={"status": "unavailable", "basis": "Не удалось перечитать CODEOWNERS из снимка."})
    if text is None:
        return info.model_copy(update={"status": "unavailable", "basis": "CODEOWNERS не совпал с hash снимка."})
    entries: List[Tuple[str, List[str], int]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) >= 2:
            owners = [part for part in parts[1:] if part and not part.startswith("!")]
            if owners:
                entries.append((parts[0], owners, line_number))
    return match_codeowners(info, path, entries)
