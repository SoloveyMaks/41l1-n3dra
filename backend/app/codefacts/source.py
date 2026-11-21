"""Чтение frozen snapshot, masked-фрагменты и точные координаты source.

Код из архива никогда не исполняется: здесь только чтение байтов и текста.
Координаты считаются по тому же рецепту, что и `docs/data-contracts/validate-bundle.mjs`:
line/column в Unicode code points (1-based), byte-смещения в UTF-8.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from backend.app.codefacts.identity import sha256_hex
from backend.app.security.redaction import mask_uri_credentials

TEXT_EXTENSIONS = {
    ".kt", ".kts", ".java", ".properties", ".yaml", ".yml", ".json", ".env",
    ".xml", ".conf", ".sql", ".txt", ".md", ".toml", ".gradle", ".cfg", ".ini",
    ".pem", ".key", ".crt", ".cer", ".env",
    ".html", ".css", ".js", ".ts", ".tsx", ".jsx",
}

MAX_SNIPPET_CHARS = 600
REDACTION_VERSION = "cg-redaction/2"

_PEM_RE = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.DOTALL,
)
_SECRET_ASSIGN_RE = re.compile(
    r"(?i)(?P<key>\b[\w.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|client[_-]?secret|"
    r"private[_-]?key|access[_-]?key)[\w.-]*(?:[\"'])?\s*(?::\s*[\w.<>?]+\s*)?[:=]\s*)"
    r"(?P<quote>[\"']?)(?P<value>[^\s\"',;]+)"
)
_ENV_DEFAULT_RE = re.compile(
    r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*):(?P<value>[^}]*)\}"
)
_BEARER_RE = re.compile(r"(?i)(Authorization\s*[:=]\s*[\"']?Bearer\s+)([A-Za-z0-9._~+/=-]{12,})")
_LONG_TOKEN_RE = re.compile(r"\b(?=[A-Za-z0-9+/_=-]*\d)(?=[A-Za-z0-9+/_=-]*[A-Za-z])[A-Za-z0-9+/_=-]{32,}\b")
_CONTROL_RE = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]")

_SOURCE_PATH_RE = re.compile(
    r"^(?!/)(?!.*\\)(?!.*(?:^|/)\.{1,2}(?:/|$))(?!.*//)(?![A-Za-z]:)"
    r"(?!.*[\u0000-\u001f\u007f])(?!.*\/$).+$"
)


class SnapshotIntegrityError(RuntimeError):
    """Файл на диске больше не совпадает с frozen manifest."""


def is_safe_source_path(path: str) -> bool:
    return bool(isinstance(path, str) and _SOURCE_PATH_RE.match(path))


def mask_token(value: str) -> str:
    value = value.strip()
    if len(value) <= 4:
        return "****"
    return f"{value[:2]}****{value[-2:]}"


def mask_text(text: str) -> str:
    """Убирает секреты из фрагмента, сохраняя структуру кода для UI."""
    if not text:
        return ""
    text = _PEM_RE.sub("[REDACTED PRIVATE KEY]", text)
    text = mask_uri_credentials(text)
    text = re.sub(
        r'''(?i)(\b[\w.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|private[_-]?key|access[_-]?key)[\w.-]*(?:["'])?\s*(?::\s*[\w.<>?]+\s*)?[:=]\s*)(["'])([^\r\n]*?)\2''',
        lambda m: f"{m.group(1)}{m.group(2)}{mask_token(m.group(3))}{m.group(2)}",
        text,
    )
    text = _SECRET_ASSIGN_RE.sub(
        lambda m: f"{m.group('key')}{m.group('quote')}{mask_token(m.group('value'))}",
        text,
    )
    text = _ENV_DEFAULT_RE.sub(
        lambda m: "${" + m.group("name") + ":****}",
        text,
    )
    text = _BEARER_RE.sub(lambda m: f"{m.group(1)}{mask_token(m.group(2))}", text)
    text = _LONG_TOKEN_RE.sub(lambda m: mask_token(m.group(0)), text)
    text = _CONTROL_RE.sub(" ", text)
    if len(text) > MAX_SNIPPET_CHARS:
        text = text[:MAX_SNIPPET_CHARS] + " …"
    return text


@dataclass
class SourceFile:
    path: str
    text: str
    raw: bytes
    sha256: str
    size_bytes: int
    decode_error: Optional[str] = None

    @property
    def lines(self) -> List[str]:
        return self.text.split("\n")


def decode_source(raw: bytes) -> Tuple[str, Optional[str]]:
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError as exc:
        return raw.decode("utf-8", errors="replace"), f"utf-8 decode error: {exc}"


def is_text_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    if name.startswith(".env"):
        return True
    suffix = Path(name).suffix.lower()
    return suffix in TEXT_EXTENSIONS or name in {
        "gradlew", "gradlew.bat", "Dockerfile", "Makefile",
    }


def load_snapshot_sources(artifact_root: Path, snapshot: Dict[str, Any]) -> Tuple[Dict[str, SourceFile], List[Dict[str, str]]]:
    """Читает файлы snapshot и проверяет полные хэши. Не доверяет диску."""
    root = Path(artifact_root).resolve()
    sources: Dict[str, SourceFile] = {}
    diagnostics: List[Dict[str, str]] = []
    for item in snapshot.get("files", []):
        rel = str(item.get("path", ""))
        expected = str(item.get("sha256", ""))
        if not is_safe_source_path(rel):
            diagnostics.append({"code": "CF-UNSAFE_PATH", "message": f"Недопустимый путь в snapshot: {rel!r}"})
            continue
        if not is_text_path(rel):
            continue
        target = (root / rel).resolve()
        if root != target and root not in target.parents:
            diagnostics.append({"code": "CF-UNSAFE_PATH", "message": f"Путь выходит за snapshot: {rel}"})
            continue
        if not target.is_file():
            raise SnapshotIntegrityError(f"Файл snapshot отсутствует: {rel}")
        raw = target.read_bytes()
        digest = sha256_hex(raw)
        if digest != expected:
            raise SnapshotIntegrityError(
                f"Файл snapshot изменён после приёма: {rel} (hash mismatch)"
            )
        text, decode_error = decode_source(raw)
        sources[rel] = SourceFile(
            path=rel, text=text, raw=raw, sha256=digest,
            size_bytes=len(raw), decode_error=decode_error,
        )
    return sources, diagnostics


def line_points(text: str, line: int) -> List[str]:
    """Code points строки без завершающего \\r, как в JS-валидаторе."""
    if line < 1:
        raise ValueError("line must be >= 1")
    pos = 0
    for _ in range(1, line):
        nxt = text.find("\n", pos)
        if nxt == -1:
            raise ValueError(f"line {line} beyond EOF")
        pos = nxt + 1
    end = text.find("\n", pos)
    row = text[pos:] if end == -1 else text[pos:end]
    if end != -1 and row.endswith("\r"):
        row = row[:-1]
    return list(row)


def line_start_offset(text: str, line: int) -> int:
    pos = 0
    for _ in range(1, line):
        nxt = text.find("\n", pos)
        if nxt == -1:
            raise ValueError(f"line {line} beyond EOF")
        pos = nxt + 1
    return pos


def offset_at(text: str, line: int, column: int) -> int:
    pos = line_start_offset(text, line)
    points = line_points(text, line)
    col = max(1, min(int(column), len(points) + 1))
    return len(text[:pos].encode("utf-8")) + len("".join(points[: col - 1]).encode("utf-8"))


def make_location(source: SourceFile, start_line: int, end_line: Optional[int] = None,
                  start_column: int = 1, end_column: Optional[int] = None) -> Dict[str, Any]:
    text = source.text
    start_line = max(1, int(start_line))
    end_line = max(start_line, int(end_line if end_line is not None else start_line))
    line_count = text.count("\n") + 1
    if start_line > line_count:
        start_line = line_count
    if end_line > line_count:
        end_line = line_count
    if end_column is None:
        end_column = len(line_points(text, end_line)) + 1
    start_column = max(1, min(int(start_column), len(line_points(text, start_line)) + 1))
    end_column = max(1, min(int(end_column), len(line_points(text, end_line)) + 1))
    return {
        "path": source.path,
        "line": start_line,
        "column": start_column,
        "endLine": end_line,
        "endColumn": end_column,
        "byteStart": offset_at(text, start_line, start_column),
        "byteEnd": offset_at(text, end_line, end_column),
    }


def line_text(source: SourceFile, line: int) -> str:
    return "".join(line_points(source.text, line))


def line_range_text(source: SourceFile, start_line: int, end_line: int) -> str:
    start_line = max(1, int(start_line))
    end_line = max(start_line, int(end_line))
    lines = source.text.split("\n")
    chunk = "\n".join(lines[start_line - 1:end_line])
    return chunk.rstrip("\r")


def make_evidence(source: SourceFile, start_line: int, end_line: Optional[int] = None,
                  kind: str = "source", snippet: Optional[str] = None,
                  start_column: int = 1, end_column: Optional[int] = None) -> Dict[str, Any]:
    """Черновик evidence без UUID: его выдаст canonical builder."""
    location = make_location(source, start_line, end_line, start_column, end_column)
    if snippet is None:
        snippet = line_range_text(source, location["line"], location["endLine"])
    return {
        "kind": kind,
        "location": location,
        "redactedSnippet": mask_text(snippet),
        "sourceHash": source.sha256,
        "producerVersion": "cg-codefacts/1.0.0",
        "path": source.path,
    }


def normalize_snippet(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def context_hash(category: str, rule_id: str, path: str, line: int, snippet: str) -> str:
    payload = f"{category}|{rule_id}|{path}|{line}|{normalize_snippet(snippet)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def hmac_evidence(secret: bytes, *parts: str) -> str:
    import hmac
    message = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def canonical_list(values: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    result: List[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
