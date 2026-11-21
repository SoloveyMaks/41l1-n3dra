from collections import defaultdict, deque
from typing import Dict, List

from backend.app.contracts.node_registry import NODE_REGISTRY
from backend.app.contracts.schemas import (
    NodeKind, PortType, WorkflowDraft, WorkflowValidationIssue, WorkflowValidationResult
)


class WorkflowValidator:
    def validate(self, draft: WorkflowDraft) -> WorkflowValidationResult:
        issues: List[WorkflowValidationIssue] = []

        if not draft.nodes:
            issues.append(WorkflowValidationIssue(
                level="error",
                message="Граф пуст: добавьте хотя бы триггер и блоки анализа"
            ))
            return WorkflowValidationResult(valid=False, issues=issues)

        # 1. Check unknown nodes and index them
        node_map = {}
        has_trigger = False

        for node in draft.nodes:
            if node.id in node_map:
                issues.append(WorkflowValidationIssue(
                    level="error",
                    nodeId=node.id,
                    message=f"Дублирующийся идентификатор ноды: '{node.id}'"
                ))
            node_map[node.id] = node

            if node.kind not in NODE_REGISTRY:
                issues.append(WorkflowValidationIssue(
                    level="error",
                    nodeId=node.id,
                    message=f"Неизвестный тип ноды: '{node.kind}'"
                ))
            else:
                if node.kind in (NodeKind.MANUAL_TRIGGER, NodeKind.SCHEDULE_TRIGGER):
                    has_trigger = True

        if not has_trigger:
            issues.append(WorkflowValidationIssue(
                level="error",
                message="В сценарии отсутствует нода запуска (Manual Trigger или Schedule Trigger)"
            ))

        scan_kinds = {NodeKind.SECRET_SCAN, NodeKind.SCA_SCAN, NodeKind.KOTLIN_SAST}
        for node in draft.nodes:
            if node.kind not in scan_kinds:
                continue
            engine = str((node.config or {}).get("engine") or "full")
            if engine not in {"full", "auto", "backend", "team_b"}:
                issues.append(WorkflowValidationIssue(
                    level="error", nodeId=node.id,
                    message=(
                        f"Неизвестный engine '{engine}': допустимы full, auto, "
                        "backend и team_b; все они выполняют полный анализ"
                    ),
                ))

        for node in draft.nodes:
            if node.kind == NodeKind.AI_SCAN:
                issues.extend(self._validate_ai_scan_config(node.id, node.config or {}))

        # 2. Check edges: source and target existence, port existence and types
        adj = defaultdict(list)
        in_degree = defaultdict(int)
        for n_id in node_map:
            in_degree[n_id] = 0

        target_ports_connected = defaultdict(set)  # node_id -> set of port_ids

        for edge in draft.edges:
            if edge.source not in node_map:
                issues.append(WorkflowValidationIssue(
                    level="error",
                    edgeId=edge.id,
                    message=f"Ребро ссылается на несуществующую начальную ноду: '{edge.source}'"
                ))
                continue

            if edge.target not in node_map:
                issues.append(WorkflowValidationIssue(
                    level="error",
                    edgeId=edge.id,
                    message=f"Ребро ссылается на несуществующую конечную ноду: '{edge.target}'"
                ))
                continue

            src_node = node_map[edge.source]
            tgt_node = node_map[edge.target]

            src_def = NODE_REGISTRY.get(src_node.kind)
            tgt_def = NODE_REGISTRY.get(tgt_node.kind)

            if not src_def or not tgt_def:
                continue

            # Find source port
            src_port = next((p for p in src_def.outputs if p.id == edge.sourcePort), None)
            if not src_port:
                issues.append(WorkflowValidationIssue(
                    level="error",
                    edgeId=edge.id,
                    nodeId=edge.source,
                    portId=edge.sourcePort,
                    message=f"Выходной порт '{edge.sourcePort}' отсутствует в ноде '{src_node.title or src_node.kind}'"
                ))

            # Find target port
            tgt_port = next((p for p in tgt_def.inputs if p.id == edge.targetPort), None)
            if not tgt_port:
                issues.append(WorkflowValidationIssue(
                    level="error",
                    edgeId=edge.id,
                    nodeId=edge.target,
                    portId=edge.targetPort,
                    message=f"Входной порт '{edge.targetPort}' отсутствует в ноде '{tgt_node.title or tgt_node.kind}'"
                ))

            # Port type compatibility check
            if src_port and tgt_port:
                if not self._are_ports_compatible(src_port.type, tgt_port.type):
                    issues.append(WorkflowValidationIssue(
                        level="error",
                        edgeId=edge.id,
                        message=f"Несовместимые типы данных между портами: {src_port.type} -> {tgt_port.type}"
                    ))

            target_ports_connected[edge.target].add(edge.targetPort)
            adj[edge.source].append(edge.target)
            in_degree[edge.target] += 1

        # 3. Check required input ports for nodes
        for node in draft.nodes:
            node_def = NODE_REGISTRY.get(node.kind)
            if not node_def:
                continue

            # Project input must have connected trigger
            if node.kind == NodeKind.PROJECT_INPUT and "trigger" not in target_ports_connected[node.id]:
                issues.append(WorkflowValidationIssue(
                    level="error",
                    nodeId=node.id,
                    message="Нода Project Input требует подключенного триггера на входе"
                ))

            # Scanners must have connected artifact
            if node.kind in (NodeKind.SECRET_SCAN, NodeKind.SCA_SCAN, NodeKind.KOTLIN_SAST):
                if "artifact" not in target_ports_connected[node.id]:
                    issues.append(WorkflowValidationIssue(
                        level="error",
                        nodeId=node.id,
                        message=f"Нода {node_def.title} требует подключения артефакта проекта на входном порту"
                    ))

            # AI scan reads the same frozen artifact; findings are optional context.
            if node.kind == NodeKind.AI_SCAN and "artifact" not in target_ports_connected[node.id]:
                issues.append(WorkflowValidationIssue(
                    level="error",
                    nodeId=node.id,
                    message=(
                        "Нода AI Vulnerability Scanner требует подключения артефакта "
                        "проекта; findings_bundle опционален"
                    )
                ))

            # Custom check runs only against the frozen artifact snapshot.
            if node.kind == NodeKind.CUSTOM_CHECK:
                if "artifact" not in target_ports_connected[node.id]:
                    issues.append(WorkflowValidationIssue(
                        level="error",
                        nodeId=node.id,
                        message="Custom Check требует подключения артефакта проекта на входном порту",
                    ))
                definition_id = (node.config or {}).get("definitionId")
                if not isinstance(definition_id, str) or not definition_id.strip():
                    issues.append(WorkflowValidationIssue(
                        level="error",
                        nodeId=node.id,
                        message="Custom Check: выберите сохранённую проверку (definitionId)",
                    ))

            # MergeFindings should have at least one scan connected
            if node.kind == NodeKind.MERGE_FINDINGS:
                connected_scans = target_ports_connected[node.id].intersection({"scan_1", "scan_2", "scan_3"})
                if not connected_scans:
                    issues.append(WorkflowValidationIssue(
                        level="error",
                        nodeId=node.id,
                        message="Нода Merge Findings требует подключения хотя бы одного сканера"
                    ))

            # PolicyGate requires findings
            if node.kind == NodeKind.POLICY_GATE and "findings_bundle" not in target_ports_connected[node.id]:
                issues.append(WorkflowValidationIssue(
                    level="error",
                    nodeId=node.id,
                    message="Нода Policy Gate требует подключения потока находок (Findings Bundle)"
                ))

            # Condition requires input value
            if node.kind == NodeKind.CONDITION and "in" not in target_ports_connected[node.id]:
                issues.append(WorkflowValidationIssue(
                    level="error",
                    nodeId=node.id,
                    message="Нода Condition (If/Else) требует подключения проверяемого значения на входе"
                ))

        # 4. Cycle detection (Kahn's Algorithm for DAG)
        queue = deque([n_id for n_id, deg in in_degree.items() if deg == 0])
        visited_count = 0

        while queue:
            curr = queue.popleft()
            visited_count += 1
            for neighbor in adj[curr]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if visited_count < len(node_map):
            issues.append(WorkflowValidationIssue(
                level="error",
                message="В графе обнаружен замкнутый цикл! Допустимы только направленные ациклические графы (DAG)"
            ))

        valid = not any(i.level == "error" for i in issues)
        return WorkflowValidationResult(valid=valid, issues=issues)

    def _validate_ai_scan_config(self, node_id: str, config: Dict[str, object]) -> List[WorkflowValidationIssue]:
        """AI_SCAN config is budget-only; credentials and model routing are server-owned."""
        issues: List[WorkflowValidationIssue] = []
        forbidden = {
            "model", "apikey", "api_key", "token", "secret", "password",
            "baseurl", "base_url", "endpoint", "prompt", "providermodel",
        }
        for key in config:
            if str(key).strip().lower() in forbidden:
                issues.append(WorkflowValidationIssue(
                    level="error",
                    nodeId=node_id,
                    message=(
                        f"AI Vulnerability Scanner не принимает '{key}' в config: "
                        "провайдер, модель и ключ задаются только сервером"
                    ),
                ))

        profile = config.get("profile", "balanced")
        if not isinstance(profile, str) or profile not in {"fast", "balanced", "deep"}:
            issues.append(WorkflowValidationIssue(
                level="error", nodeId=node_id,
                message="AI Vulnerability Scanner: profile должен быть fast, balanced или deep",
            ))

        caps = {"maxFiles": 20, "maxBytes": 131072, "perFileBytes": 8192, "maxObservations": 30}
        for key, upper in caps.items():
            if key not in config:
                continue
            value = config.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > upper:
                issues.append(WorkflowValidationIssue(
                    level="error", nodeId=node_id,
                    message=(
                        f"AI Vulnerability Scanner: {key} должен быть целым числом "
                        f"от 1 до {upper}"
                    ),
                ))
        return issues

    def _are_ports_compatible(self, src: PortType, tgt: PortType) -> bool:
        if src == tgt:
            return True
        # Allow Condition Branch to connect to general inputs or BranchMerge
        if src == PortType.CONDITION_BRANCH or tgt == PortType.CONDITION_BRANCH:
            return True
        # Allow PolicyResult into Condition
        if src == PortType.POLICY_RESULT and tgt == PortType.POLICY_RESULT:
            return True
        return False
