"""Small Russian-language normalization for report prose.

Scanner DTOs keep technical identifiers; only display text is adjusted here.
"""

from __future__ import annotations

import re

_REPLACEMENTS = (
    (re.compile(r"\bTaint-анализ\b", re.IGNORECASE), "анализ потока данных"),
    (re.compile(r"\bTaint-путь\b", re.IGNORECASE), "путь данных"),
    (re.compile(r"\btaint-путь\b", re.IGNORECASE), "путь данных"),
    (re.compile(r"\bscopes\b", re.IGNORECASE), "области"),
    (re.compile(r"\bAI_SCAN\b", re.IGNORECASE), "ИИ-сканер"),
    (re.compile(r"\bAI\b", re.IGNORECASE), "ИИ"),
    (re.compile(r"redacted-фрагмент", re.IGNORECASE), "фрагмент с удалёнными секретами"),
    (re.compile(r"\bredacted\b", re.IGNORECASE), "с удалёнными секретами"),
    (re.compile(r"\badvisories\b", re.IGNORECASE), "записи безопасности"),
    (re.compile(r"runtime-configuration", re.IGNORECASE), "конфигурация времени выполнения"),
    (re.compile(r"runtime-guards", re.IGNORECASE), "защита во время выполнения"),
    (re.compile(r"binaries-and-archives", re.IGNORECASE), "бинарники и архивы"),
    (re.compile(r"dependency-resolution", re.IGNORECASE), "разрешение зависимостей"),
    (re.compile(r"generated-code", re.IGNORECASE), "сгенерированный код"),
)


_PROTECTED_RE = re.compile(
    r"https?://[^\s<>\"']+"
    r"|`[^`]*`"
    r"|\b(?:CVE-\d{4}-\d{4,7}|GHSA-[A-Za-z0-9-]+"
    r"|(?:KOTLIN|SECRET|SCA|CF)-[A-Z0-9_-]+"
    r"|fnd_[A-Za-z0-9_]+|aiobs_[A-Za-z0-9_-]+)\b",
    re.IGNORECASE,
)


def _replace_prose(value: str) -> str:
    for pattern, replacement in _REPLACEMENTS:
        value = pattern.sub(replacement, value)
    return value


def naturalize(text: str) -> str:
    """Replace prose terms without touching URLs, IDs or quoted code."""
    value = str(text or "")
    parts = []
    position = 0
    for match in _PROTECTED_RE.finditer(value):
        parts.append(_replace_prose(value[position:match.start()]))
        parts.append(match.group(0))
        position = match.end()
    parts.append(_replace_prose(value[position:]))
    return "".join(parts)
