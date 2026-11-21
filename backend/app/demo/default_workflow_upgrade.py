"""Explicit, revision-preserving upgrade of the system default workflow.

The API only seeds `wf_secure_pr_gate` when a workspace has no workflows. A
production workspace already has that row, so shipping a new factory default
must not silently replace user edits. This module:

* detects the exact frozen pre-AI_SCAN default and upgrades only that;
* leaves a user-modified draft untouched and reports it;
* can insert `AI_SCAN` surgically into a real user draft without replacing
  existing nodes, config, positions or the selected artifact;
* compares the active revision and writes the next one atomically, so a fresh
  user edit or a concurrent upgrade cannot be overwritten;
* exposes an explicit root command (dry-run by default, no DDL on dry-run).

Run from the backend root with an explicit target workspace::

    DATABASE_URL=... RUNTIME_WORKSPACE_ID=<workspace-uuid> \\
        python -m backend.app.demo.default_workflow_upgrade --workflow-id wf_secure_pr_gate

Add `--apply` to write the revision. `--force-user-edits` is the explicit
override for a factory-template replacement. For a real modified draft use
`--add-ai-scan --apply`: it preserves the existing draft and only adds the AI
node plus its edges.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from backend.app.contracts.node_registry import NODE_REGISTRY
from backend.app.contracts.schemas import (
    NodeKind,
    WorkflowDraft,
    WorkflowEdge,
    WorkflowNode,
)
from backend.app.demo.default_workflow import (
    SYSTEM_DEFAULT_WORKFLOW_ID,
    get_default_workflow_draft,
    get_previous_system_default_draft,
)
from backend.app.engine.validator import WorkflowValidator

# SHA-256 of the pre-AI_SCAN default draft. This is a tripwire: if someone
# edits the base builder later, automatic upgrades stop instead of guessing
# that a draft is "the old system default" and overwriting user edits.
_PREVIOUS_SYSTEM_DEFAULT_SHA256 = (
    "7da4dea0efc13a3f4a08fd2dd23c6116c5b9237c9eca8eb34c47d4f6b9375210"
)
_MAX_CAS_ATTEMPTS = 3


def _draft_sha256(draft: WorkflowDraft) -> str:
    payload = json.dumps(
        draft.model_dump(), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass
class DefaultUpgradeResult:
    workflow_id: str
    status: str  # missing | created | already_current | upgraded | would_upgrade |
    #              user_modified | forced_upgrade | would_force_upgrade |
    #              would_add_ai_scan | added_ai_scan | already_present |
    #              not_applicable | stale_upgrade | invalid_target |
    #              invalid_baseline | invalid_surgical
    applied: bool
    previous_revision_id: Optional[str]
    revision_id: Optional[str]
    reason: str
    diff: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)
    command: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "workflowId": self.workflow_id,
            "status": self.status,
            "applied": self.applied,
            "previousRevisionId": self.previous_revision_id,
            "revisionId": self.revision_id,
            "reason": self.reason,
            "diff": self.diff,
            "details": self.details,
            "command": self.command,
        }


def _normalized(draft: Dict[str, Any]) -> Dict[str, Any]:
    """Load through the schema so defaults/coercions compare like-for-like."""
    return WorkflowDraft(**draft).model_dump()


def _project_artifact_id(draft: WorkflowDraft) -> str:
    for node in draft.nodes:
        if node.kind == NodeKind.PROJECT_INPUT:
            return str((node.config or {}).get("artifactId") or "")
    return ""


def _with_artifact_id(draft: WorkflowDraft, artifact_id: str) -> WorkflowDraft:
    nodes = []
    for node in draft.nodes:
        if node.kind == NodeKind.PROJECT_INPUT:
            node = node.model_copy(update={
                "config": {**(node.config or {}), "artifactId": artifact_id},
            })
        nodes.append(node)
    return draft.model_copy(update={"nodes": nodes})


def _diff_summary(current: WorkflowDraft, baseline: WorkflowDraft) -> List[str]:
    diff: List[str] = []
    if current.name != baseline.name:
        diff.append("name")
    if current.description != baseline.description:
        diff.append("description")
    current_nodes = {node.id: node for node in current.nodes}
    baseline_nodes = {node.id: node for node in baseline.nodes}
    added = sorted(set(current_nodes) - set(baseline_nodes))
    removed = sorted(set(baseline_nodes) - set(current_nodes))
    if added:
        diff.append("added nodes: " + ", ".join(added))
    if removed:
        diff.append("removed nodes: " + ", ".join(removed))
    for node_id in sorted(set(current_nodes) & set(baseline_nodes)):
        left, right = current_nodes[node_id], baseline_nodes[node_id]
        if left.kind != right.kind:
            diff.append(f"{node_id}: kind")
        if left.title != right.title:
            diff.append(f"{node_id}: title")
        if left.config != right.config:
            diff.append(f"{node_id}: config")
        if left.position != right.position:
            diff.append(f"{node_id}: position")
    current_edges = {edge.id for edge in current.edges}
    baseline_edges = {edge.id for edge in baseline.edges}
    added_edges = sorted(current_edges - baseline_edges)
    removed_edges = sorted(baseline_edges - current_edges)
    if added_edges:
        diff.append("added edges: " + ", ".join(added_edges[:10]))
    if removed_edges:
        diff.append("removed edges: " + ", ".join(removed_edges[:10]))
    return diff[:20]


def _command(workflow_id: str, force: bool) -> str:
    suffix = " --force-user-edits" if force else ""
    return (
        "DATABASE_URL=<target-db> RUNTIME_WORKSPACE_ID=<workspace-uuid> "
        f"python -m backend.app.demo.default_workflow_upgrade "
        f"--workflow-id {workflow_id} --apply{suffix}"
    )


def _surgical_command(workflow_id: str) -> str:
    return (
        "DATABASE_URL=<target-db> RUNTIME_WORKSPACE_ID=<workspace-uuid> "
        f"python -m backend.app.demo.default_workflow_upgrade "
        f"--workflow-id {workflow_id} --add-ai-scan --apply"
    )


def _unique_id(base: str, used: set[str]) -> str:
    if base not in used:
        return base
    index = 2
    while f"{base}_{index}" in used:
        index += 1
    return f"{base}_{index}"


def _merge_node_for_ai(draft: WorkflowDraft) -> Optional[WorkflowNode]:
    merges = [node for node in draft.nodes if node.kind == NodeKind.MERGE_FINDINGS]
    return merges[0] if len(merges) == 1 else None


def _report_node_for_ai(draft: WorkflowDraft) -> Optional[WorkflowNode]:
    reports = [node for node in draft.nodes if node.kind == NodeKind.BUILD_REPORT]
    if not reports:
        return None
    if len(reports) == 1:
        return reports[0]
    # Prefer the report that consumes the policy gate, not a side report.
    policy_targets = {
        edge.target for edge in draft.edges if edge.targetPort == "policy_result"
    }
    candidates = [node for node in reports if node.id in policy_targets]
    return candidates[0] if len(candidates) == 1 else None


def _surgical_draft(
    current: WorkflowDraft,
) -> Tuple[Optional[WorkflowDraft], str, Dict[str, Any]]:
    """Add AI_SCAN to an existing draft without touching existing nodes."""
    projects = [node for node in current.nodes if node.kind == NodeKind.PROJECT_INPUT]
    if len(projects) != 1:
        return None, (
            "Нужен ровно один Project Input с артефактом; "
            f"найдено {len(projects)}. Автоматическое подключение неоднозначно."
        ), {}
    project = projects[0]

    used_node_ids = {node.id for node in current.nodes}
    ai_id = _unique_id("node_ai_scan", used_node_ids)
    merge = _merge_node_for_ai(current)
    report = _report_node_for_ai(current)
    anchor = merge or project
    try:
        x = float(anchor.position.get("x", 100.0)) + 240.0
        y = float(anchor.position.get("y", 100.0)) + 180.0
    except (TypeError, ValueError):
        x, y = 1000.0, 600.0

    ai_node = WorkflowNode(
        id=ai_id,
        kind=NodeKind.AI_SCAN,
        title=NODE_REGISTRY[NodeKind.AI_SCAN].title,
        position={"x": x, "y": y},
        config=copy.deepcopy(NODE_REGISTRY[NodeKind.AI_SCAN].default_config),
    )

    used_edge_ids = {edge.id for edge in current.edges}
    edges = list(current.edges)
    new_edges: List[WorkflowEdge] = []

    artifact_edge_id = _unique_id(f"e_{project.id}_{ai_id}", used_edge_ids)
    used_edge_ids.add(artifact_edge_id)
    new_edges.append(WorkflowEdge(
        id=artifact_edge_id,
        source=project.id, sourcePort="artifact",
        target=ai_id, targetPort="artifact",
    ))

    if merge is not None:
        merge_edge_id = _unique_id(f"e_{merge.id}_{ai_id}", used_edge_ids)
        used_edge_ids.add(merge_edge_id)
        new_edges.append(WorkflowEdge(
            id=merge_edge_id,
            source=merge.id, sourcePort="findings_bundle",
            target=ai_id, targetPort="findings_bundle",
        ))

    if report is not None:
        report_def = NODE_REGISTRY.get(report.kind)
        if report_def and any(port.id == "ai_scan_result" for port in report_def.inputs):
            report_edge_id = _unique_id(f"e_{ai_id}_{report.id}", used_edge_ids)
            used_edge_ids.add(report_edge_id)
            new_edges.append(WorkflowEdge(
                id=report_edge_id,
                source=ai_id, sourcePort="ai_scan_result",
                target=report.id, targetPort="ai_scan_result",
            ))

    surgical = current.model_copy(update={
        "nodes": [*current.nodes, ai_node],
        "edges": [*edges, *new_edges],
    })
    details = {
        "preservedNodes": len(current.nodes),
        "preservedEdges": len(current.edges),
        "artifactId": _project_artifact_id(current),
        "findingsSource": merge.id if merge else None,
        "reportNode": report.id if report else None,
        "addedNode": ai_id,
        "addedEdges": [edge.id for edge in new_edges],
    }
    return surgical, "", details


def _validation_reason(draft: WorkflowDraft) -> str:
    validation = WorkflowValidator().validate(draft)
    if validation.valid:
        return ""
    return "; ".join(issue.message for issue in validation.issues[:5])


def upgrade_system_default_workflow(
    db: Any,
    *,
    workflow_id: str = SYSTEM_DEFAULT_WORKFLOW_ID,
    apply: bool = False,
    force_user_edits: bool = False,
) -> DefaultUpgradeResult:
    """Upgrade the untouched factory default, or report why it was skipped.

    A new workflow revision is always created; no revision row is mutated and
    no user draft is overwritten unless `force_user_edits` is explicit. The
    active revision is compared and written under one row lock, so a racing
    user edit or a second upgrade cannot be overwritten.
    """
    command = _command(workflow_id, force_user_edits)
    surgical_command = _surgical_command(workflow_id)
    target = get_default_workflow_draft()
    baseline = get_previous_system_default_draft()
    target_dump = target.model_dump()
    baseline_dump = _normalized(_with_artifact_id(baseline, "").model_dump())

    if _draft_sha256(baseline) != _PREVIOUS_SYSTEM_DEFAULT_SHA256:
        return DefaultUpgradeResult(
            workflow_id=workflow_id,
            status="invalid_baseline",
            applied=False,
            previous_revision_id=None,
            revision_id=None,
            reason=(
                "Замороженный прежний системный дефолт изменился в коде: "
                "автоматическое обновление остановлено, чтобы не перезаписать "
                "пользовательские правки. Зафиксируйте новую базовую ревизию явно."
            ),
            command=command,
        )

    validation_reason = _validation_reason(target)
    if validation_reason:
        return DefaultUpgradeResult(
            workflow_id=workflow_id,
            status="invalid_target",
            applied=False,
            previous_revision_id=None,
            revision_id=None,
            reason="Новый системный дефолт не прошёл валидатор: " + validation_reason,
            command=command,
        )

    row = db.get_workflow(workflow_id)
    for _ in range(_MAX_CAS_ATTEMPTS):
        previous_revision = (row or {}).get("latest_revision_id")

        if not row or not row.get("draft"):
            if not apply:
                return DefaultUpgradeResult(
                    workflow_id=workflow_id,
                    status="missing",
                    applied=False,
                    previous_revision_id=previous_revision,
                    revision_id=None,
                    reason=(
                        "Сценарий отсутствует; при --apply будет создан как системный "
                        "дефолт в текущем workspace."
                    ),
                    command=command,
                )
            revision = db.save_workflow_if_latest(
                workflow_id, None, target.name, target.description, target_dump,
            )
            if revision["revision_id"] is not None:
                return DefaultUpgradeResult(
                    workflow_id=workflow_id,
                    status="created",
                    applied=True,
                    previous_revision_id=previous_revision,
                    revision_id=revision["revision_id"],
                    reason="Системный дефолт создан новой ревизией; пользовательских правок не было.",
                    command=command,
                )
            row = {
                "latest_revision_id": revision["latest_revision_id"],
                "draft": revision["draft"],
            }
            continue

        current = WorkflowDraft(**row["draft"])
        # A user-selected artifact on Project Input is a binding, not a change
        # to the workflow shape: ignore it when comparing factory defaults.
        current_shape_dump = _normalized(_with_artifact_id(current, "").model_dump())
        target_shape_dump = _normalized(_with_artifact_id(target, "").model_dump())

        if current_shape_dump == target_shape_dump:
            return DefaultUpgradeResult(
                workflow_id=workflow_id,
                status="already_current",
                applied=False,
                previous_revision_id=previous_revision,
                revision_id=previous_revision,
                reason="Активная ревизия уже совпадает с новым системным дефолтом.",
                command=command,
            )

        is_untouched_system_default = current_shape_dump == baseline_dump
        if not is_untouched_system_default and not force_user_edits:
            ai_present = any(node.kind == NodeKind.AI_SCAN for node in current.nodes)
            return DefaultUpgradeResult(
                workflow_id=workflow_id,
                status="user_modified",
                applied=False,
                previous_revision_id=previous_revision,
                revision_id=previous_revision,
                reason=(
                    "Активная ревизия отличается от прежнего системного дефолта: "
                    "правки пользователя сохранены. Замена фабричным шаблоном не "
                    "выполнена. Для добавления одного AI_SCAN без перезаписи "
                    "существующих нод и артефакта используйте --add-ai-scan --apply."
                ),
                diff=_diff_summary(
                    _with_artifact_id(current, ""), _with_artifact_id(baseline, ""),
                ),
                details={"aiScanPresent": ai_present},
                command=surgical_command,
            )

        artifact_id = _project_artifact_id(current)
        upgraded = _with_artifact_id(target, artifact_id)

        if not apply:
            return DefaultUpgradeResult(
                workflow_id=workflow_id,
                status="would_upgrade" if not force_user_edits else "would_force_upgrade",
                applied=False,
                previous_revision_id=previous_revision,
                revision_id=None,
                reason=(
                    "Прежний системный дефолт без AI_SCAN будет заменён новой ревизией."
                    if not force_user_edits
                    else "Правки пользователя будут перезаписаны новой ревизией по явному --force-user-edits."
                ),
                diff=_diff_summary(
                    _with_artifact_id(target, ""), _with_artifact_id(current, ""),
                ),
                command=command,
            )

        revision = db.save_workflow_if_latest(
            workflow_id, previous_revision, upgraded.name, upgraded.description,
            upgraded.model_dump(),
        )
        if revision["revision_id"] is not None:
            forced = force_user_edits and not is_untouched_system_default
            return DefaultUpgradeResult(
                workflow_id=workflow_id,
                status="forced_upgrade" if forced else "upgraded",
                applied=True,
                previous_revision_id=previous_revision,
                revision_id=revision["revision_id"],
                reason=(
                    "Новая ревизия создана поверх пользовательской по явному --force-user-edits."
                    if forced
                    else "Прежний системный дефолт обновлён новой ревизией; старые ревизии сохранены."
                ),
                diff=_diff_summary(
                    _with_artifact_id(target, ""), _with_artifact_id(current, ""),
                ),
                command=command,
            )
        # CAS lost: another writer moved the active revision. Re-evaluate from
        # the state returned by that same transaction, without a second read.
        row = {
            "latest_revision_id": revision["latest_revision_id"],
            "draft": revision["draft"],
        }

    return DefaultUpgradeResult(
        workflow_id=workflow_id,
        status="stale_upgrade",
        applied=False,
        previous_revision_id=None,
        revision_id=None,
        reason=(
            "Активная ревизия менялась во время обновления несколько раз. "
            "Ничего не записано; повторите dry-run."
        ),
        command=command,
    )


def add_ai_scan_surgically(
    db: Any,
    *,
    workflow_id: str = SYSTEM_DEFAULT_WORKFLOW_ID,
    apply: bool = False,
) -> DefaultUpgradeResult:
    """Insert AI_SCAN into a modified draft, preserving everything else.

    Only a new node and its edges are added. Existing node ids, kinds, titles,
    configs, positions, the selected artifact and all existing edges stay
    byte-identical (apart from Pydantic serialization).
    """
    command = _surgical_command(workflow_id)

    row = db.get_workflow(workflow_id)
    for _ in range(_MAX_CAS_ATTEMPTS):
        previous_revision = (row or {}).get("latest_revision_id")
        if not row or not row.get("draft"):
            return DefaultUpgradeResult(
                workflow_id=workflow_id,
                status="missing",
                applied=False,
                previous_revision_id=previous_revision,
                revision_id=None,
                reason="Сценарий отсутствует; хирургическое добавление невозможно.",
                command=command,
            )

        current = WorkflowDraft(**row["draft"])
        if any(node.kind == NodeKind.AI_SCAN for node in current.nodes):
            return DefaultUpgradeResult(
                workflow_id=workflow_id,
                status="already_present",
                applied=False,
                previous_revision_id=previous_revision,
                revision_id=previous_revision,
                reason="В активной ревизии уже есть AI_SCAN; ничего не изменено.",
                command=command,
            )

        surgical, refusal, details = _surgical_draft(current)
        if surgical is None:
            return DefaultUpgradeResult(
                workflow_id=workflow_id,
                status="not_applicable",
                applied=False,
                previous_revision_id=previous_revision,
                revision_id=previous_revision,
                reason=refusal,
                command=command,
            )

        validation_reason = _validation_reason(surgical)
        if validation_reason:
            return DefaultUpgradeResult(
                workflow_id=workflow_id,
                status="invalid_surgical",
                applied=False,
                previous_revision_id=previous_revision,
                revision_id=None,
                reason=(
                    "Хирургическая вставка AI_SCAN не прошла валидатор; "
                    "ничего не записано: " + validation_reason
                ),
                diff=_diff_summary(surgical, current),
                details=details,
                command=command,
            )

        diff = _diff_summary(surgical, current)
        if not apply:
            return DefaultUpgradeResult(
                workflow_id=workflow_id,
                status="would_add_ai_scan",
                applied=False,
                previous_revision_id=previous_revision,
                revision_id=None,
                reason=(
                    "AI_SCAN будет добавлен одной новой ревизией; существующие "
                    "ноды, конфигурация, позиции и artifactId сохраняются."
                ),
                diff=diff,
                details=details,
                command=command,
            )

        revision = db.save_workflow_if_latest(
            workflow_id, previous_revision, surgical.name, surgical.description,
            surgical.model_dump(),
        )
        if revision["revision_id"] is not None:
            return DefaultUpgradeResult(
                workflow_id=workflow_id,
                status="added_ai_scan",
                applied=True,
                previous_revision_id=previous_revision,
                revision_id=revision["revision_id"],
                reason=(
                    "AI_SCAN добавлен новой ревизией; существующие ноды, "
                    "конфигурация, позиции и artifactId сохранены."
                ),
                diff=diff,
                details=details,
                command=command,
            )
        # CAS lost: the user or another upgrade moved the revision. Re-evaluate
        # from the state returned by the same transaction.
        row = {
            "latest_revision_id": revision["latest_revision_id"],
            "draft": revision["draft"],
        }

    return DefaultUpgradeResult(
        workflow_id=workflow_id,
        status="stale_upgrade",
        applied=False,
        previous_revision_id=None,
        revision_id=None,
        reason=(
            "Активная ревизия менялась во время хирургической вставки несколько "
            "раз. Ничего не записано; повторите dry-run."
        ),
        command=command,
    )


def _exit_code(result: DefaultUpgradeResult, apply: bool) -> int:
    if result.status in {
        "invalid_target", "invalid_baseline", "invalid_surgical",
        "not_applicable", "stale_upgrade",
    }:
        return 1
    if apply and not result.applied and result.status not in {
        "already_current", "already_present",
    }:
        return 1
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Upgrade the system default workflow without touching user drafts",
    )
    parser.add_argument("--workflow-id", default=SYSTEM_DEFAULT_WORKFLOW_ID)
    parser.add_argument("--apply", action="store_true", help="write a new revision")
    parser.add_argument(
        "--force-user-edits", action="store_true",
        help="explicitly replace a modified draft with the factory template",
    )
    parser.add_argument(
        "--add-ai-scan", action="store_true",
        help="insert only AI_SCAN into a modified draft, preserving existing nodes",
    )
    parser.add_argument(
        "--workspace-id",
        help="RUNTIME_WORKSPACE_ID for this run (must already be a UUID)",
    )
    args = parser.parse_args(argv)

    workspace = (args.workspace_id or os.getenv("RUNTIME_WORKSPACE_ID", "")).strip()
    if not workspace:
        print(json.dumps({
            "status": "error",
            "reason": (
                "нужен явный workspace: передайте --workspace-id <uuid> или "
                "задайте RUNTIME_WORKSPACE_ID"
            ),
        }, ensure_ascii=False, indent=2))
        return 2
    try:
        uuid.UUID(workspace)
    except ValueError:
        print(json.dumps({
            "status": "error",
            "reason": "workspace id должен быть UUID",
        }, ensure_ascii=False, indent=2))
        return 2
    os.environ["RUNTIME_WORKSPACE_ID"] = workspace

    try:
        from backend.app.storage.database import DatabaseRepository

        # Dry-run must not run DDL or insert a workspace row. The same explicit
        # workspace is used for the read and, with --apply, the CAS write.
        db = DatabaseRepository(initialize=args.apply)
        if args.add_ai_scan:
            result = add_ai_scan_surgically(
                db, workflow_id=args.workflow_id, apply=args.apply,
            )
        else:
            result = upgrade_system_default_workflow(
                db,
                workflow_id=args.workflow_id,
                apply=args.apply,
                force_user_edits=args.force_user_edits,
            )
    except Exception as exc:  # noqa: BLE001 - CLI must not leak credentials/DSN
        from backend.app.ai.provider import mask_sensitive_text

        print(json.dumps({
            "status": "error",
            "reason": mask_sensitive_text(f"{type(exc).__name__}: {exc}"),
        }, ensure_ascii=False, indent=2))
        return 2

    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return _exit_code(result, args.apply)


if __name__ == "__main__":
    raise SystemExit(main())