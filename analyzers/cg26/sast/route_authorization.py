"""Route authorization: a public rule exposes a handler that returns real data.

The rule does not flag ``permitAll`` by itself.  It reports only when the
matched handler reads records through JDBC and returns them without any
owner/session filter.  A public catalog that returns a static list is not a
finding.
"""

from __future__ import annotations

from ..model import Diagnostic, Finding, RuleInfo
from .common import PUBLIC_EFFECTS, handler_sensitivity, is_authenticated

RULES = [
    RuleInfo(
        "CG26-SAST-004",
        "SAST",
        "1",
        "Публичное правило открывает маршрут, возвращающий защищённые данные",
        "route-authorization",
    )
]


def analyze(project) -> tuple[list[Finding], list[Diagnostic]]:
    findings: list[Finding] = []
    diagnostics: list[Diagnostic] = []
    seen: set[tuple[int, str]] = set()

    for route in project.routes:
        rule = route.applied_rule
        if rule is None or rule.effect not in PUBLIC_EFFECTS:
            continue
        if is_authenticated(route):
            continue
        key = (rule.line, route.path)
        if key in seen:
            continue
        sensitive, reason = handler_sensitivity(project, route.handler)
        if not sensitive:
            continue
        seen.add(key)
        findings.append(
            Finding(
                category="SAST",
                path=rule.source_file,
                class_code="route-authorization",
                line=rule.line,
                end_line=rule.end_line,
                severity="HIGH",
                severity_basis=(
                    "Маршрут доступен без роли, а обработчик возвращает записи других пользователей."
                ),
                description=(
                    f"Правило `{rule.effect}()` открывает `{route.route_id}`. Обработчик "
                    f"`{route.handler.name}`: {reason}, поэтому данные доступны без нужной роли."
                ),
                remediation=(
                    f"Требовать роль для `{route.route_id}` и проверить доступ для гостя, клиента и сотрудника."
                ),
                rule_id="CG26-SAST-004",
                confidence="confirmed",
                confidence_basis=(
                    "Правило сопоставлено с маршрутом по методу и пути, обработчик прочитан и возвращает строки БД."
                ),
                related=[
                    {
                        "path": route.handler.file,
                        "line": route.handler.line,
                        "endLine": route.handler.end_line,
                    }
                ],
                evidence={
                    "rule": {
                        "effect": rule.effect,
                        "matchers": rule.matchers,
                        "line": rule.line,
                        "anyRequest": rule.any_request,
                    },
                    "route": {"id": route.route_id, "handler": route.handler.name},
                    "handlerFinding": reason,
                },
            )
        )

    return findings, diagnostics
