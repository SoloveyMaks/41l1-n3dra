"""Credentialed CORS: wildcard origin combined with credentials.

The rule requires both settings on the same ``CorsConfiguration`` object:

* ``allowedOriginPatterns = listOf("*")`` (or ``allowedOrigins`` / setters);
* ``allowCredentials = true``.

When the configuration is registered for a route, the route and handler are
attached as evidence.  A CORS configuration with only one of the two settings
does not report.
"""

from __future__ import annotations

from ..kotlin_lexer import Token, build_matching, find_statement_end
from ..model import Diagnostic, Finding, RuleInfo

RULES = [
    RuleInfo(
        "CG26-SAST-003",
        "SAST",
        "1",
        "Credentialed CORS разрешён для любого origin",
        "credentialed-cors",
    )
]

_ORIGIN_KEYS = {"allowedOriginPatterns", "allowedOrigins", "setAllowedOriginPatterns", "setAllowedOrigins"}
_CREDENTIAL_KEYS = {"allowCredentials", "setAllowCredentials"}


def _clean(tokens: list[Token]) -> list[Token]:
    return [token for token in tokens if token.kind not in {"NL", "COMMENT"}]


def _assignment(tokens: list[Token], index: int) -> list[Token]:
    """Return the right-hand side of an assignment or setter call."""

    cursor = index + 1
    while cursor < len(tokens) and tokens[cursor].kind in {"NL", "COMMENT"}:
        cursor += 1
    if cursor < len(tokens) and tokens[cursor].is_op("="):
        cursor += 1
        end = find_statement_end(tokens, cursor)
        return _clean(tokens[cursor:end])
    if cursor < len(tokens) and tokens[cursor].is_op("("):
        depth = 1
        cursor += 1
        start = cursor
        while cursor < len(tokens) and depth > 0:
            if tokens[cursor].is_op("("):
                depth += 1
            elif tokens[cursor].is_op(")"):
                depth -= 1
            cursor += 1
        return _clean(tokens[start : max(start, cursor - 1)])
    return []


def _wildcard(rhs: list[Token]) -> bool:
    return any(token.kind == "STRING" and token.value == "*" for token in rhs)


def _true(rhs: list[Token]) -> bool:
    return any(token.kind == "IDENT" and token.text == "true" for token in rhs)


def analyze(project) -> tuple[list[Finding], list[Diagnostic]]:
    findings: list[Finding] = []
    diagnostics: list[Diagnostic] = []

    routes_by_path = {route.path: route for route in project.routes}

    for function in project.functions:
        tokens = function.body
        if not tokens:
            continue
        matching = build_matching(tokens)
        for index, token in enumerate(tokens):
            if not (token.kind == "IDENT" and token.text == "CorsConfiguration"):
                continue
            cursor = index + 1
            while cursor < len(tokens) and tokens[cursor].kind in {"NL", "COMMENT"}:
                cursor += 1
            # ``CorsConfiguration()`` then ``.apply { ... }``.
            if cursor < len(tokens) and tokens[cursor].is_op("("):
                close = matching.get(cursor)
                if close is None:
                    continue
                cursor = close + 1
            while cursor < len(tokens) and tokens[cursor].kind in {"NL", "COMMENT"}:
                cursor += 1
            if cursor + 1 < len(tokens) and tokens[cursor].is_op(".") and tokens[cursor + 1].is_ident("apply"):
                cursor += 2
                while cursor < len(tokens) and tokens[cursor].kind in {"NL", "COMMENT"}:
                    cursor += 1
            if cursor >= len(tokens) or not tokens[cursor].is_op("{"):
                continue
            block_close = matching.get(cursor)
            if block_close is None:
                continue
            block = tokens[cursor + 1 : block_close]

            origin_lines: list[int] = []
            credential_lines: list[int] = []
            for offset, item in enumerate(block):
                if item.kind != "IDENT":
                    continue
                if item.text in _ORIGIN_KEYS:
                    if _wildcard(_assignment(block, offset)):
                        origin_lines.append(item.line)
                elif item.text in _CREDENTIAL_KEYS:
                    if _true(_assignment(block, offset)):
                        credential_lines.append(item.line)

            if not origin_lines or not credential_lines:
                continue

            line = min(origin_lines)
            end_line = max(credential_lines)
            registered: list[dict[str, object]] = []
            related = []
            for call in function.calls:
                if call.name != "registerCorsConfiguration":
                    continue
                path = call.string_arg(0)
                if not path:
                    continue
                route = routes_by_path.get(path)
                entry: dict[str, object] = {
                    "routePath": path,
                    "sourcePath": function.file,
                    "line": call.line,
                    "resolution": "unknown",
                }
                if route is not None:
                    entry["resolution"] = "established"
                    entry["route"] = route.route_id
                    entry["handler"] = route.handler.name
                    entry["handlerLine"] = route.handler.line
                    related.append(
                        {
                            "path": route.handler.file,
                            "line": route.handler.line,
                            "endLine": route.handler.end_line,
                        }
                    )
                registered.append(entry)

            findings.append(
                Finding(
                    category="SAST",
                    path=function.file,
                    class_code="credentialed-cors",
                    line=line,
                    end_line=end_line,
                    severity="MEDIUM",
                    severity_basis=(
                        "Браузер сможет отправить cookie на запрос с недоверенного origin и прочитать ответ."
                    ),
                    description=(
                        "`CorsConfiguration` одновременно разрешает шаблон `*` и `allowCredentials = true`"
                        + (
                            f" для маршрута `{registered[0]['routePath']}`."
                            if registered
                            else "."
                        )
                    ),
                    remediation=(
                        "Перечислить доверенные origin явно или отключить `allowCredentials` для этого маршрута."
                    ),
                    rule_id="CG26-SAST-003",
                    confidence="confirmed",
                    confidence_basis=(
                        "Оба свойства прочитаны в одном блоке `CorsConfiguration.apply`; маршрут регистрации найден."
                        if registered
                        else "Оба свойства прочитаны в одном блоке `CorsConfiguration.apply`."
                    ),
                    related=related,
                    evidence={
                        "originSetting": {"line": min(origin_lines), "wildcard": True},
                        "credentialSetting": {"line": max(credential_lines), "enabled": True},
                        "registeredRoutes": registered,
                    },
                )
            )

    return findings, diagnostics
