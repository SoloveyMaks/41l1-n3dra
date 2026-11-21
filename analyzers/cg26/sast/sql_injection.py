"""SQL injection: a request-controlled value reaches a JDBC SQL string.

The rule is deliberately data-flow shaped rather than pattern based:

* a parameter annotated with ``@RequestParam`` / ``@PathVariable`` is a source;
* the SQL argument of a ``JdbcTemplate`` call is a sink;
* the source reaches the sink when it is interpolated into the SQL literal or
  concatenated into the SQL argument.

A parameterised query (``?`` plus a separate argument) never reports, even when
it sits next to a vulnerable query in the same file.
"""

from __future__ import annotations

from ..kotlin_lexer import Token
from ..model import Diagnostic, Finding, RuleInfo
from .common import JDBC_METHODS, leading_identifier, source_params, sql_arguments

RULES = [
    RuleInfo(
        "CG26-SAST-001",
        "SAST",
        "1",
        "Значение запроса попадает в текст SQL без параметризации",
        "sql-injection",
    )
]


def _sql_sink_tokens(function) -> list[tuple[object, Token, list[Token]]]:
    results = []
    for call in function.calls:
        if call.name not in JDBC_METHODS:
            continue
        for argument in call.args:
            for token in argument:
                if token.kind == "STRING" and token.value:
                    results.append((call, token, argument))
    return results


def analyze(project) -> tuple[list[Finding], list[Diagnostic]]:
    findings: list[Finding] = []
    diagnostics: list[Diagnostic] = []

    route_by_handler = {id(route.handler): route for route in project.routes}

    for function in project.functions:
        sources = source_params(function)
        if not sources:
            continue
        tainted = {source.name for source in sources if source.kind in {"request-param", "path-variable"}}
        if not tainted:
            continue

        for call, string_token, argument in _sql_sink_tokens(function):
            if not string_token.value:
                continue
            from .common import SQL_PATTERN

            if not SQL_PATTERN.search(string_token.value):
                continue

            reached: list[tuple[str, str]] = []
            for interpolation in string_token.interpolations:
                name = leading_identifier(interpolation.expr)
                if name in tainted:
                    reached.append((name, "interpolation"))

            if any(token.is_op("+") for token in argument):
                for token in argument:
                    if token.kind == "IDENT" and token.text in tainted:
                        if all(name != token.text for name, _ in reached):
                            reached.append((token.text, "concatenation"))

            if not reached:
                continue

            route = route_by_handler.get(id(function))
            route_text = route.route_id if route is not None else "вне маршрута"
            names = ", ".join(f"`{name}`" for name, _ in reached)
            how = "интерполяцию" if any(kind == "interpolation" for _, kind in reached) else "конкатенацию"
            findings.append(
                Finding(
                    category="SAST",
                    path=function.file,
                    class_code="sql-injection",
                    line=function.line,
                    end_line=max(function.line, string_token.line),
                    severity="HIGH",
                    severity_basis=(
                        "Параметр запроса управляет текстом SQL, поэтому значение может изменить "
                        "условие выборки."
                    ),
                    description=(
                        f"Параметр {names} попадает в SQL через {how} и передаётся в "
                        f"`{call.name}` для маршрута `{route_text}`. Параметризация `?` не используется."
                    ),
                    remediation="Использовать `?` и передавать значение отдельным аргументом JDBC.",
                    rule_id="CG26-SAST-001",
                    confidence="confirmed",
                    confidence_basis=(
                        "Источник объявлен аннотацией параметра, sink — вызов JDBC; связь видна в одном теле функции."
                    ),
                    evidence={
                        "source": [source.to_dict() for source in sources if source.name in tainted],
                        "sink": {
                            "call": call.name,
                            "line": call.line,
                            "endLine": call.end_line,
                            "sqlLine": string_token.line,
                        },
                        "route": route_text,
                        "flowKind": how,
                    },
                )
            )

    return findings, diagnostics
