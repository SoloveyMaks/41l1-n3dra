from backend.app.contracts.schemas import NodeKind, WorkflowDraft, WorkflowEdge, WorkflowNode


def get_default_workflow_draft(artifact_id: str = "") -> WorkflowDraft:
    nodes = [
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
            config={}
        ),
        WorkflowNode(
            id="node_sca",
            kind=NodeKind.SCA_SCAN,
            title="SCA Dependencies",
            position={"x": 500, "y": 250},
            config={}
        ),
        WorkflowNode(
            id="node_sast",
            kind=NodeKind.KOTLIN_SAST,
            title="Kotlin SAST",
            position={"x": 500, "y": 400},
            config={}
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
            config={"model": "gemini-3.8-flash", "strictValidation": True}
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

    edges = [
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

    return WorkflowDraft(
        schemaVersion=1,
        name="Банковский аудит Kotlin/Spring Boot (Secure PR Gate)",
        description="Комплексный сквозной анализ: поиск секретов, проверка зависимостей SCA, taint-анализ SAST, AI-обоснование и опциональное уведомление (по умолчанию выключено)",
        nodes=nodes,
        edges=edges
    )
