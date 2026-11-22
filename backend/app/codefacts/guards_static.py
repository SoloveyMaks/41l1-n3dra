"""Статическая проверка method security (runtime-guards).

Ищет в исходниках ``@PreAuthorize``/``@PostAuthorize``/``@PreFilter``/
``@PostFilter``/``@Secured``/``@RolesAllowed`` и сверяет их с объявлением
включения: ``@EnableMethodSecurity`` или
``@EnableGlobalMethodSecurity(prePostEnabled = true)``.

Если аннотации есть, а включения нет — это реальный обход проверки
(``CF-GUARD-NOT-ENABLED``). Если включение объявлено — scope
``CF-GUARD-STATIC`` помечается checked с честной причиной: объявление
проверено статически, само runtime enforcement не исполнялось.

Правила включения считались по документации Spring Security:
``@EnableMethodSecurity`` включает pre/post по умолчанию (prePostEnabled=true),
``securedEnabled``/``jsr250Enabled`` по умолчанию выключены;
``@EnableGlobalMethodSecurity`` по умолчанию выключает всё.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from backend.app.codefacts.source import SourceFile, make_evidence, mask_text
from backend.app.contracts.schemas import (
    Confidence,
    Finding,
    FindingStatus,
    Location,
    Severity,
)

GUARD_SCOPE_RULE = "CF-GUARD-STATIC"
GUARD_FINDING_RULE = "CF-GUARD-NOT-ENABLED"
GUARD_CHECKED_REASON = "Объявление проверено статически, runtime enforcement не исполнялся."

# (аннотация, какой флаг включения нужен, какие аннотации требуют pre/post)
_PRE_ANNOTATIONS = {"PreAuthorize", "PostAuthorize", "PreFilter", "PostFilter"}
_ANNOTATION_RE = re.compile(
    r"@(PreAuthorize|PostAuthorize|PreFilter|PostFilter|Secured|RolesAllowed)\b"
)
_ENABLE_METHOD_SECURITY_RE = re.compile(r"@EnableMethodSecurity\b")
_ENABLE_GLOBAL_RE = re.compile(r"@EnableGlobalMethodSecurity\s*\((.*?)\)", re.DOTALL)
_FLAG_RE = {
    "prePostEnabled": re.compile(r"prePostEnabled\s*=\s*(true|false)", re.IGNORECASE),
    "securedEnabled": re.compile(r"securedEnabled\s*=\s*(true|false)", re.IGNORECASE),
    "jsr250Enabled": re.compile(r"jsr250Enabled\s*=\s*(true|false)", re.IGNORECASE),
}


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset)) + 1


def _enabled_flags(text: str) -> Dict[str, bool]:
    """Какие семейства method security реально включены в проекте."""
    flags = {"prePostEnabled": False, "securedEnabled": False, "jsr250Enabled": False}
    if _ENABLE_METHOD_SECURITY_RE.search(text):
        # @EnableMethodSecurity: prePostEnabled=true по умолчанию.
        flags["prePostEnabled"] = True
    for match in _ENABLE_GLOBAL_RE.finditer(text):
        args = match.group(1)
        for name, pattern in _FLAG_RE.items():
            found = pattern.search(args)
            if found:
                flags[name] = found.group(1).lower() == "true"
    return flags


def _required_flag(annotation: str) -> str:
    if annotation in _PRE_ANNOTATIONS:
        return "prePostEnabled"
    if annotation == "Secured":
        return "securedEnabled"
    return "jsr250Enabled"


def _finding(source: SourceFile, line: int, annotation: str, flag: str) -> Finding:
    evidence = make_evidence(source, line, line, kind="source")
    loc = evidence["location"]
    snippet = mask_text(evidence["redactedSnippet"])
    fingerprint = __import__("hashlib").sha256(
        f"cf:{GUARD_FINDING_RULE}:{source.path}:{line}:{annotation}".encode("utf-8")
    ).hexdigest()
    return Finding(
        id=f"fnd_cf_{fingerprint[:16]}",
        fingerprint=fingerprint,
        analyzer="codefacts",
        ruleId=GUARD_FINDING_RULE,
        ruleVersion="1.0.0",
        severity=Severity.HIGH,
        severityBasis=(
            f"Метод помечен @{annotation}, но {flag} не включён: нет ни "
            "@EnableMethodSecurity, ни @EnableGlobalMethodSecurity с этим флагом. "
            "Проверка доступа на уровне метода не применяется."
        ),
        confidence=Confidence.CONFIRMED,
        confidenceBasis=(
            "Аннотация проверки и отсутствие объявления включения найдены "
            "статически во всех исходниках проекта."
        ),
        status=FindingStatus.CONFIRMED,
        primaryLocation=Location(
            path=source.path,
            startLine=loc["line"],
            endLine=loc["endLine"],
            startColumn=loc["column"],
            endColumn=loc["endColumn"],
            byteRange=[loc["byteStart"], loc["byteEnd"]],
        ),
        redactedEvidence=snippet,
        advisoryIds=[],
        coverageWarnings=[
            "Статическая проверка объявлений method security в исходниках "
            "проекта; XML-конфигурация и runtime-включение не исполнялись."
        ],
    )


def scan_guards(sources: Dict[str, SourceFile]) -> Tuple[List[Finding], List[Dict[str, Any]]]:
    """Возвращает (находки, scope-записи CF-GUARD-STATIC)."""
    code = {
        path: source
        for path, source in sorted(sources.items())
        if path.endswith((".kt", ".java"))
    }
    if not code:
        return [], []

    enabled = {"prePostEnabled": False, "securedEnabled": False, "jsr250Enabled": False}
    for source in code.values():
        for name, value in _enabled_flags(source.text).items():
            enabled[name] = enabled[name] or value

    findings: List[Finding] = []
    scopes: List[Dict[str, Any]] = []
    for path, source in code.items():
        if source.decode_error:
            continue
        hits: List[Tuple[str, int]] = []
        for match in _ANNOTATION_RE.finditer(source.text):
            hits.append((match.group(1), _line_of(source.text, match.start())))
        if not hits:
            continue
        missing = sorted({
            annotation for annotation, _ in hits
            if not enabled[_required_flag(annotation)]
        })
        for annotation, line in hits:
            flag = _required_flag(annotation)
            if not enabled[flag]:
                findings.append(_finding(source, line, annotation, flag))
        if missing:
            reason = (
                f"Аннотации {', '.join('@' + item for item in missing)} найдены, "
                f"объявление включения не найдено; runtime enforcement не исполнялся."
            )
        else:
            reason = GUARD_CHECKED_REASON
        scopes.append({
            "path": path,
            "ruleId": GUARD_SCOPE_RULE,
            "category": "SAST",
            "status": "checked",
            "reason": reason,
        })
    return findings, scopes
