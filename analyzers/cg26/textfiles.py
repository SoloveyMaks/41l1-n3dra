"""Safe, bounded project file discovery.

Only regular files inside the project root are read.  Symbolic links are not
followed, binary files are skipped, and oversized files are reported as skipped
instead of being loaded into memory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

MAX_FILE_BYTES = 2 * 1024 * 1024

TEXT_SUFFIXES = {
    ".kt",
    ".kts",
    ".java",
    ".groovy",
    ".gradle",
    ".properties",
    ".lockfile",
    ".yml",
    ".yaml",
    ".json",
    ".xml",
    ".conf",
    ".cfg",
    ".ini",
    ".toml",
    ".sql",
    ".env",
    ".txt",
    ".md",
    ".pem",
    ".crt",
    ".key",
    ".sh",
    ".bat",
    ".ps1",
}

# Directories that are never part of the reviewed source tree.
EXCLUDED_DIRS = {
    ".git",
    ".gradle",
    ".idea",
    "build",
    "out",
    "target",
    "node_modules",
    "dist",
    "bin",
    "obj",
    "__pycache__",
    ".venv",
    "venv",
    "ground-truth",
    "vulnerability-database",
}

# The analyzer itself must not be scanned when the repository root is passed.
SELF_DIR_NAMES = {"analyzers"}


@dataclass
class TextFile:
    abs_path: Path
    rel_path: str
    text: str
    encoding: str

    @property
    def lines(self) -> list[str]:
        return self.text.splitlines()


def _is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _decode(raw: bytes) -> tuple[str, str] | None:
    if b"\x00" in raw:
        return None
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return None


def iter_project_files(
    root: str | Path,
    suffixes: set[str] | None = None,
    excluded_dirs: set[str] | None = None,
) -> tuple[list[TextFile], list[dict[str, str]]]:
    """Return readable text files and a list of skipped entries."""

    root_path = Path(root).resolve()
    suffixes = suffixes or TEXT_SUFFIXES
    excluded = EXCLUDED_DIRS | (excluded_dirs or set())
    files: list[TextFile] = []
    skipped: list[dict[str, str]] = []

    for current, dirnames, filenames in os.walk(root_path, followlinks=False):
        current_path = Path(current)
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in excluded and name not in SELF_DIR_NAMES
        )
        for name in sorted(filenames):
            abs_path = current_path / name
            rel_path = abs_path.relative_to(root_path).as_posix()
            if abs_path.is_symlink():
                skipped.append({"path": rel_path, "reason": "symlink"})
                continue
            if not _is_within(root_path, abs_path.resolve()):
                skipped.append({"path": rel_path, "reason": "outside-root"})
                continue
            suffix = abs_path.suffix.lower()
            if suffix not in suffixes and name not in {".env", ".env.local"}:
                skipped.append({"path": rel_path, "reason": "unsupported-suffix"})
                continue
            try:
                size = abs_path.stat().st_size
            except OSError:
                skipped.append({"path": rel_path, "reason": "stat-failed"})
                continue
            if size > MAX_FILE_BYTES:
                skipped.append({"path": rel_path, "reason": "too-large"})
                continue
            try:
                raw = abs_path.read_bytes()
            except OSError:
                skipped.append({"path": rel_path, "reason": "read-failed"})
                continue
            decoded = _decode(raw)
            if decoded is None:
                skipped.append({"path": rel_path, "reason": "binary"})
                continue
            text, encoding = decoded
            files.append(TextFile(abs_path=abs_path, rel_path=rel_path, text=text, encoding=encoding))

    return files, skipped


def read_text(path: str | Path) -> str | None:
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None
    decoded = _decode(raw)
    return decoded[0] if decoded else None
