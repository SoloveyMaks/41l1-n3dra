from typing import Dict, List
from backend.app.contracts.schemas import NodeDefinition, NodeKind, PortDefinition, PortType

NODE_REGISTRY: Dict[NodeKind, NodeDefinition] = {
    NodeKind.MANUAL_TRIGGER: NodeDefinition(
        kind=NodeKind.MANUAL_TRIGGER,
        title="Manual Trigger",
        category="Triggers",
        description="Запуск проверки вручную пользователем или через API",
        inputs=[],
        outputs=[
            PortDefinition(id="out", name="Trigger Out", type=PortType.TRIGGER)
        ],
        default_config={}
    ),
    NodeKind.SCHEDULE_TRIGGER: NodeDefinition(
        kind=NodeKind.SCHEDULE_TRIGGER,
        title="Schedule Trigger",
        category="Triggers",
        description="Периодический запуск проверки по расписанию (cron / интервал)",
        inputs=[],
        outputs=[
            PortDefinition(id="out", name="Trigger Out", type=PortType.TRIGGER)
        ],
        default_config={"cron": "*/30 * * * *", "timezone": "UTC"}
    ),
    NodeKind.PROJECT_INPUT: NodeDefinition(
        kind=NodeKind.PROJECT_INPUT,
        title="Project Input",
        category="Input",
        description="Привязка проверенного загруженного проекта (ZIP-архива)",
        inputs=[
            PortDefinition(id="trigger", name="Trigger In", type=PortType.TRIGGER)
        ],
        outputs=[
            PortDefinition(id="artifact", name="Artifact Out", type=PortType.ARTIFACT)
        ],
        default_config={"artifactId": ""}
    ),
    NodeKind.SECRET_SCAN: NodeDefinition(
        kind=NodeKind.SECRET_SCAN,
        title="Secret Scanner",
        category="Analyzers",
        description="Собственный анализатор утечек паролей, ключей, токенов и секретов",
        inputs=[
            PortDefinition(id="artifact", name="Artifact In", type=PortType.ARTIFACT)
        ],
        outputs=[
            PortDefinition(id="scan_result", name="Scan Result", type=PortType.SCAN_RESULT)
        ],
        default_config={"engine": "full", "detectEntropy": True, "scanProperties": True}
    ),
    NodeKind.SCA_SCAN: NodeDefinition(
        kind=NodeKind.SCA_SCAN,
        title="SCA Scanner",
        category="Analyzers",
        description="Собственный анализатор зависимостей Kotlin/Gradle без исполнения кода",
        inputs=[
            PortDefinition(id="artifact", name="Artifact In", type=PortType.ARTIFACT)
        ],
        outputs=[
            PortDefinition(id="scan_result", name="Scan Result", type=PortType.SCAN_RESULT)
        ],
        default_config={"engine": "full", "checkTransitive": True}
    ),
    NodeKind.KOTLIN_SAST: NodeDefinition(
        kind=NodeKind.KOTLIN_SAST,
        title="Kotlin SAST Scanner",
        category="Analyzers",
        description="Собственный статический taint-анализатор Kotlin/Spring Boot на базе Tree-sitter",
        inputs=[
            PortDefinition(id="artifact", name="Artifact In", type=PortType.ARTIFACT)
        ],
        outputs=[
            PortDefinition(id="scan_result", name="Scan Result", type=PortType.SCAN_RESULT)
        ],
        default_config={"engine": "full", "rules": ["SQL_INJECTION", "PATH_TRAVERSAL", "COMMAND_INJECTION", "OPEN_ENDPOINT", "IDOR"]}
    ),
    NodeKind.MERGE_FINDINGS: NodeDefinition(
        kind=NodeKind.MERGE_FINDINGS,
        title="Merge Findings",
        category="Aggregators",
        description="Объединение находок сканеров, агрегация метрик покрытия и диагностик",
        inputs=[
            PortDefinition(id="scan_1", name="Scan 1", type=PortType.SCAN_RESULT),
            PortDefinition(id="scan_2", name="Scan 2", type=PortType.SCAN_RESULT),
            PortDefinition(id="scan_3", name="Scan 3", type=PortType.SCAN_RESULT)
        ],
        outputs=[
            PortDefinition(id="findings_bundle", name="Findings Bundle", type=PortType.FINDINGS_BUNDLE)
        ],
        default_config={}
    ),
    NodeKind.POLICY_GATE: NodeDefinition(
        kind=NodeKind.POLICY_GATE,
        title="Policy Gate",
        category="Quality Gates",
        description="Принятие решения безопасности (Pass / Fail / Inconclusive) на основе порогов",
        inputs=[
            PortDefinition(id="findings_bundle", name="Findings In", type=PortType.FINDINGS_BUNDLE)
        ],
        outputs=[
            PortDefinition(id="policy_result", name="Policy Out", type=PortType.POLICY_RESULT)
        ],
        default_config={
            "failOnCritical": True,
            "failOnHigh": True,
            "maxAllowedMedium": 2,
            "strictCoverageRequired": True
        }
    ),
    NodeKind.CONDITION: NodeDefinition(
        kind=NodeKind.CONDITION,
        title="Condition (If/Else)",
        category="Logic",
        description="Ветвление выполнения: сравнение чисел, строк или regex",
        inputs=[
            PortDefinition(id="in", name="Value In", type=PortType.POLICY_RESULT)
        ],
        outputs=[
            PortDefinition(id="true_branch", name="True Branch", type=PortType.CONDITION_BRANCH),
            PortDefinition(id="false_branch", name="False Branch", type=PortType.CONDITION_BRANCH)
        ],
        default_config={
            "field": "verdict",
            "operator": "equals",  # "equals", "not_equals", "greater_than", "less_than", "regex"
            "targetValue": "fail"
        }
    ),
    NodeKind.BRANCH_MERGE: NodeDefinition(
        kind=NodeKind.BRANCH_MERGE,
        title="Branch Merge",
        category="Logic",
        description="Схождение условных ветвей (продолжает выполнение активной ветки)",
        inputs=[
            PortDefinition(id="branch_a", name="Branch A", type=PortType.CONDITION_BRANCH),
            PortDefinition(id="branch_b", name="Branch B", type=PortType.CONDITION_BRANCH)
        ],
        outputs=[
            PortDefinition(id="out", name="Merged Out", type=PortType.CONDITION_BRANCH)
        ],
        default_config={}
    ),
    NodeKind.AI_EXPLAIN: NodeDefinition(
        kind=NodeKind.AI_EXPLAIN,
        title="AI Explain & Fix",
        category="AI / Enrichment",
        description="Генерация понятных объяснений и рекомендаций по исправлению от LLM с валидацией",
        inputs=[
            PortDefinition(id="findings_bundle", name="Findings In", type=PortType.FINDINGS_BUNDLE)
        ],
        outputs=[
            PortDefinition(id="explain_result", name="Explain Out", type=PortType.EXPLAIN_RESULT)
        ],
        default_config={
            "strictValidation": True
        }
    ),
    NodeKind.AI_SCAN: NodeDefinition(
        kind=NodeKind.AI_SCAN,
        title="AI Vulnerability Scanner",
        category="AI / Enrichment",
        description=(
            "Поиск дополнительных гипотез уязвимостей в ограниченном redacted "
            "фрагменте проекта; не подтверждает находки и не меняет вердикт"
        ),
        inputs=[
            PortDefinition(id="artifact", name="Artifact In", type=PortType.ARTIFACT),
            PortDefinition(id="findings_bundle", name="Deterministic Findings (Optional)", type=PortType.FINDINGS_BUNDLE),
        ],
        outputs=[
            PortDefinition(id="ai_scan_result", name="AI Hypotheses Out", type=PortType.AI_SCAN_RESULT)
        ],
        default_config={
            "profile": "balanced",
            "maxFiles": 6,
            "maxBytes": 24576,
            "perFileBytes": 4096,
            "maxObservations": 12,
        }
    ),
    NodeKind.BUILD_REPORT: NodeDefinition(
        kind=NodeKind.BUILD_REPORT,
        title="Build Report",
        category="Reporting",
        description="Формирование читаемого отчета и официального JSON отчета с картой безопасности",
        inputs=[
            PortDefinition(id="findings_bundle", name="Findings In", type=PortType.FINDINGS_BUNDLE),
            PortDefinition(id="policy_result", name="Policy In", type=PortType.POLICY_RESULT),
            PortDefinition(id="explain_result", name="AI Explain (Optional)", type=PortType.EXPLAIN_RESULT),
            PortDefinition(id="ai_scan_result", name="AI Hypotheses (Optional, advisory)", type=PortType.AI_SCAN_RESULT)
        ],
        outputs=[
            PortDefinition(id="report", name="Report Out", type=PortType.REPORT)
        ],
        default_config={
            "includePassport": True,
            "generateOfficialJson": True
        }
    ),
    NodeKind.CUSTOM_CHECK: NodeDefinition(
        kind=NodeKind.CUSTOM_CHECK,
        title="Custom Check (Python sandbox)",
        category="Analyzers",
        description=(
            "Пользовательская Python-проверка workspace в bwrap: без сети, host env "
            "и записи в проект; результат — отдельный поток custom-фактов, не findings/verdict"
        ),
        inputs=[
            PortDefinition(id="artifact", name="Artifact In", type=PortType.ARTIFACT)
        ],
        outputs=[
            PortDefinition(id="custom_findings", name="Custom Findings Out", type=PortType.CUSTOM_FINDINGS)
        ],
        default_config={"definitionId": ""}
    ),
    NodeKind.NOTIFY: NodeDefinition(
        kind=NodeKind.NOTIFY,
        title="Notify (Webhook)",
        category="Notifications",
        description="Отправка оповещения с вердиктом и сводкой на внешний HTTP Webhook",
        inputs=[
            PortDefinition(id="report", name="Report In", type=PortType.REPORT)
        ],
        outputs=[
            PortDefinition(id="delivery_result", name="Delivery Result", type=PortType.DELIVERY_RESULT)
        ],
        default_config={
            "webhookUrl": "http://127.0.0.1:8000/api/webhook-test",
            "sendOnFailOnly": False,
            "secretToken": ""
        }
    )
}


def get_node_definitions() -> List[NodeDefinition]:
    return list(NODE_REGISTRY.values())
