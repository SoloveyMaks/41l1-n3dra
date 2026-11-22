"""Безопасное статическое чтение архивов и бинарных файлов snapshot.

Архивы (``.zip/.jar/.war/.aar``) читаются только на чтение, без исполнения и
без распаковки на диск. Защита:

* symlink-файлы и symlink-entry не читаются;
* zip-slip (абсолютные пути, ``..``, backslash, control-символы) отбрасывается;
* лимиты: размер архива, число entry, размер entry, суммарный распакованный
  объём, коэффициент сжатия, глубина 1 (вложенные архивы не раскрываются);
* зашифрованный, слишком большой или неподдерживаемый архив получает
  явный skipped-с reason, а не общую фразу.

Текстовые entry проверяются существующими шаблонами: опасные конфиги —
``config_static``, секреты — регулярки redaction-модуля. Находки якорятся на
самом файле архива в манифесте (byte 0..0), entry и строка внутри entry
указаны в severityBasis/trace.

Бинарные файлы (картинки и прочее) получают scope ``not_applicable`` с
причиной: это не «непроверенный код».
"""

from __future__ import annotations

import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from backend.app.codefacts.config_static import is_config_path, scan_config_file
from backend.app.codefacts.source import (
    SourceFile,
    _PEM_RE,
    _SECRET_ASSIGN_RE,
    is_text_path,
    mask_text,
)
from backend.app.contracts.schemas import (
    Confidence,
    Finding,
    FindingStatus,
    Location,
    Severity,
)

ARCHIVE_SUFFIXES = (".zip", ".jar", ".war", ".aar")

MAX_ARCHIVE_BYTES = int(os.getenv("CG_MAX_NESTED_ARCHIVE_BYTES", str(64 * 1024 * 1024)))
MAX_ENTRIES = int(os.getenv("CG_MAX_ARCHIVE_ENTRIES", "5000"))
MAX_ENTRY_BYTES = int(os.getenv("CG_MAX_ARCHIVE_ENTRY_BYTES", str(4 * 1024 * 1024)))
MAX_TOTAL_BYTES = int(os.getenv("CG_MAX_ARCHIVE_TOTAL_BYTES", str(128 * 1024 * 1024)))
MAX_COMPRESSION_RATIO = int(os.getenv("CG_MAX_ARCHIVE_RATIO", "200"))

_SUPPORTED_METHODS = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA}
_IFMT = 0o170000
_IFLNK = 0o120000

_UNSAFE_ENTRY_RE = re.compile(r"^(?:/|[A-Za-z]:)|\\\\|[\x00-\x1f\x7f]|\.\.")

_SECRET_KEY_RE = re.compile(
    r"(?i)\b(?:password|passwd|pwd|passphrase|secret|token|api[_-]?key|apikey|"
    r"credential|private[_-]?key|access[_-]?key|client[_-]?secret)\b"
)
_ENV_REF_RE = re.compile(r"^\$\{[^}]+\}$|^\$[A-Za-z_][A-Za-z0-9_]*$")
_PLACEHOLDER_RE = re.compile(
    r"(?i)^(?:changeme|change[-_]me|replaceme|password|passwd|admin|root|guest|"
    r"test|default|secret|placeholder|dummy|xxx|none|null|todo|<[^>]*>)$"
)


def is_archive_path(path: str) -> bool:
    return path.lower().endswith(ARCHIVE_SUFFIXES)


@dataclass
class ArchiveOutcome:
    """Результат обработки одного архива."""

    findings: List[Finding] = field(default_factory=list)
    scope: Optional[Dict[str, Any]] = None
    skipped: Optional[str] = None
    text_entries: int = 0


def _finding(rule_id: str, severity: Severity, basis: str, confidence_basis: str,
             archive_path: str, entry: str, line: int, snippet: str) -> Finding:
    """Находка, якоренная на файле архива в манифесте."""
    fingerprint = __import__("hashlib").sha256(
        f"cf:{rule_id}:{archive_path}:{entry}:{line}:{snippet}".encode("utf-8")
    ).hexdigest()
    where = f"{entry}:{line}" if line > 0 else entry
    return Finding(
        id=f"fnd_cf_{fingerprint[:16]}",
        fingerprint=fingerprint,
        analyzer="codefacts",
        ruleId=rule_id,
        ruleVersion="1.0.0",
        severity=severity,
        severityBasis=f"Внутри архива {archive_path}, entry {where}: {basis}",
        confidence=Confidence.CONFIRMED,
        confidenceBasis=confidence_basis,
        status=FindingStatus.CONFIRMED,
        primaryLocation=Location(
            path=archive_path, startLine=1, endLine=1,
            startColumn=1, endColumn=1, byteRange=[0, 0],
        ),
        redactedEvidence=mask_text(snippet),
        advisoryIds=[],
        coverageWarnings=[
            "Архив прочитан статически, содержимое не исполнялось; "
            "entry указан в описании находки."
        ],
    )


def _secret_findings(archive_path: str, entry: str, text: str) -> List[Finding]:
    findings: List[Finding] = []
    for number, raw in enumerate(text.split("\n"), start=1):
        if re.match(r"^\s*(?:#|//|;)", raw):
            continue
        match = _SECRET_ASSIGN_RE.search(raw)
        if not match:
            continue
        key = match.group("key")
        value = (match.group("value") or "").strip().strip("\"'")
        if not _SECRET_KEY_RE.search(key) or len(value) < 6:
            continue
        if _ENV_REF_RE.match(value) or _PLACEHOLDER_RE.match(value):
            continue
        if value.startswith(("http://", "https://", "jdbc:", "file:", "classpath:")):
            continue
        findings.append(_finding(
            "CF-ARCHIVE-SECRET", Severity.HIGH,
            f"ключ '{key}' содержит литеральный секрет.",
            "Литерал в credential-ключе распознан существующим шаблоном "
            "redaction и не является placeholder/ссылкой на окружение.",
            archive_path, entry, number, raw.strip(),
        ))
    if _PEM_RE.search(text):
        findings.append(_finding(
            "CF-ARCHIVE-SECRET", Severity.HIGH,
            "entry содержит приватный ключ в формате PEM.",
            "Заголовок PEM-блока найден существующим шаблоном redaction.",
            archive_path, entry, 1, "-----BEGIN ... PRIVATE KEY-----",
        ))
    return findings


def _config_findings(archive_path: str, entry: str, text: str) -> List[Finding]:
    if not is_config_path(entry):
        return []
    probe = SourceFile(path=entry, text=text, raw=text.encode("utf-8"),
                       sha256="", size_bytes=len(text.encode("utf-8")))
    findings, _skipped = scan_config_file(probe)
    result: List[Finding] = []
    for finding in findings:
        line = finding.primaryLocation.startLine
        snippet = finding.redactedEvidence
        # Локализация внутри entry не соответствует байтам архива в манифесте,
        # поэтому якорь переносится на файл архива, а entry/строка остаются
        # в описании.
        result.append(_finding(
            finding.ruleId, finding.severity, finding.severityBasis,
            finding.confidenceBasis, archive_path, entry, line, snippet,
        ))
    return result


def _entry_is_symlink(info: zipfile.ZipInfo) -> bool:
    return ((info.external_attr >> 16) & _IFMT) == _IFLNK


def scan_archive(archive_path: Path, rel_path: str) -> ArchiveOutcome:
    """Читает один архив без распаковки на диск. Никакого исполнения."""
    outcome = ArchiveOutcome()

    def skip(reason: str) -> ArchiveOutcome:
        outcome.skipped = f"{rel_path} · CF-ARCHIVE-STATIC: skipped — {reason}"
        outcome.scope = None
        return outcome

    try:
        if archive_path.is_symlink():
            return skip("symlink")
        size = archive_path.stat().st_size
    except OSError as exc:
        return skip(f"stat failed: {exc}")
    if size > MAX_ARCHIVE_BYTES:
        return skip(f"too-large: {size} bytes > {MAX_ARCHIVE_BYTES}")

    try:
        handle = archive_path.open("rb")
    except OSError as exc:
        return skip(f"read failed: {exc}")

    total = 0
    entries = 0
    text_entries = 0
    file_entries = 0
    processed = 0
    blocked: Dict[str, int] = {}
    with handle:
        try:
            zf = zipfile.ZipFile(handle)
        except zipfile.BadZipFile:
            return skip("unsupported: not a valid zip/jar/war/aar")
        except RuntimeError as exc:
            return skip(f"unsupported: {exc}")
        with zf:
            for info in zf.infolist():
                entries += 1
                if entries > MAX_ENTRIES:
                    return skip(f"too-many-entries: > {MAX_ENTRIES}")
                name = info.filename
                if info.is_dir():
                    continue
                file_entries += 1
                if _UNSAFE_ENTRY_RE.search(name) or "\\" in name:
                    # zip-slip отсечён намеренно: это обработанный исход,
                    # а не невозможность прочитать архив.
                    processed += 1
                    continue
                if _entry_is_symlink(info):
                    processed += 1  # symlink отсечён намеренно
                    continue
                if info.flag_bits & 0x1:
                    blocked["encrypted"] = blocked.get("encrypted", 0) + 1
                    continue  # encrypted entry
                if info.compress_type not in _SUPPORTED_METHODS:
                    blocked["unsupported"] = blocked.get("unsupported", 0) + 1
                    continue
                if info.file_size > MAX_ENTRY_BYTES:
                    blocked["too-large"] = blocked.get("too-large", 0) + 1
                    continue
                if info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
                    blocked["ratio"] = blocked.get("ratio", 0) + 1
                    continue
                if name.lower().endswith(ARCHIVE_SUFFIXES):
                    processed += 1  # глубина 1: вложенные архивы не раскрываются
                    continue
                total += info.file_size
                if total > MAX_TOTAL_BYTES:
                    return skip(f"too-large: распакованный объём > {MAX_TOTAL_BYTES}")
                processed += 1
                try:
                    with zf.open(info) as entry_handle:
                        raw = entry_handle.read(MAX_ENTRY_BYTES + 1)
                except (RuntimeError, OSError, zipfile.BadZipFile, NotImplementedError):
                    blocked["unreadable"] = blocked.get("unreadable", 0) + 1
                    continue
                if len(raw) > MAX_ENTRY_BYTES or b"\x00" in raw:
                    blocked["binary"] = blocked.get("binary", 0) + 1
                    continue  # бинарный entry
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    blocked["binary"] = blocked.get("binary", 0) + 1
                    continue
                text_entries += 1
                outcome.findings.extend(_config_findings(rel_path, name, text))
                outcome.findings.extend(_secret_findings(rel_path, name, text))

    if file_entries and not processed:
        # Ни один entry не удалось прочитать: это не «проверено», а явный
        # skipped с конкретной причиной.
        if blocked.get("encrypted"):
            reason = f"encrypted: {blocked['encrypted']} entry зашифровано, содержимое не читалось"
        elif blocked.get("unsupported"):
            reason = f"unsupported: метод сжатия не поддерживается ({blocked['unsupported']} entry)"
        elif blocked.get("too-large"):
            reason = f"too-large: entry больше {MAX_ENTRY_BYTES} ({blocked['too-large']} entry)"
        elif blocked.get("ratio"):
            reason = (
                f"too-large: коэффициент сжатия выше {MAX_COMPRESSION_RATIO} "
                f"({blocked['ratio']} entry)"
            )
        else:
            reason = f"unreadable: entry не прочитаны ({blocked.get('unreadable', 0)} entry)"
        return skip(reason)

    outcome.text_entries = text_entries
    encrypted = blocked.get("encrypted", 0)
    extra = f"; зашифровано и не прочитано entry: {encrypted}" if encrypted else ""
    outcome.scope = {
        "path": rel_path,
        "ruleId": "CF-ARCHIVE-STATIC",
        "category": "SAST",
        "status": "checked",
        "reason": (
            f"Архив прочитан без исполнения и без распаковки на диск: "
            f"entry просмотрено {entries}, текстовых проверено {text_entries}; "
            f"вложенные архивы не раскрывались (глубина 1).{extra}"
        ),
    }
    return outcome


def _binary_scope(rel_path: str) -> Dict[str, Any]:
    return {
        "path": rel_path,
        "ruleId": "CF-BINARY-STATIC",
        "category": "SAST",
        "status": "not_applicable",
        "reason": (
            "Бинарный файл (изображение/ресурс): статические правила исходного "
            "кода и конфигурации к нему неприменимы."
        ),
    }


def _unread_text_scope(rel_path: str) -> Dict[str, Any]:
    return {
        "path": rel_path,
        "ruleId": "CF-FILE-STATIC",
        "category": "SAST",
        "status": "not_applicable",
        "reason": (
            "Файл прочитан в snapshot, но не относится к проверяемым областям "
            "(не исходник Spring, не конфигурация, не архив): документация, "
            "сборка, метаданные."
        ),
    }


def scan_archives_and_binaries(
    root: Optional[Path],
    manifest_paths: Iterable[str],
    read_paths: Iterable[str],
) -> Tuple[List[Finding], List[Dict[str, Any]], List[str]]:
    """Архивы, бинарники и прочитанные, но неподчинённые правилам файлы.

    Возвращает (находки, scope-записи, skipped-причины).
    """
    findings: List[Finding] = []
    scopes: List[Dict[str, Any]] = []
    skipped: List[str] = []
    if root is None:
        return findings, scopes, skipped

    root = Path(root)
    read = set(read_paths)
    seen: set[str] = set()
    for rel in sorted({str(item) for item in manifest_paths}):
        if not rel or rel in seen:
            continue
        seen.add(rel)
        if is_archive_path(rel):
            target = root / rel
            if not target.is_file():
                skipped.append(f"{rel} · CF-ARCHIVE-STATIC: skipped — файл отсутствует")
                continue
            outcome = scan_archive(target, rel)
            if outcome.skipped:
                skipped.append(outcome.skipped)
                if outcome.scope:
                    scopes.append(outcome.scope)
                continue
            findings.extend(outcome.findings)
            if outcome.scope:
                scopes.append(outcome.scope)
            continue
        if not is_text_path(rel):
            scopes.append(_binary_scope(rel))
            continue
        if rel not in read:
            scopes.append(_unread_text_scope(rel))
    return findings, scopes, skipped
