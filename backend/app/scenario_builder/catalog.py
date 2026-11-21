"""Closed intent catalog for the scenario builder.

The model never selects NodeKind, ports or config directly. It may only return
these intent names; the server maps them through NODE_REGISTRY and builds edges.
"""

from __future__ import annotations

from typing import Dict, Tuple

INTENT_ALIASES: Dict[str, str] = {
    "secrets": "secrets",
    "secret": "secrets",
    "secret_scan": "secrets",
    "secret_scanner": "secrets",
    "секреты": "secrets",
    "full": "full",
    "all": "full",
    "полный": "full",
    "полная": "full",
    "полный_анализ": "full",
    "секрет": "secrets",
    "dependencies": "dependencies",
    "dependency": "dependencies",
    "sca": "dependencies",
    "sca_scan": "dependencies",
    "зависимости": "dependencies",
    "sast": "sast",
    "kotlin_sast": "sast",
    "code": "sast",
    "код": "sast",
    "ai_scan": "ai_scan",
    "ai": "ai_scan",
    "ии": "ai_scan",
    "ai_scan_hypotheses": "ai_scan",
    "ai_explain": "ai_explain",
    "explain": "ai_explain",
    "объяснение": "ai_explain",
    "report": "report",
    "reports": "report",
    "отчёт": "report",
    "отчет": "report",
    "notify": "notify",
    "notification": "notify",
    "webhook": "notify",
    "telegram": "notify",
    "уведомление": "notify",
    "schedule": "schedule",
    "scheduled": "schedule",
    "cron": "schedule",
    "расписание": "schedule",
}

KNOWN_INTENTS: Tuple[str, ...] = (
    "secrets",
    "dependencies",
    "sast",
    "ai_scan",
    "ai_explain",
    "report",
    "notify",
    "schedule",
)

DEFAULT_INTENTS: Tuple[str, ...] = ("secrets", "dependencies", "sast", "report")

INTENT_TITLES = {
    "secrets": "секреты",
    "dependencies": "зависимости",
    "sast": "Kotlin SAST",
    "ai_scan": "ИИ-анализ исходников (гипотезы)",
    "ai_explain": "ИИ-объяснение готовых находок",
    "report": "отчёт",
    "notify": "уведомление",
    "schedule": "расписание",
}

MAX_NODES = 12
MAX_EDGES = 16
MAX_TITLE_CHARS = 80
MAX_PROMPT_CHARS = 2000
MIN_PROMPT_CHARS = 8
