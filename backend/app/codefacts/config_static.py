"""Статическая проверка runtime-конфигурации проекта.

Читает только текстовые конфиги frozen snapshot: ``application*``,
``bootstrap*``, ``.env*``, конфигурационные XML, Dockerfile и compose.
Никакого исполнения и никакой подстановки значений окружения: решение
принимается только по тексту файла.

Находки уходят тем же потоком, что и остальные codefacts-findings
(``extract_facts`` -> ``merge_extra_findings`` -> отчёт/policy и карта),
поэтому это не map-only слой.
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

# Паттерны конфигов, которые читаем статически.
_CONFIG_SUFFIXES = (".yml", ".yaml", ".properties", ".xml", ".env", ".conf", ".cfg", ".ini", ".toml")
_CONFIG_BASENAMES = {"dockerfile", "containerfile"}
_COMPOSE_PREFIXES = ("docker-compose", "compose", "dockercompose")

# Actuator-эндпоинты, открытие которых раскрывает окружение/кучу/bean-контекст.
_DANGEROUS_ACTUATOR = {
    "env", "heapdump", "configprops", "beans", "threaddump", "logfile",
    "jolokia", "shutdown", "mappings", "trace", "httptrace", "scheduledtasks",
}

# Значения, которые считаются дефолтными/placeholder-кредами.
_DEFAULT_CREDENTIALS = {
    "changeme", "change-me", "change_me", "replaceme", "password", "passwd",
    "admin", "administrator", "root", "guest", "test", "default", "secret",
    "123456", "12345678", "qwerty", "letmein", "welcome", "toor", "dummy",
    "placeholder", "xxx", "none", "null", "todo",
}

_CREDENTIAL_KEY_RE = re.compile(
    r"(?i)\b(?:password|passwd|pwd|passphrase|secret|token|api[_-]?key|apikey|"
    r"credential|private[_-]?key|access[_-]?key|client[_-]?secret)\b"
)
# ``${VAR}`` без значения по умолчанию: проверить статически невозможно.
_UNRESOLVED_PLACEHOLDER_RE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$")
_PLACEHOLDER_VALUE_RE = re.compile(r"^<[^>]*>$")

_COMMENT_RE = re.compile(r"^\s*(?:#|//|--|;)")


def is_config_path(path: str) -> bool:
    """True, если файл относится к статически проверяемой конфигурации."""
    name = path.rsplit("/", 1)[-1]
    lowered = name.lower()
    if lowered in _CONFIG_BASENAMES or lowered.startswith("dockerfile"):
        return True
    if lowered.startswith(_COMPOSE_PREFIXES) and lowered.endswith((".yml", ".yaml")):
        return True
    if lowered.startswith(".env"):
        return True
    if lowered.startswith(("application", "bootstrap")) and lowered.endswith(
        (".yml", ".yaml", ".properties")
    ):
        return True
    if lowered.endswith((".xml", ".conf", ".cfg", ".ini", ".toml")):
        # Обычные XML/ini в корне (pom.xml, logback.xml) тоже конфигурация.
        return True
    if lowered.endswith((".yml", ".yaml", ".properties")):
        # Прочие yaml/properties — конфигурация, только если лежат в
        # resources/config/etc либо названы как конфиг приложения.
        parts = [part.lower() for part in path.split("/")[:-1]]
        return bool({"resources", "config", "conf", "etc", "deploy"} & set(parts))
    return False


def _split_kv(stripped: str) -> Tuple[str, str]:
    if "=" in stripped:
        left, right = stripped.split("=", 1)
    elif ":" in stripped:
        left, right = stripped.split(":", 1)
    else:
        return "", ""
    key = left.strip().strip("\"'").split()[-1] if left.strip() else ""
    value = right.strip().strip(",")
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return key, value


class _KeyPaths:
    """Собирает полный путь ключа в yaml по отступам.

    В ``key=value``-файлах (properties, env, Dockerfile) ключ уже dotted,
    поэтому отступы там не накапливаются.
    """

    def __init__(self, dotted: bool) -> None:
        self._stack: List[Tuple[int, str]] = []
        self._dotted = dotted

    def push(self, line: str) -> Tuple[str, str]:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" \t"))
        key, value = _split_kv(stripped)
        if not key:
            return "", ""
        if self._dotted:
            return key, value
        while self._stack and self._stack[-1][0] >= indent:
            self._stack.pop()
        path = ".".join([part for _, part in self._stack] + [key])
        if not value:
            self._stack.append((indent, key))
        return path, value


def _values(value: str) -> List[str]:
    return [item.strip().strip("\"'[]") for item in re.split(r"[,\s]+", value) if item.strip()]


def _finding(rule_id: str, severity: Severity, basis: str, confidence_basis: str,
             source: SourceFile, line: int, snippet: str) -> Finding:
    evidence = make_evidence(source, line, line, kind="source")
    location = Location(
        path=source.path,
        startLine=evidence["location"]["line"],
        endLine=evidence["location"]["endLine"],
        startColumn=evidence["location"]["column"],
        endColumn=evidence["location"]["endColumn"],
        byteRange=[evidence["location"]["byteStart"], evidence["location"]["byteEnd"]],
    )
    fingerprint = __import__("hashlib").sha256(
        f"cf:{rule_id}:{source.path}:{line}:{snippet}".encode("utf-8")
    ).hexdigest()
    return Finding(
        id=f"fnd_cf_{fingerprint[:16]}",
        fingerprint=fingerprint,
        analyzer="codefacts",
        ruleId=rule_id,
        ruleVersion="1.0.0",
        severity=severity,
        severityBasis=basis,
        confidence=Confidence.CONFIRMED,
        confidenceBasis=confidence_basis,
        status=FindingStatus.CONFIRMED,
        primaryLocation=location,
        redactedEvidence=mask_text(snippet),
        advisoryIds=[],
        coverageWarnings=[
            "Статическая проверка текста конфигурации; фактические значения "
            "окружения и применение конфига не исполнялись."
        ],
    )


def scan_config_file(source: SourceFile) -> Tuple[List[Finding], List[str]]:
    """Проверяет один конфиг: находки + причины, которые нельзя разрешить."""
    findings: List[Finding] = []
    skipped: List[str] = []
    if source.decode_error:
        skipped.append(
            f"{source.path} · CF-CONFIG-STATIC: partial — {source.decode_error}"
        )
        return findings, skipped

    allow_credentials = False
    lines = source.text.split("\n")
    dotted = not source.path.lower().endswith((".yml", ".yaml"))
    paths = _KeyPaths(dotted=dotted)
    for number, raw in enumerate(lines, start=1):
        if _COMMENT_RE.match(raw):
            continue
        key, value = paths.push(raw)
        if not key:
            continue
        lowered_key = key.lower()
        lowered_value = value.lower()

        if "allow-credentials" in lowered_key and lowered_value in {"true", "yes", "1"}:
            allow_credentials = True

        # 1. Открытый actuator.
        if lowered_key.endswith("management.endpoints.web.exposure.include"):
            exposed = set(_values(value))
            danger = exposed & _DANGEROUS_ACTUATOR
            if "*" in exposed or danger:
                shown = ", ".join(sorted(danger)) if danger else "*"
                findings.append(_finding(
                    "CF-CONFIG-ACTUATOR-EXPOSED", Severity.HIGH,
                    f"management.endpoints.web.exposure.include открывает: {shown}.",
                    "Значение задано прямо в конфигурации и не зависит от окружения.",
                    source, number, raw,
                ))
        match = re.match(r"(?i)^management\.endpoint\.([a-z]+)\.enabled$", lowered_key)
        if match and match.group(1) in _DANGEROUS_ACTUATOR and lowered_value in {"true", "yes", "1"}:
            findings.append(_finding(
                "CF-CONFIG-ACTUATOR-EXPOSED", Severity.HIGH,
                f"management.endpoint.{match.group(1)}.enabled=true.",
                "Значение задано прямо в конфигурации и не зависит от окружения.",
                source, number, raw,
            ))

        # 2. debug/trace.
        if lowered_key in {"debug", "trace", "server.debug", "server.trace",
                           "management.trace.enabled"} and lowered_value in {"true", "yes", "1"}:
            findings.append(_finding(
                "CF-CONFIG-DEBUG-ENABLED", Severity.MEDIUM,
                f"{key}={value} включает отладочные/трассировочные выходы.",
                "Значение задано прямо в конфигурации и не зависит от окружения.",
                source, number, raw,
            ))

        # 3. Отключённый SSL/TLS.
        if lowered_key.endswith("ssl.enabled") and lowered_value in {"false", "no", "0"}:
            findings.append(_finding(
                "CF-CONFIG-SSL-DISABLED", Severity.MEDIUM,
                f"{key}={value} отключает TLS.",
                "Значение задано прямо в конфигурации и не зависит от окружения.",
                source, number, raw,
            ))
        if lowered_key in {"insecure", "verify_ssl", "verify-ssl", "ssl_verify",
                           "tls_verify", "node_tls_reject_unauthorized"} and \
                lowered_value in {"false", "no", "0", "never"}:
            findings.append(_finding(
                "CF-CONFIG-SSL-DISABLED", Severity.MEDIUM,
                f"{key}={value} отключает проверку TLS-сертификатов.",
                "Значение задано прямо в конфигурации и не зависит от окружения.",
                source, number, raw,
            ))
        if re.search(r"(?i)\bcurl\b[^\n]*\s(?:-k|--insecure)\b", raw) or \
                re.search(r"(?i)wget\b[^\n]*--no-check-certificate", raw):
            findings.append(_finding(
                "CF-CONFIG-SSL-DISABLED", Severity.MEDIUM,
                "Команда загрузки отключает проверку TLS-сертификатов.",
                "Флаг присутствует в тексте конфигурации/скрипта сборки.",
                source, number, raw,
            ))

        # 4. Security выключен.
        if lowered_key in {"management.security.enabled", "security.basic.enabled",
                           "security.oauth2.client.enabled"} and \
                lowered_value in {"false", "no", "0"}:
            findings.append(_finding(
                "CF-CONFIG-SECURITY-DISABLED", Severity.HIGH,
                f"{key}={value} отключает проверку доступа.",
                "Значение задано прямо в конфигурации и не зависит от окружения.",
                source, number, raw,
            ))
        if lowered_key.endswith("spring.autoconfigure.exclude") and \
                "securityautoconfiguration" in lowered_value.replace(" ", ""):
            findings.append(_finding(
                "CF-CONFIG-SECURITY-DISABLED", Severity.HIGH,
                "Spring Security исключён из автонастройки.",
                "Исключение перечислено прямо в конфигурации.",
                source, number, raw,
            ))

        # 5. Дефолтные/placeholder креды.
        if _CREDENTIAL_KEY_RE.search(key):
            normalized = lowered_value.strip()
            if normalized in _DEFAULT_CREDENTIALS or _PLACEHOLDER_VALUE_RE.match(value):
                findings.append(_finding(
                    "CF-CONFIG-DEFAULT-CREDS", Severity.HIGH,
                    f"Ключ '{key}' содержит дефолтное/placeholder-значение.",
                    "Значение распознано как известный дефолт или placeholder.",
                    source, number, raw,
                ))
            elif _UNRESOLVED_PLACEHOLDER_RE.match(value):
                skipped.append(
                    f"{source.path}:{number} · CF-CONFIG-STATIC: skipped — "
                    f"unresolved security-relevant placeholder ${{{value[2:-1]}}}"
                )

    # 6. Wildcard CORS (с учётом allow-credentials из того же файла).
    cors_paths = _KeyPaths(dotted=dotted)
    for number, raw in enumerate(lines, start=1):
        if _COMMENT_RE.match(raw):
            continue
        key, value = cors_paths.push(raw)
        if not key:
            continue
        lowered_key = key.lower()
        if not re.search(r"(?i)allowed[-_]?origins?|allowed[-_]?origin[-_]?patterns?", lowered_key):
            continue
        if "*" not in _values(value) and "*" not in value:
            continue
        severity = Severity.HIGH if allow_credentials else Severity.MEDIUM
        basis = "Wildcard CORS в сочетании с передачей учётных данных." \
            if allow_credentials else "Wildcard CORS без учётных данных."
        findings.append(_finding(
            "CF-CONFIG-CORS-WILDCARD", severity, basis,
            "Значение задано прямо в конфигурации и не зависит от окружения.",
            source, number, raw,
        ))

    return findings, skipped


def scan_configs(sources: Dict[str, SourceFile]) -> Tuple[List[Finding], List[str], List[str]]:
    """Проверяет все конфиги snapshot.

    Возвращает (находки, пути конфигов, причины skipped).
    """
    findings: List[Finding] = []
    skipped: List[str] = []
    paths: List[str] = []
    for path in sorted(sources):
        if not is_config_path(path):
            continue
        paths.append(path)
        file_findings, file_skipped = scan_config_file(sources[path])
        findings.extend(file_findings)
        skipped.extend(file_skipped)
    return findings, paths, skipped
