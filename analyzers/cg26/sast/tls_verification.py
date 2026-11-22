"""TLS verification: hostname or certificate verification is disabled.

Two shapes are recognised:

* an unconditional ``HostnameVerifier { _, _ -> true }`` installed on a
  connection that is actually used;
* a trust manager whose ``checkServerTrusted`` / ``checkClientTrusted`` bodies
  accept every certificate, wired into an ``SSLContext``.

A normal ``HttpsURLConnection`` that keeps the default verifier never reports.
"""

from __future__ import annotations

from ..kotlin_lexer import Token, build_matching
from ..model import Diagnostic, Finding, RuleInfo

RULES = [
    RuleInfo(
        "CG26-SAST-002",
        "SAST",
        "1",
        "Отключена проверка имени хоста или цепочки сертификатов TLS",
        "tls-verification",
    )
]

_CONNECTION_HINTS = {
    "HttpsURLConnection",
    "openConnection",
    "SSLContext",
    "SSLSocketFactory",
    "setHostnameVerifier",
    "hostnameVerifier",
}


def _clean(tokens: list[Token]) -> list[Token]:
    return [token for token in tokens if token.kind not in {"NL", "COMMENT"}]


def _unconditional_true(tokens: list[Token]) -> bool:
    clean = _clean(tokens)
    arrow = next((index for index, token in enumerate(clean) if token.is_op("->")), None)
    if arrow is not None:
        clean = clean[arrow + 1 :]
    return len(clean) == 1 and clean[0].is_ident("true")


def _lambda_after(tokens: list[Token], matching: dict[int, int], index: int) -> tuple[bool, int]:
    """Check whether ``{ ... }`` after ``index`` returns an unconditional true."""

    cursor = index
    while cursor < len(tokens) and tokens[cursor].kind in {"NL", "COMMENT"}:
        cursor += 1
    if cursor >= len(tokens) or not tokens[cursor].is_op("{"):
        return False, cursor
    close = matching.get(cursor)
    if close is None:
        return False, cursor
    return _unconditional_true(tokens[cursor + 1 : close]), close


def _has_connection_use(function) -> bool:
    return any(
        token.kind == "IDENT" and token.text in _CONNECTION_HINTS for token in function.body
    )


def _trust_manager_disabled(function, matching) -> tuple[bool, int | None]:
    tokens = function.body
    for index, token in enumerate(tokens):
        if not token.is_ident("checkServerTrusted"):
            continue
        cursor = index + 1
        if cursor < len(tokens) and tokens[cursor].is_op("("):
            close = matching.get(cursor)
            if close is None:
                continue
            cursor = close + 1
        # Skip an explicit return type if present.
        while cursor < len(tokens) and tokens[cursor].kind in {"NL", "COMMENT"}:
            cursor += 1
        if cursor < len(tokens) and tokens[cursor].is_op("{"):
            close = matching.get(cursor)
            if close is None:
                continue
            body = _clean(tokens[cursor + 1 : close])
            if not body or all(item.is_ident("Unit") for item in body):
                return True, token.line
    return False, None


def analyze(project) -> tuple[list[Finding], list[Diagnostic]]:
    findings: list[Finding] = []
    diagnostics: list[Diagnostic] = []

    for function in project.functions:
        if not _has_connection_use(function):
            continue
        tokens = function.body
        matching = build_matching(tokens)
        reported = False

        for index, token in enumerate(tokens):
            if token.kind != "IDENT" or token.text not in {"hostnameVerifier", "setHostnameVerifier", "HostnameVerifier"}:
                continue
            cursor = index
            if token.text in {"hostnameVerifier", "setHostnameVerifier"}:
                cursor = index + 1
                while cursor < len(tokens) and tokens[cursor].kind in {"NL", "COMMENT"}:
                    cursor += 1
                if cursor < len(tokens) and tokens[cursor].is_op("="):
                    cursor += 1
                elif cursor < len(tokens) and tokens[cursor].is_op("("):
                    cursor = cursor + 1
                else:
                    continue
                while cursor < len(tokens) and tokens[cursor].kind in {"NL", "COMMENT"}:
                    cursor += 1
                if cursor >= len(tokens) or not tokens[cursor].is_ident("HostnameVerifier"):
                    continue
            enabled, _ = _lambda_after(tokens, matching, cursor + 1)
            if not enabled:
                continue
            line = token.line if token.text != "HostnameVerifier" else tokens[cursor].line
            findings.append(
                Finding(
                    category="SAST",
                    path=function.file,
                    class_code="tls-verification",
                    line=line,
                    end_line=line,
                    severity="MEDIUM",
                    severity_basis=(
                        "Проверка имени хоста отключена для реально используемого HTTPS-соединения."
                    ),
                    description=(
                        f"`{function.name}` устанавливает `HostnameVerifier`, который принимает любое "
                        "имя хоста (`-> true`). Соединение продолжает использоваться для внешнего запроса."
                    ),
                    remediation=(
                        "Не переопределять `HostnameVerifier`; использовать сертификат, действительный "
                        "для имени хоста."
                    ),
                    rule_id="CG26-SAST-002",
                    confidence="confirmed",
                    confidence_basis=(
                        "Верификатор возвращает безусловный `true`, а в той же функции создаётся HTTPS-соединение."
                    ),
                    evidence={
                        "sink": {"kind": "hostname-verifier", "line": line},
                        "function": {"name": function.name, "line": function.line, "endLine": function.end_line},
                        "verifierReturnsTrue": True,
                    },
                )
            )
            reported = True
            break

        if reported:
            continue

        disabled, line = _trust_manager_disabled(function, matching)
        if disabled and line is not None:
            findings.append(
                Finding(
                    category="SAST",
                    path=function.file,
                    class_code="tls-verification",
                    line=line,
                    end_line=line,
                    severity="MEDIUM",
                    severity_basis="Trust manager принимает любой сертификат.",
                    description=(
                        f"`{function.name}` содержит trust manager, который не проверяет сертификаты "
                        "(`checkServerTrusted`/`checkClientTrusted` пусты)."
                    ),
                    remediation="Использовать системный trust store и штатную проверку цепочки.",
                    rule_id="CG26-SAST-002",
                    confidence="confirmed",
                    confidence_basis="Тело проверки сертификата пустое, trust manager подключён к SSLContext.",
                    evidence={"sink": {"kind": "trust-manager", "line": line}},
                )
            )

    return findings, diagnostics
