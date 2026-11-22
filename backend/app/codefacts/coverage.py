"""Honest verification of the scanner scopes a workflow asked for.

`expectedScopesVerified` is a trust signal: it may be true only when every
scanner node in the frozen workflow produced checked coverage for at least one
scope and nothing in that category was skipped or errored. A missing category
is not a pass, and an empty expectation is not verification.
"""

from __future__ import annotations

from typing import Any, Dict, Set

_SCANNER_CATEGORIES = {
    "secret_scan": "SECRET",
    "sca_scan": "SCA",
    "kotlin_sast": "SAST",
}


def expected_scope_categories(workflow: Dict[str, Any]) -> Set[str]:
    draft = workflow.get("draft") or {}
    nodes = draft.get("nodes") or []
    expected = {
        _SCANNER_CATEGORIES[node.get("kind")]
        for node in nodes
        if isinstance(node, dict) and node.get("kind") in _SCANNER_CATEGORIES
    }
    return expected


def observed_scope_statuses(envelope: Dict[str, Any]) -> Dict[str, Set[str]]:
    observed: Dict[str, Set[str]] = {}
    for result in envelope.get("scanResults") or []:
        if not isinstance(result, dict):
            continue
        coverage = (
            result.get("coverage")
            or ((result.get("codeFacts") or {}).get("coverage"))
            or {}
        )
        for scope in coverage.get("scopes") or []:
            if not isinstance(scope, dict):
                continue
            category = str(scope.get("category") or "").upper()
            status = str(scope.get("status") or "")
            if category:
                observed.setdefault(category, set()).add(status)
    return observed


def expected_scopes_verified(envelope: Dict[str, Any], workflow: Dict[str, Any]) -> bool:
    """True only when every requested scanner category is fully checked."""
    expected = expected_scope_categories(workflow)
    if not expected:
        return False
    observed = observed_scope_statuses(envelope)
    for category in expected:
        statuses = observed.get(category)
        if not statuses or any(status != "checked" for status in statuses):
            return False
    return True
