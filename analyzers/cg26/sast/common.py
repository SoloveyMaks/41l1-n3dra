"""Shared helpers for the SAST rules."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..kotlin_index import Function, Route
from ..kotlin_lexer import Token

JDBC_METHODS = {
    "queryForList",
    "queryForObject",
    "query",
    "update",
    "execute",
    "batchUpdate",
    "queryForRowSet",
}

SQL_PATTERN = re.compile(r"(?is)\b(select|insert|update|delete|merge|call)\b")
OWNER_PATTERN = re.compile(r"(?is)\b{column}\b\s*(=|in)\s*[?:]")

AUTHENTICATED_EFFECTS = {"authenticated", "hasRole", "hasAnyRole", "hasAuthority", "hasAnyAuthority", "access"}
PUBLIC_EFFECTS = {"permitAll", "anonymous"}


@dataclass
class Source:
    name: str
    kind: str  # request-param / path-variable / principal
    line: int
    end_line: int
    path: str

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "path": self.path,
            "line": self.line,
            "endLine": self.end_line,
        }


@dataclass
class SqlArgument:
    call: object
    argument_index: int
    string_token: Token
    argument_tokens: list[Token]


def clean(tokens: list[Token]) -> list[Token]:
    return [token for token in tokens if token.kind not in {"NL", "COMMENT"}]


def source_params(function: Function) -> list[Source]:
    sources: list[Source] = []
    for param in function.params:
        if param.annotation("RequestParam") is not None:
            sources.append(Source(param.name, "request-param", param.line, param.end_line, function.file))
        elif param.annotation("PathVariable") is not None:
            sources.append(Source(param.name, "path-variable", param.line, param.end_line, function.file))
        elif param.type_text.split("<")[0] in {"Principal", "Authentication"}:
            sources.append(Source(param.name, "principal", param.line, param.end_line, function.file))
    return sources


def uses_principal(function: Function) -> bool:
    for token in function.body:
        if token.kind == "IDENT" and token.text in {"Principal", "Authentication", "SecurityContextHolder"}:
            return True
    clean_tokens = clean(function.body)
    for index in range(len(clean_tokens) - 2):
        if (
            clean_tokens[index].is_ident("principal")
            and clean_tokens[index + 1].is_op(".")
            and clean_tokens[index + 2].is_ident("name")
        ):
            return True
    return False


def sql_arguments(function: Function) -> list[SqlArgument]:
    results: list[SqlArgument] = []
    for call in function.calls:
        if call.name not in JDBC_METHODS:
            continue
        for argument_index, argument in enumerate(call.args):
            for token in argument:
                if token.kind != "STRING" or not token.value:
                    continue
                if not SQL_PATTERN.search(token.value):
                    continue
                results.append(SqlArgument(call, argument_index, token, argument))
    return results


def leading_identifier(expression: str) -> str:
    match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)", expression)
    return match.group(1) if match else ""


def has_owner_filter(sql: str, column: str) -> bool:
    return bool(OWNER_PATTERN.sub(lambda match: match.group(0), sql)) and bool(
        re.search(r"(?is)\b" + re.escape(column) + r"\b\s*(=|in)\s*[?:]", sql)
    )


def route_effect(route: Route) -> str:
    if route.applied_rule is None:
        return ""
    return route.applied_rule.effect


def is_authenticated(route: Route) -> bool:
    return route_effect(route) in AUTHENTICATED_EFFECTS


def handler_sensitivity(project, function: Function) -> tuple[bool, str]:
    """Decide whether a handler exposes data beyond a public catalog.

    Evidence-based: it must read rows through JDBC and return either an owner
    column or an unfiltered result set.  Names of files, classes or routes are
    never used.
    """

    reads = [call for call in function.calls if call.name in {"queryForList", "query", "queryForObject"}]
    if not reads:
        return False, "обработчик не читает записи из БД"
    if uses_principal(function):
        return False, "обработчик ограничивает выборку текущей сессией"
    for call in reads:
        sql = call.string_arg(0) or ""
        if not sql:
            continue
        if _sql_has_owner_predicate(sql):
            continue
        if _sql_returns_sensitive(sql):
            return True, f"`{call.name}` возвращает записи без фильтра владельца"
    return False, "записи ограничены условием запроса"


def _sql_returns_sensitive(sql: str) -> bool:
    match = re.search(r"(?is)\bselect\b(.*?)\bfrom\b", sql)
    selected = match.group(1).lower() if match else sql.lower()
    if re.search(r"(?is)\b(customer_id|owner_id|user_id|account_id)\b", selected):
        return True
    return re.search(r"(?is)\bwhere\b", sql) is None


def _sql_has_owner_predicate(sql: str) -> bool:
    return bool(
        re.search(
            r"(?is)\b(customer_id|owner_id|user_id|account_id|principal)\b\s*(=|in)\s*[?:]",
            sql,
        )
    )


def location(path: str, line: int, end_line: int | None = None) -> dict[str, object]:
    data: dict[str, object] = {"path": path, "line": line}
    data["endLine"] = end_line if end_line is not None else line
    return data
