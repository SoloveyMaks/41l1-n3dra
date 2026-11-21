"""Explicit, revision-preserving upgrade of the system default workflow.

The API only seeds `wf_secure_pr_gate` when a workspace has no workflows. A
production workspace already has that row, so shipping a new factory default
must not silently replace user edits. This module:

* detects the exact frozen pre-AI_SCAN default and upgrades only that;
* leaves a user-modified draft untouched and reports it;
* adds a new revision instead of mutating history;
* exposes an explicit root command for production (dry-run by default).

Run from the backend root with the target workspace in the environment::

    DATABASE_URL=... RUNTIME_WORKSPACE_ID=<workspace-uuid> \\
        python -m backend.app.demo.default_workflow_upgrade --workflow-id wf_secure_pr_gate

Add `--apply` to write the revision. `--force-user-edits` is the explicit
override for a draft that no longer matches the old factory default.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from backend.app.contracts.schemas import NodeKind, WorkflowDraft
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


def _draft_sha256(draft: WorkflowDraft) -> str:
    payload = json.dumps(
        draft.model_dump(), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass
class DefaultUpgradeResult:
    workflow_id: str
    status: str  # missing | created | already_current | upgraded | would_upgrade |
    #              user_modified | forced_upgrade | invalid_target | invalid_baseline
    applied: bool
    previous_revision_id: Optional[str]
    revision_id: Optional[str]
    reason: str
    diff: List[str] = field(default_factory=list)
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
    if current_edges != baseline_edges:
        diff.append("edges")
    return diff[:20]


def _command(workflow_id: str, force: bool) -> str:
    suffix = " --force-user-edits" if force else ""
    return (
        "DATABASE_URL=<target-db> RUNTIME_WORKSPACE_ID=<workspace-uuid> "
        f"python -m backend.app.demo.default_workflow_upgrade "
        f"--workflow-id {workflow_id} --apply{suffix}"
    )


def upgrade_system_default_workflow(
    db: Any,
    *,
    workflow_id: str = SYSTEM_DEFAULT_WORKFLOW_ID,
    apply: bool = False,
    force_user_edits: bool = False,
) -> DefaultUpgradeResult:
    """Upgrade the untouched factory default, or report why it was skipped.

    A new workflow revision is always created; no revision row is mutated and
    no user draft is overwritten unless `force_user_edits` is explicit.
    """
    row = db.get_workflow(workflow_id)
    previous_revision = (row or {}).get("latest_revision_id")
    command = _command(workflow_id, force_user_edits)
    target = get_default_workflow_draft()
    baseline = get_previous_system_default_draft()
    target_dump = target.model_dump()
    baseline_dump = _normalized(_with_artifact_id(baseline, "").model_dump())

    if _draft_sha256(baseline) != _PREVIOUS_SYSTEM_DEFAULT_SHA256:
        return DefaultUpgradeResult(
            workflow_id=workflow_id,
            status="invalid_baseline",
            applied=False,
            previous_revision_id=previous_revision,
            revision_id=None,
            reason=(
                "Замороженный прежний системный дефолт изменился в коде: "
                "автоматическое обновление остановлено, чтобы не перезаписать "
                "пользовательские правки. Зафиксируйте новую базовую ревизию явно."
            ),
            command=command,
        )

    validation = WorkflowValidator().validate(target)
    if not validation.valid:
        return DefaultUpgradeResult(
            workflow_id=workflow_id,
            status="invalid_target",
            applied=False,
            previous_revision_id=previous_revision,
            revision_id=None,
            reason="Новый системный дефолт не прошёл валидатор: " + "; ".join(
                issue.message for issue in validation.issues[:5]
            ),
            command=command,
        )

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
        revision = db.save_workflow(
            workflow_id, target.name, target.description, target_dump,
        )
        return DefaultUpgradeResult(
            workflow_id=workflow_id,
            status="created",
            applied=True,
            previous_revision_id=previous_revision,
            revision_id=revision,
            reason="Системный дефолт создан новой ревизией; пользовательских правок не было.",
            command=command,
        )

    current = WorkflowDraft(**row["draft"])
    # A user-selected artifact on Project Input is a binding, not a change to
    # the workflow shape: ignore it when comparing against factory defaults.
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
        return DefaultUpgradeResult(
            workflow_id=workflow_id,
            status="user_modified",
            applied=False,
            previous_revision_id=previous_revision,
            revision_id=previous_revision,
            reason=(
                "Активная ревизия отличается от прежнего системного дефолта: "
                "правки пользователя сохранены. Для принудительной новой "
                "ревизии нужен явный --force-user-edits."
            ),
            diff=_diff_summary(_with_artifact_id(current, ""), _with_artifact_id(baseline, "")),
            command=command,
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
            diff=_diff_summary(_with_artifact_id(target, ""), _with_artifact_id(current, "")),
            command=command,
        )

    revision = db.save_workflow(
        workflow_id, upgraded.name, upgraded.description, upgraded.model_dump(),
    )
    forced = force_user_edits and not is_untouched_system_default
    return DefaultUpgradeResult(
        workflow_id=workflow_id,
        status="forced_upgrade" if forced else "upgraded",
        applied=True,
        previous_revision_id=previous_revision,
        revision_id=revision,
        reason=(
            "Новая ревизия создана поверх пользовательской по явному --force-user-edits."
            if forced
            else "Прежний системный дефолт обновлён новой ревизией; старые ревизии сохранены."
        ),
        diff=_diff_summary(_with_artifact_id(target, ""), _with_artifact_id(current, "")),
        command=command,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Upgrade the untouched system default workflow without touching user drafts",
    )
    parser.add_argument("--workflow-id", default=SYSTEM_DEFAULT_WORKFLOW_ID)
    parser.add_argument("--apply", action="store_true", help="write a new revision")
    parser.add_argument(
        "--force-user-edits", action="store_true",
        help="explicitly overwrite a modified draft with a new revision",
    )
    parser.add_argument(
        "--workspace-id",
        help="RUNTIME_WORKSPACE_ID for this run (must already be a UUID)",
    )
    args = parser.parse_args(argv)

    if args.workspace_id:
        os.environ["RUNTIME_WORKSPACE_ID"] = args.workspace_id
    try:
        from backend.app.storage.database import DatabaseRepository

        db = DatabaseRepository()
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
    return 0 if result.status not in {"invalid_target", "invalid_baseline"} else 1


if __name__ == "__main__":
    raise SystemExit(main())