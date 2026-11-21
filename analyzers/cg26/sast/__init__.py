"""SAST analyzers for the five required classes."""

from __future__ import annotations

from . import cors, object_authorization, route_authorization, sql_injection, tls_verification
from ..model import Diagnostic, Finding

RULES = [
    *sql_injection.RULES,
    *tls_verification.RULES,
    *cors.RULES,
    *route_authorization.RULES,
    *object_authorization.RULES,
]


def analyze(project) -> tuple[list[Finding], list[Diagnostic]]:
    findings: list[Finding] = []
    diagnostics: list[Diagnostic] = []
    for module in (
        sql_injection,
        tls_verification,
        cors,
        route_authorization,
        object_authorization,
    ):
        module_findings, module_diagnostics = module.analyze(project)
        findings.extend(module_findings)
        diagnostics.extend(module_diagnostics)
    return findings, diagnostics
