"""Small Russian-language normalization for report prose.

Scanner DTOs keep technical identifiers; only display text is adjusted here.
"""

from __future__ import annotations

import re

_REPLACEMENTS = (
    (re.compile(r"\bTaint-анализ\b", re.IGNORECASE), "анализ потока данных"),
    (re.compile(r"\bTaint-путь\b", re.IGNORECASE), "путь данных"),
    (re.compile(r"\btaint-путь\b", re.IGNORECASE), "путь данных"),
    (re.compile(r"\bSQL sink\b", re.IGNORECASE), "SQL-запрос"),
    (re.compile(r"\bsink\b", re.IGNORECASE), "точка выполнения"),
    (re.compile(r"\bscopes\b", re.IGNORECASE), "области"),
    (re.compile(r"\bAI_SCAN\b", re.IGNORECASE), "AI-сканер"),
    (re.compile(r"\bredacted\b", re.IGNORECASE), "с удалёнными секретами"),
    (re.compile(r"\badvisories\b", re.IGNORECASE), "записи безопасности"),
    (re.compile(r"runtime-configuration", re.IGNORECASE), "конфигурация времени выполнения"),
    (re.compile(r"runtime-guards", re.IGNORECASE), "защита во время выполнения"),
    (re.compile(r"binaries-and-archives", re.IGNORECASE), "бинарники и архивы"),
    (re.compile(r"dependency-resolution", re.IGNORECASE), "разрешение зависимостей"),
    (re.compile(r"generated-code", re.IGNORECASE), "сгенерированный код"),
)


def naturalize(text: str) -> str:
    value = str(text or "")
    for pattern, replacement in _REPLACEMENTS:
        value = pattern.sub(replacement, value)
    return value
