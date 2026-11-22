"""System default workflow: Secure PR Gate.

The default is seeded only when a workspace has no workflows yet. Existing user
workflows are never rewritten from here; the explicit, revision-preserving
upgrade path lives in `backend.app.demo.default_workflow_upgrade`.

`AI_SCAN` reads the same frozen artifact as deterministic scanners. It only
receives bounded redacted source/config excerpts (never logs, `.env`, keys,
test or generated paths) and returns advisory hypotheses that stay outside
canonical findings, policy counts and the verdict.
"""

from backend.app.contracts.schemas import NodeKind, WorkflowDraft, WorkflowEdge, WorkflowNode

SYSTEM_DEFAULT_WORKFLOW_ID = "wf_secure_pr_gate"
AI_SCAN_NODE_ID = "node_ai_scan"


def _base_nodes(artifact_id: str) -> list[WorkflowNode]:
    """Deterministic part of the default; also the frozen pre-AI_SCAN baseline."""
    return [
        WorkflowNode(
            id="node_start",
            kind=NodeKind.MANUAL_TRIGGER,
            title="Manual Trigger",
            position={"x": 50, "y": 250},
            config={}
        ),
        WorkflowNode(
            id="node_project",
            kind=NodeKind.PROJECT_INPUT,
            title="Project Input",
            position={"x": 260, "y": 250},
            config={"artifactId": artifact_id}
        ),
        WorkflowNode(
            id="node_secret",
            kind=NodeKind.SECRET_SCAN,
            title="Secret Scanner",
            position={"x": 500, "y": 100},
            config={"engine": "full"}
        ),
        WorkflowNode(
            id="node_sca",
            kind=NodeKind.SCA_SCAN,
            title="SCA Dependencies",
            position={"x": 500, "y": 250},
            config={"engine": "full"}
        ),
        WorkflowNode(
            id="node_sast",
            kind=NodeKind.KOTLIN_SAST,
            title="Kotlin SAST",
            position={"x": 500, "y": 400},
            config={"engine": "full"}
        ),
        WorkflowNode(
            id="node_merge",
            kind=NodeKind.MERGE_FINDINGS,
            title="Merge Findings",
            position={"x": 760, "y": 250},
            config={}
        ),
        WorkflowNode(
            id="node_policy",
            kind=NodeKind.POLICY_GATE,
            title="Policy Gate",
            position={"x": 980, "y": 250},
            config={"failOnCritical": True, "failOnHigh": True, "strictCoverageRequired": True}
        ),
        WorkflowNode(
            id="node_cond",
            kind=NodeKind.CONDITION,
            title="Condition (Risk Found?)",
            position={"x": 1200, "y": 250},
            config={"field": "verdict", "operator": "equals", "targetValue": "fail"}
        ),
        WorkflowNode(
            id="node_ai",
            kind=NodeKind.AI_EXPLAIN,
            title="AI Explain & Fix",
            position={"x": 1420, "y": 150},
            config={"strictValidation": True}
        ),
        WorkflowNode(
            id="node_report",
            kind=NodeKind.BUILD_REPORT,
            title="Build Report",
            position={"x": 1660, "y": 250},
            config={"includePassport": True}
        ),
        WorkflowNode(
            id="node_notify",
            kind=NodeKind.NOTIFY,
            title="Notify (Webhook)",
            position={"x": 1900, "y": 250},
            config={"enabled": False, "webhookUrl": "", "sendOnFailOnly": True}
        )
    ]


def _base_edges() -> list[WorkflowEdge]:
    return [
        WorkflowEdge(id="e_start_proj", source="node_start", sourcePort="out", target="node_project", targetPort="trigger"),
        WorkflowEdge(id="e_proj_sec", source="node_project", sourcePort="artifact", target="node_secret", targetPort="artifact"),
        WorkflowEdge(id="e_proj_sca", source="node_project", sourcePort="artifact", target="node_sca", targetPort="artifact"),
        WorkflowEdge(id="e_proj_sast", source="node_project", sourcePort="artifact", target="node_sast", targetPort="artifact"),
        WorkflowEdge(id="e_sec_merge", source="node_secret", sourcePort="scan_result", target="node_merge", targetPort="scan_1"),
        WorkflowEdge(id="e_sca_merge", source="node_sca", sourcePort="scan_result", target="node_merge", targetPort="scan_2"),
        WorkflowEdge(id="e_sast_merge", source="node_sast", sourcePort="scan_result", target="node_merge", targetPort="scan_3"),
        WorkflowEdge(id="e_merge_policy", source="node_merge", sourcePort="findings_bundle", target="node_policy", targetPort="findings_bundle"),
        WorkflowEdge(id="e_policy_cond", source="node_policy", sourcePort="policy_result", target="node_cond", targetPort="in"),
        WorkflowEdge(id="e_cond_ai", source="node_cond", sourcePort="true_branch", target="node_ai", targetPort="findings_bundle"),
        WorkflowEdge(id="e_ai_report", source="node_ai", sourcePort="explain_result", target="node_report", targetPort="explain_result"),
        WorkflowEdge(id="e_cond_report_false", source="node_cond", sourcePort="false_branch", target="node_report", targetPort="policy_result"),
        WorkflowEdge(id="e_report_notify", source="node_report", sourcePort="report", target="node_notify", targetPort="report")
    ]


def _ai_scan_node() -> WorkflowNode:
    return WorkflowNode(
        id=AI_SCAN_NODE_ID,
        kind=NodeKind.AI_SCAN,
        title="AI Vulnerability Scanner",
        # Advisory side branch: the only outgoing edge goes to the report's
        # dedicated AI input. It never reaches policy, canonical findings or
        # the deterministic counts.
        position={"x": 500, "y": 560},
        config={
            "profile": "balanced",
            "maxFiles": 6,
            "maxBytes": 24576,
            "perFileBytes": 4096,
            "maxObservations": 12,
        },
    )


def _ai_scan_edges() -> list[WorkflowEdge]:
    return [
        WorkflowEdge(
            id="e_proj_ai_scan", source="node_project", sourcePort="artifact",
            target=AI_SCAN_NODE_ID, targetPort="artifact",
        ),
        WorkflowEdge(
            id="e_merge_ai_scan", source="node_merge", sourcePort="findings_bundle",
            target=AI_SCAN_NODE_ID, targetPort="findings_bundle",
        ),
        WorkflowEdge(
            id="e_ai_scan_report", source=AI_SCAN_NODE_ID, sourcePort="ai_scan_result",
            target="node_report", targetPort="ai_scan_result",
        ),
    ]


def get_previous_system_default_draft(artifact_id: str = "") -> WorkflowDraft:
    """Frozen pre-AI_SCAN default, used only to detect untouched system drafts.

    Do not extend this baseline when the default changes again: capture the
    then-current default instead, otherwise user edits stop being detectable.
    """
    return WorkflowDraft(
        schemaVersion=1,
        name="Банковский аудит Kotlin/Spring Boot (Secure PR Gate)",
        description="Комплексный сквозной анализ: поиск секретов, проверка зависимостей SCA, taint-анализ SAST, AI-обоснование и опциональное уведомление (по умолчанию выключено)",
        nodes=_base_nodes(artifact_id),
        edges=_base_edges(),
    )


def get_default_workflow_draft(artifact_id: str = "") -> WorkflowDraft:
    nodes = _base_nodes(artifact_id)
    for node in nodes:
        if node.kind == NodeKind.AI_EXPLAIN:
            node.config = {"strictValidation": True}
    return WorkflowDraft(
        schemaVersion=1,
        name="Банковский аудит Kotlin/Spring Boot (Secure PR Gate)",
        description=(
            "Комплексный сквозной анализ: поиск секретов, проверка зависимостей SCA, "
            "taint-анализ SAST, отдельные ИИ-гипотезы по redacted-фрагментам "
            "замороженного кода, AI-обоснование и опциональное уведомление "
            "(по умолчанию выключено)"
        ),
        nodes=[*nodes, _ai_scan_node()],
        edges=[*_base_edges(), *_ai_scan_edges()],
    )
