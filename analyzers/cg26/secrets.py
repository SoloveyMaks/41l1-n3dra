"""SECRET analyzer.

Detects five classes of embedded credentials without ever exposing raw values:

* ``password``              - a literal password in code
* ``api-key``               - a literal API key / token in code
* ``configuration-token``   - a literal token in YAML/properties
* ``secret-fallback``       - ``getenv(...) ?: "<literal>"`` or ``${VAR:default}``
* ``recoverable-token``     - a token folded from several local literals

Raw values stay inside this module.  Findings and evidence only carry a mask
and the value length.
"""

from __future__ import annotations

import re

from .kotlin_lexer import Token, skip_newlines
from .model import Diagnostic, Finding, RuleInfo
from .project import Project, Property
from .yaml_lite import parse_properties, parse_yaml

RULES = [
    RuleInfo("CG26-SEC-001", "SECRET", "1", "Встроенный пароль в коде", "password"),
    RuleInfo("CG26-SEC-002", "SECRET", "1", "Встроенный API-ключ или токен в коде", "api-key"),
    RuleInfo("CG26-SEC-003", "SECRET", "1", "Встроенный токен в конфигурации", "configuration-token"),
    RuleInfo("CG26-SEC-004", "SECRET", "1", "Встроенный fallback после чтения окружения", "secret-fallback"),
    RuleInfo("CG26-SEC-005", "SECRET", "1", "Токен, восстанавливаемый из нескольких литералов", "recoverable-token"),
]

CREDENTIAL_SINKS = {
    "setBearerAuth",
    "setBasicAuth",
    "setRequestProperty",
    "setHeader",
    "addHeader",
    "header",
    "mapOf",
    "set",
}

CREDENTIAL_HEADERS = {
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "api-key",
    "x-auth-token",
    "x-import-password",
    "x-access-token",
}

_PLACEHOLDER_VALUES = {
    "changeme",
    "change-me",
    "change_me",
    "replaceme",
    "replace-me",
    "password",
    "passwd",
    "secret",
    "token",
    "apikey",
    "api_key",
    "api-key",
    "none",
    "null",
    "todo",
    "example",
    "sample",
    "dummy",
    "placeholder",
    "redacted",
    "xxx",
    "test",
}

_PASSWORD_NAME = re.compile(r"(?i)(password|passwd|pwd|passphrase)")
_API_KEY_NAME = re.compile(r"(?i)(api[_-]?key|apikey|access[_-]?key|client[_-]?key|secret[_-]?key)")
_TOKEN_NAME = re.compile(r"(?i)(token|secret|credential|bearer)")

_ENV_REF = re.compile(r"^\$\{[^:}]+\}$")
_ENV_WITH_DEFAULT = re.compile(r"^\$\{([^:}]+):(.*)\}$")
_BEARER = re.compile(r"(?i)^(bearer|basic|token)\s+(\S{6,})$")
_CONFIG_CREDENTIAL_KEY = re.compile(
    r"(?i)(password|passwd|pwd|passphrase|token|secret|api[_-]?key|apikey|"
    r"credential|authorization|auth[_-]?key|access[_-]?key|client[_-]?key|private[_-]?key)"
)


def _clean(tokens: list[Token]) -> list[Token]:
    return [token for token in tokens if token.kind not in {"NL", "COMMENT"}]


def is_placeholder(value: str) -> bool:
    text = value.strip()
    if not text:
        return True
    if _ENV_REF.match(text):
        return True
    if re.fullmatch(r"<[^>]*>", text):
        return True
    if re.match(r"(?i)^(your|my)[_-]", text):
        return True
    if text.lower() in _PLACEHOLDER_VALUES:
        return True
    if re.fullmatch(r"[x*.\-_]{4,}", text, re.IGNORECASE):
        return True
    if re.search(r"(?i)(placeholder|redacted|replace[_-]?me|change[_-]?me)", text):
        return True
    return False


def looks_like_secret(value: str, min_length: int = 8) -> bool:
    text = value.strip()
    if not text or is_placeholder(text):
        return False
    if text.startswith(("http://", "https://", "jdbc:", "file:", "classpath:", "mongodb:")):
        return False
    if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", text):
        return False
    if re.fullmatch(r"\d+(\.\d+)*", text):
        return False
    if len(text) < min_length:
        return False
    if re.fullmatch(r"[A-Za-z_.\-]+", text) and len(text) < 16:
        return False
    digits = sum(char.isdigit() for char in text)
    symbols = sum((not char.isalnum()) and char not in "_-." for char in text)
    return digits >= 1 or symbols >= 1 or len(text) >= 16


def _credential_value(value: str) -> str | None:
    """Return the secret part of a header value, or None."""

    text = value.strip()
    match = _BEARER.match(text)
    if match:
        candidate = match.group(2)
        return candidate if looks_like_secret(candidate) else None
    return text if looks_like_secret(text) else None


def _masked(value: str) -> str:
    return "*" * min(12, max(6, len(value) // 2))


def _classify_name(name: str) -> tuple[str, str, str] | None:
    if _PASSWORD_NAME.search(name):
        return (
            "password",
            "HIGH",
            "Встроенный пароль находится в исходном коде и доступен всем, кто читает репозиторий.",
        )
    if _API_KEY_NAME.search(name):
        return (
            "api-key",
            "MEDIUM",
            "Встроенный API-ключ находится в исходном коде и может быть извлечён из репозитория.",
        )
    if _TOKEN_NAME.search(name):
        return (
            "api-key",
            "MEDIUM",
            "Встроенный токен находится в исходном коде и может быть извлечён из репозитория.",
        )
    return None


def _arg_mentions(call, name: str) -> bool:
    return any(token.kind == "IDENT" and token.text == name for argument in call.args for token in argument)


def _find_credential_sink(project: Project, prop: Property):
    for file in project.files.values():
        if file.rel_path != prop.file:
            continue
        for function in file.functions:
            for call in function.calls:
                if call.name not in CREDENTIAL_SINKS:
                    continue
                if _arg_mentions(call, prop.name):
                    return call
    return None


def _elvis_literals(
    project: Project,
    tokens: list[Token],
    file: str,
    func: str | None,
    cls: str | None,
) -> list[tuple[str, int, str]]:
    """Find literal values on the right side of ``?:`` after an env lookup."""

    results: list[tuple[str, int, str]] = []
    clean = _clean(tokens)
    for index, token in enumerate(clean):
        if not token.is_op("?:"):
            continue
        left = clean[max(0, index - 14) : index]
        if not any(
            item.kind == "IDENT" and item.text in {"getenv", "getProperty", "getPropertyOrNull"}
            for item in left
        ):
            continue
        cursor = index + 1
        while cursor < len(clean) and clean[cursor].kind == "NL":
            cursor += 1
        if cursor >= len(clean):
            continue
        right = clean[cursor]
        if right.kind == "STRING" and right.value:
            results.append((right.value, right.line, "literal"))
            continue
        if right.kind == "IDENT":
            prop = project.resolve_property(right.text, file, func, cls)
            if prop is None:
                continue
            folded = project.fold(prop.expr, prop.file, prop.func, prop.owner)
            if folded.pure and folded.value:
                results.append((folded.value, prop.line, "property"))
    return results


def analyze(project: Project) -> tuple[list[Finding], list[Diagnostic]]:
    findings: list[Finding] = []
    diagnostics: list[Diagnostic] = []
    consumed: set[int] = set()

    # 1. Recoverable tokens: several local literals folded into a credential.
    for prop in project.properties:
        clean = _clean(prop.expr)
        if len(clean) < 3:
            continue
        folded = project.fold(prop.expr, prop.file, prop.func, prop.owner)
        if not (folded.pure and folded.value):
            continue
        composite = len(folded.parts) >= 2 or folded.literal_parts >= 2
        if not composite:
            continue
        if not looks_like_secret(folded.value, min_length=10):
            continue
        sink = _find_credential_sink(project, prop)
        if sink is None:
            continue
        start = min([prop.line] + [part.line for part in folded.parts])
        end = max([prop.end_line, sink.end_line] + [part.end_line for part in folded.parts])
        findings.append(
            Finding(
                category="SECRET",
                path=prop.file,
                class_code="recoverable-token",
                line=start,
                end_line=end,
                severity="MEDIUM",
                severity_basis="Токен восстанавливается из исходников и передаётся в credential-заголовок.",
                description=(
                    f"Токен собирается из {len(folded.parts) or folded.literal_parts} строковых "
                    f"фрагментов и передаётся в защищённый заголовок через `{sink.name}`. "
                    "Значения фрагментов и итоговый токен скрыты."
                ),
                remediation=(
                    "Хранить цельный токен в переменной окружения или хранилище секретов; "
                    "не собирать credential из литералов в коде."
                ),
                rule_id="CG26-SEC-005",
                confidence="confirmed",
                confidence_basis="Все фрагменты найдены как литералы, поток до заголовка прослежен.",
                evidence={
                    "declaration": {"path": prop.file, "line": prop.line, "endLine": prop.end_line},
                    "parts": [{"path": part.file, "line": part.line} for part in folded.parts],
                    "sink": {"name": sink.name, "line": sink.line, "endLine": sink.end_line},
                    "maskedValue": _masked(folded.value),
                    "valueLength": len(folded.value),
                },
            )
        )
        consumed.add(id(prop))
        for part in folded.parts:
            consumed.add(id(part))

    # 2. Embedded fallback after an environment lookup.
    for prop in project.properties:
        for value, line, source in _elvis_literals(project, prop.expr, prop.file, prop.func, prop.owner):
            if not looks_like_secret(value):
                continue
            findings.append(
                Finding(
                    category="SECRET",
                    path=prop.file,
                    class_code="secret-fallback",
                    line=line,
                    end_line=line,
                    severity="LOW",
                    severity_basis="Секрет используется только при отсутствии переменной окружения.",
                    description=(
                        "Если переменная окружения не задана, используется встроенный секрет "
                        "по умолчанию. Значение скрыто."
                    ),
                    remediation="Удалить fallback: при отсутствии переменной отключать интеграцию.",
                    rule_id="CG26-SEC-004",
                    confidence="confirmed",
                    confidence_basis="Правая часть Elvis-выражения после чтения окружения является литералом.",
                    evidence={
                        "declaration": {"path": prop.file, "line": prop.line},
                        "fallback": {"line": line, "source": source, "maskedValue": _masked(value)},
                    },
                )
            )
            consumed.add(id(prop))

    for function in project.functions:
        for value, line, source in _elvis_literals(
            project, function.body, function.file, function.name, function.owner
        ):
            if not looks_like_secret(value):
                continue
            findings.append(
                Finding(
                    category="SECRET",
                    path=function.file,
                    class_code="secret-fallback",
                    line=line,
                    end_line=line,
                    severity="LOW",
                    severity_basis="Секрет используется только при отсутствии переменной окружения.",
                    description="Встроенный секрет используется как fallback после чтения окружения. Значение скрыто.",
                    remediation="Удалить fallback и требовать явную настройку окружения.",
                    rule_id="CG26-SEC-004",
                    evidence={"fallback": {"line": line, "source": source, "maskedValue": _masked(value)}},
                )
            )

    # 3. Direct literals in code.
    for prop in project.properties:
        if id(prop) in consumed:
            continue
        folded = project.fold(prop.expr, prop.file, prop.func, prop.owner)
        if not (folded.pure and folded.value):
            continue
        role = _classify_name(prop.name)
        if role is None:
            continue
        class_code, severity, description = role
        if not looks_like_secret(folded.value):
            continue
        sink = _find_credential_sink(project, prop)
        sink_text = ""
        if sink is not None:
            header = sink.string_arg(0)
            if header:
                sink_text = f" Значение передаётся в заголовок `{header}`."
        findings.append(
            Finding(
                category="SECRET",
                path=prop.file,
                class_code=class_code,
                line=prop.line,
                end_line=prop.end_line,
                severity=severity,
                severity_basis=description,
                description=f"`{prop.name}` содержит встроенный секрет.{sink_text} Значение скрыто.",
                remediation=(
                    "Читать значение из переменной окружения или хранилища секретов; "
                    "не хранить литерал в репозитории."
                ),
                rule_id="CG26-SEC-001" if class_code == "password" else "CG26-SEC-002",
                confidence="confirmed",
                confidence_basis="Литерал присвоен параметру с явной ролью секрета.",
                evidence={
                    "declaration": {"path": prop.file, "line": prop.line, "endLine": prop.end_line},
                    "name": prop.name,
                    "maskedValue": _masked(folded.value),
                    "valueLength": len(folded.value),
                    "sink": (
                        {"name": sink.name, "line": sink.line, "endLine": sink.end_line} if sink else None
                    ),
                },
            )
        )

    # 4. Configuration files: YAML and properties.
    for rel_path, text_file in project.text_files.items():
        suffix = "." + rel_path.rsplit(".", 1)[-1].lower() if "." in rel_path else ""
        if suffix in {".yml", ".yaml"}:
            entries = parse_yaml(text_file.text)
        elif suffix == ".properties":
            entries = parse_properties(text_file.text)
        else:
            continue
        for entry in entries:
            value = entry.value.strip()
            if not value:
                continue
            if not _CONFIG_CREDENTIAL_KEY.search(entry.key):
                continue
            default_match = _ENV_WITH_DEFAULT.match(value)
            if default_match:
                default = default_match.group(2).strip()
                if looks_like_secret(default):
                    findings.append(
                        Finding(
                            category="SECRET",
                            path=rel_path,
                            class_code="secret-fallback",
                            line=entry.line,
                            end_line=entry.line,
                            severity="LOW",
                            severity_basis="В конфигурации остаётся встроенное значение по умолчанию.",
                            description=(
                                f"`{entry.dotted}` использует встроенное значение по умолчанию "
                                "для переменной окружения. Значение скрыто."
                            ),
                            remediation="Убрать значение по умолчанию и требовать явную настройку.",
                            rule_id="CG26-SEC-004",
                            evidence={
                                "property": entry.dotted,
                                "maskedValue": _masked(default),
                            },
                        )
                    )
                continue
            if _ENV_REF.match(value):
                continue
            secret = _credential_value(value)
            if secret is None:
                continue
            findings.append(
                Finding(
                    category="SECRET",
                    path=rel_path,
                    class_code="configuration-token",
                    line=entry.line,
                    end_line=entry.line,
                    severity="HIGH",
                    severity_basis="Токен хранится в конфигурации репозитория открытым текстом.",
                    description=(
                        f"`{entry.dotted}` содержит встроенный токен авторизации. Значение скрыто."
                    ),
                    remediation=(
                        "Задать свойство через переменную окружения без встроенного значения "
                        "и собирать заголовок на границе адаптера."
                    ),
                    rule_id="CG26-SEC-003",
                    confidence="confirmed",
                    confidence_basis="Значение является литералом, а не ссылкой на переменную окружения.",
                    evidence={
                        "property": entry.dotted,
                        "maskedValue": _masked(secret),
                        "valueLength": len(secret),
                    },
                )
            )

    return findings, diagnostics
