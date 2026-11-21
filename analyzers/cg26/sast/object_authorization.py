"""Object authorization (IDOR/BOLA) with an explicit ownership policy.

The rule follows a client-supplied identifier from an authenticated route to a
JDBC read.  It reports only when the read selects an owner column, filters on
an object identifier, and does not constrain the query by the owner.  The
policy lives in :mod:`analyzers.cg26.policy`; a column name alone is never
treated as proof of access control.
"""

from __future__ import annotations

import re

from ..model import Diagnostic, Finding, RuleInfo
from ..policy import OBJECT_ACCESS_POLICY, OWNER_COLUMNS, owner_predicate
from .common import is_authenticated, sql_arguments

RULES = [
    RuleInfo(
        "CG26-SAST-005",
        "SAST",
        "1",
        "Аутентифицированный маршрут читает объект по чужому идентификатору без проверки владельца",
        "object-authorization",
    )
]

_FILTER = re.compile(r"(?is)\b([a-z_][a-z0-9_]*)\b\s*(?:=|in)\s*\?")
_SELECT = re.compile(r"(?is)\bselect\b(.*?)\bfrom\b")


def _selected_columns(sql: str) -> str:
    match = _SELECT.search(sql)
    return match.group(1).lower() if match else sql.lower()


def _filter_columns(sql: str) -> list[str]:
    return [match.group(1).lower() for match in _FILTER.finditer(sql)]


def _has_owner_predicate(sql: str) -> bool:
    return any(re.search(owner_predicate(column), sql) for column in OWNER_COLUMNS)


def _looks_like_object_read(sql: str) -> tuple[bool, str]:
    selected = _selected_columns(sql)
    owner_selected = next((column for column in OWNER_COLUMNS if column in selected), None)
    if owner_selected is None:
        return False, "запрос не возвращает owner-колонку"
    filters = _filter_columns(sql)
    if not filters:
        return False, "запрос не фильтрует по идентификатору"
    if _has_owner_predicate(sql):
        return False, "запрос ограничен владельцем"
    if not any(column == "id" or column.endswith("_id") for column in filters):
        return False, "запрос не фильтрует по идентификатору объекта"
    return True, f"выбрана owner-колонка `{owner_selected}`, фильтр только по `{', '.join(filters)}`"


def _ctor_types(project, class_name: str | None) -> dict[str, str]:
    if class_name is None:
        return {}
    cls = project.find_class(class_name)
    if cls is None:
        return {}
    result: dict[str, str] = {}
    for param in cls.ctor_params:
        base = param.type_text.split("<")[0].rstrip("?").strip()
        if base:
            result[param.name] = base
    return result


def _path_variables(function) -> list[str]:
    return [
        param.name
        for param in function.params
        if param.annotation("PathVariable") is not None
    ]


def _call_passes(call, name: str) -> bool:
    return any(token.kind == "IDENT" and token.text == name for argument in call.args for token in argument)


def analyze(project) -> tuple[list[Finding], list[Diagnostic]]:
    findings: list[Finding] = []
    diagnostics: list[Diagnostic] = []

    for route in project.routes:
        if not is_authenticated(route):
            continue
        handler = route.handler
        variables = _path_variables(handler)
        if not variables:
            continue

        ctor_types = _ctor_types(project, handler.owner)
        direct_candidates = [(handler, None)]
        for call in handler.calls:
            receiver_type = ctor_types.get(call.receiver)
            if receiver_type is None:
                continue
            target = project.find_class(receiver_type)
            if target is None:
                continue
            for method in target.functions:
                if method.name != call.name:
                    continue
                if not any(_call_passes(call, variable) for variable in variables):
                    continue
                direct_candidates.append((method, call))

        for method, via_call in direct_candidates:
            for sql_arg in sql_arguments(method):
                sql = sql_arg.string_token.value or ""
                is_object, reason = _looks_like_object_read(sql)
                if not is_object:
                    continue
                line = method.line if via_call is not None else sql_arg.string_token.line
                findings.append(
                    Finding(
                        category="SAST",
                        path=method.file,
                        class_code="object-authorization",
                        line=line,
                        end_line=sql_arg.string_token.line,
                        severity="HIGH",
                        severity_basis=(
                            "Пользователь может запросить чужой объект, подставив его идентификатор."
                        ),
                        description=(
                            f"`{method.name}` читает объект по идентификатору из маршрута "
                            f"`{route.route_id}`; {reason}. Политика: {OBJECT_ACCESS_POLICY}"
                        ),
                        remediation=(
                            "Добавить в запрос owner-колонку и значение из `Principal`; "
                            "чужой и отсутствующий объект должны давать одинаковый ответ."
                        ),
                        rule_id="CG26-SAST-005",
                        confidence="confirmed",
                        confidence_basis=(
                            "Идентификатор пути передан в чтение; SQL выбирает owner-колонку и не содержит owner-предиката."
                        ),
                        related=[
                            {
                                "path": handler.file,
                                "line": handler.line,
                                "endLine": handler.end_line,
                            }
                        ],
                        evidence={
                            "policy": OBJECT_ACCESS_POLICY,
                            "ownerColumns": list(OWNER_COLUMNS),
                            "route": route.route_id,
                            "pathVariables": variables,
                            "reader": {
                                "path": method.file,
                                "name": method.name,
                                "line": method.line,
                                "endLine": method.end_line,
                            },
                            "call": (
                                {
                                    "receiver": via_call.receiver,
                                    "name": via_call.name,
                                    "line": via_call.line,
                                }
                                if via_call is not None
                                else None
                            ),
                            "sql": {"line": sql_arg.string_token.line, "reason": reason},
                        },
                    )
                )

    return findings, diagnostics
