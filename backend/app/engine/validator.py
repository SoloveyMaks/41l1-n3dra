from collections import defaultdict, deque
from typing import List

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
        scan_engines = {}
        for node in draft.nodes:
            if node.kind not in scan_kinds:
                continue
            engine = str((node.config or {}).get("engine") or "backend")
            if engine not in {"backend", "team_b"}:
                issues.append(WorkflowValidationIssue(
                    level="error", nodeId=node.id,
                    message=f"Неизвестный engine '{engine}': допустимы backend и team_b",
                ))
            scan_engines[node.id] = engine
        if len(set(scan_engines.values())) > 1:
            issues.append(WorkflowValidationIssue(
                level="error",
                message=(
                    "Один run не может смешивать engine сканеров: "
                    + ", ".join(f"{node_id}={engine}" for node_id, engine in sorted(scan_engines.items()))
                    + ". Выберите один engine для всех scan nodes."
                ),
            ))

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
