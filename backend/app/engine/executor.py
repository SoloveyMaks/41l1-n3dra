import asyncio
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.app.ai.scan_service import AiScanService
from backend.app.ai.service import AiService
from backend.app.analyzers.redaction import sanitize_fact_evidence, sanitize_sources, secret_ranges
from backend.app.contracts.schemas import (
    AiScanResult, Coverage, DeliveryResult, Diagnostic, ExecutionStatus, ExplainResult,
    Finding, FindingsBundle, NodeKind, NodeRunStatus, PolicyResult,
    ScanResult, SecurityVerdict, Severity, WorkflowDraft, WorkflowNode
)
from backend.app.codefacts.extractor import RULE_DEFINITIONS, extract_facts
from backend.app.codefacts.identity import run_uuid, workspace_id
from backend.app.codefacts.map_builder import build_run_map, ensure_artifact_meta
from backend.app.codefacts.publish import publish_run_artifact
from backend.app.codefacts.source import load_snapshot_sources
from backend.app.codefacts.store import CodeFactsStore
from backend.app.engine.notify_policy import validate_webhook_url
from backend.app.engine.sse import SSEBroker
from backend.app.engine.validator import WorkflowValidator
from backend.app.integrations.full_analysis import merge_extra_findings, scan_category_full
from backend.app.integrations.team_b import TeamBAdapter
from backend.app.notifications.webhook import WebhookNotifier
from backend.app.reports.builder import ReportBuilder
from backend.app.storage.database import DatabaseRepository


logger = logging.getLogger("WorkflowExecutor")


class ConditionEvaluationError(Exception):
    pass


class WorkflowExecutor:
    def __init__(self, db: DatabaseRepository, sse: SSEBroker):
        self.db = db
        self.sse = sse
        self.validator = WorkflowValidator()
        self.ai_service = AiService()
        self.ai_scan_service = AiScanService()
        self.report_builder = ReportBuilder()
        self.notifier = WebhookNotifier()
        self.team_b = TeamBAdapter()
        try:
            concurrency = max(1, int(os.getenv("CG_MAX_CONCURRENT_RUNS", "2")))
        except ValueError:
            concurrency = 2
        self._run_sem = asyncio.Semaphore(concurrency)

    async def execute_run(self, run_id: str, draft: WorkflowDraft, artifact_id: Optional[str],
                          trigger_type: str = "manual", workflow_id: Optional[str] = None,
                          revision_id: Optional[str] = None) -> None:
        """Single bounded entrypoint for manual and scheduled runs."""
        async with self._run_sem:
            try:
                await self._execute_run(
                    run_id, draft, artifact_id, trigger_type, workflow_id, revision_id
                )
            except Exception:  # noqa: BLE001 - run must not stay queued/running
                logger.exception("Run %s failed unexpectedly", run_id)
                self.db.update_run_status(
                    run_id, execution_status="failed", security_verdict="inconclusive",
                    finished_at=datetime.now(timezone.utc).isoformat()
                )
                try:
                    await self.sse.emit_event(run_id, "run_status", {
                        "status": "failed", "verdict": "inconclusive",
                        "error": "Внутренняя ошибка выполнения",
                    })
                except Exception:  # noqa: BLE001
                    logger.exception("Failed to emit failure event for %s", run_id)
            finally:
                self.team_b.release(run_id)

    async def _execute_run(self, run_id: str, draft: WorkflowDraft, artifact_id: Optional[str],
                           trigger_type: str = "manual", workflow_id: Optional[str] = None,
                           revision_id: Optional[str] = None) -> None:
        start_time_iso = datetime.now(timezone.utc).isoformat()
        if not self.db.claim_run_start(run_id, start_time_iso):
            # A second dispatcher may hold the same run through a stale watch
            # lease: only the caller that promoted queued->running executes it.
            logger.info("Run %s is not queued anymore; skipping duplicate start", run_id)
            return
        run_row = self.db.get_run(run_id) or {}
        workflow_id = workflow_id or str(run_row.get("workflow_id") or "workflow-unknown")
        revision_id = revision_id or str(run_row.get("revision_id") or "revision-unknown")
        workflow = self.db.get_workflow(workflow_id) or {}
        workflow_name = str(workflow.get("name") or "Security Scan")
        await self.sse.emit_event(run_id, "run_status", {"status": "running", "trigger": trigger_type})

        # Pre-validate draft
        val_res = self.validator.validate(draft)
        if not val_res.valid:
            error_msgs = [i.message for i in val_res.issues]
            self.db.update_run_status(
                run_id, execution_status="failed", security_verdict="inconclusive",
                finished_at=datetime.now(timezone.utc).isoformat()
            )
            await self.sse.emit_event(run_id, "run_status", {
                "status": "failed", "verdict": "inconclusive", "errors": error_msgs
            })
            return

        # Build graph execution structures
        node_map: Dict[str, WorkflowNode] = {n.id: n for n in draft.nodes}
        incoming_edges: Dict[str, List[Any]] = {n.id: [] for n in draft.nodes}
        outgoing_edges: Dict[str, List[Any]] = {n.id: [] for n in draft.nodes}

        for edge in draft.edges:
            incoming_edges[edge.target].append(edge)
            outgoing_edges[edge.source].append(edge)

        # Execution state
        node_statuses: Dict[str, NodeRunStatus] = {n.id: NodeRunStatus.PENDING for n in draft.nodes}
        node_outputs: Dict[str, Dict[str, Any]] = {n.id: {} for n in draft.nodes}
        edge_active_states: Dict[str, Optional[bool]] = {e.id: None for e in draft.edges}  # None=pending, True=active, False=inactive

        # Shared execution context for accumulating findings & results
        findings_bundle: Optional[FindingsBundle] = None
        policy_result: Optional[PolicyResult] = None
        explain_result: Optional[ExplainResult] = None

        # Topological execution loop
        while True:
            # Find next ready node
            ready_node_id = None
            for n_id, status in node_statuses.items():
                if status != NodeRunStatus.PENDING:
                    continue

                in_edges = incoming_edges[n_id]
                if not in_edges:
                    ready_node_id = n_id
                    break

                # For BranchMerge: ready if all incoming edges resolved (either True or False)
                if node_map[n_id].kind == NodeKind.BRANCH_MERGE:
                    all_resolved = all(edge_active_states[e.id] is not None for e in in_edges)
                    if all_resolved:
                        ready_node_id = n_id
                        break
                else:
                    # Normal node: all incoming edges must be resolved
                    all_resolved = all(edge_active_states[e.id] is not None for e in in_edges)
                    if all_resolved:
                        ready_node_id = n_id
                        break

            if not ready_node_id:
                break

            curr_node = node_map[ready_node_id]

            # Check if this node should be skipped (e.g. if all active incoming edges are False/inactive)
            in_edges = incoming_edges[ready_node_id]
            should_skip = False
            if in_edges:
                if curr_node.kind == NodeKind.BRANCH_MERGE:
                    # Skipped only if NONE of incoming edges are active
                    active_count = sum(1 for e in in_edges if edge_active_states[e.id] is True)
                    if active_count == 0:
                        should_skip = True
                else:
                    # For normal nodes, if any incoming edge is inactive, check if all are inactive
                    active_in_edges = [e for e in in_edges if edge_active_states[e.id] is True]
                    if not active_in_edges:
                        should_skip = True

            if should_skip:
                node_statuses[ready_node_id] = NodeRunStatus.SKIPPED
                self._record_node_run(run_id, curr_node, NodeRunStatus.SKIPPED, 0.0, None, {}, {})
                await self.sse.emit_event(run_id, "node_status", {"nodeId": ready_node_id, "status": "skipped"}, ready_node_id)
                # Deactivate all outgoing edges
                for out_e in outgoing_edges[ready_node_id]:
                    edge_active_states[out_e.id] = False
                continue

            # Execute node!
            node_statuses[ready_node_id] = NodeRunStatus.RUNNING
            await self.sse.emit_event(run_id, "node_status", {"nodeId": ready_node_id, "status": "running"}, ready_node_id)
            node_start_time = time.time()

            # Gather input data from active incoming edges
            node_inputs: Dict[str, Any] = {}
            for in_e in in_edges:
                if edge_active_states[in_e.id] is True:
                    src_val = node_outputs[in_e.source].get(in_e.sourcePort)
                    node_inputs[in_e.targetPort] = src_val

            try:
                outputs, branch_decision = await self._run_node_logic(
                    run_id, curr_node, node_inputs, artifact_id, trigger_type,
                    findings_bundle, policy_result, explain_result,
                    workflow_id, revision_id, workflow_name,
                )

                duration_ms = (time.time() - node_start_time) * 1000
                node_statuses[ready_node_id] = NodeRunStatus.SUCCEEDED
                node_outputs[ready_node_id] = outputs

                # Update shared execution pointers
                if "findings_bundle" in outputs:
                    findings_bundle = outputs["findings_bundle"]
                if "policy_result" in outputs:
                    policy_result = outputs["policy_result"]
                if "explain_result" in outputs:
                    explain_result = outputs["explain_result"]
                # Handle outgoing edges
                for out_e in outgoing_edges[ready_node_id]:
                    if curr_node.kind == NodeKind.CONDITION:
                        # Activate strictly the chosen branch
                        if out_e.sourcePort == "true_branch":
                            edge_active_states[out_e.id] = (branch_decision is True)
                        elif out_e.sourcePort == "false_branch":
                            edge_active_states[out_e.id] = (branch_decision is False)
                        else:
                            edge_active_states[out_e.id] = True
                    else:
                        edge_active_states[out_e.id] = True

                    # Emit edge data event for inspector
                    edge_val = outputs.get(out_e.sourcePort)
                    if edge_val is not None and edge_active_states[out_e.id] is True:
                        masked_edge_data = self._mask_data_for_ui(edge_val)
                        await self.sse.emit_event(run_id, "edge_data", {
                            "edgeId": out_e.id,
                            "source": out_e.source,
                            "target": out_e.target,
                            "data": masked_edge_data
                        })

                # Record in DB and emit SSE
                masked_out = self._mask_data_for_ui(outputs)
                masked_in = self._mask_data_for_ui(node_inputs)
                self._record_node_run(run_id, curr_node, NodeRunStatus.SUCCEEDED, duration_ms, None, masked_in, masked_out)
                await self.sse.emit_event(run_id, "node_status", {
                    "nodeId": ready_node_id,
                    "status": "succeeded",
                    "duration_ms": round(duration_ms, 2)
                }, ready_node_id)

            except Exception as e:
                duration_ms = (time.time() - node_start_time) * 1000
                node_statuses[ready_node_id] = NodeRunStatus.FAILED
                err_msg = str(e)

                # Record failed node in DB
                self._record_node_run(run_id, curr_node, NodeRunStatus.FAILED, duration_ms, err_msg, {}, {})
                await self.sse.emit_event(run_id, "node_status", {
                    "nodeId": ready_node_id,
                    "status": "failed",
                    "error": err_msg,
                    "duration_ms": round(duration_ms, 2)
                }, ready_node_id)

                # A condition error fails execution, but finalization still runs:
                # report executionStatus/publication must not be left pending.
                # Notify failures are separate: they use DeliveryResult and are
                # not counted as analysis failure.
                for out_e in outgoing_edges[ready_node_id]:
                    edge_active_states[out_e.id] = False

        # Finalize run status and verdict
        if findings_bundle is None:
            final_verdict = SecurityVerdict.INCONCLUSIVE
        else:
            final_verdict = policy_result.verdict if policy_result else SecurityVerdict.INCONCLUSIVE
        analysis_failed = any(
            status == NodeRunStatus.FAILED
            and node_map[node_id].kind not in {NodeKind.NOTIFY, NodeKind.AI_SCAN}
            for node_id, status in node_statuses.items()
        )
        final_exec_status = (
            ExecutionStatus.FAILED
            if findings_bundle is None or analysis_failed
            else ExecutionStatus.SUCCEEDED
        )

        counts = {
            "total": len(findings_bundle.findings) if findings_bundle else 0,
            "critical": policy_result.critical_count if policy_result else 0,
            "high": policy_result.high_count if policy_result else 0,
            "medium": policy_result.medium_count if policy_result else 0,
            "low": policy_result.low_count if policy_result else 0,
            "info": policy_result.info_count if policy_result else 0
        }

        # A policy PASS is not a full security verdict: canonical coverage must
        # also be complete and expected scopes verified. Scanner-runtime
        # coverage alone cannot prove the skipped/runtime scopes.
        if final_verdict == SecurityVerdict.PASS:
            # Defense only: the policy gate already applies canonical evidence.
            canonical = self._canonical_coverage_facts(run_id)
            if not canonical["verified"]:
                final_verdict = SecurityVerdict.INCONCLUSIVE
                if policy_result is not None:
                    policy_result = policy_result.model_copy(update={
                        "verdict": final_verdict,
                        "reasons": [
                            *(r for r in policy_result.reasons if not r.startswith("Все проверки пройдены")),
                            canonical["reason"],
                        ],
                        "details": {
                            **policy_result.details,
                            "canonicalCoverage": canonical["details"],
                        },
                    })
        if findings_bundle is not None and policy_result is not None:
            # One final rewrite keeps executionStatus, official metadata and
            # readable text aligned with the actual terminal status. Security
            # verdict/passport stay the policy verdict, not the execution status.
            self._rewrite_report_verdict(
                run_id, workflow_name, findings_bundle, policy_result,
                explain_result, final_exec_status.value,
            )

        finished_iso = datetime.now(timezone.utc).isoformat()
        # Publish before exposing final status: once the UI sees "succeeded/failed",
        # the published canonical record (or an honest publication=false) already exists.
        try:
            await asyncio.to_thread(
                self._publish_canonical, run_id, workflow_id, revision_id,
                final_exec_status.value, final_verdict.value,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Canonical publish failed for %s: %s", run_id, exc)

        self.db.update_run_status(
            run_id,
            execution_status=final_exec_status.value,
            security_verdict=final_verdict.value,
            finished_at=finished_iso,
            findings_summary=counts
        )

        await self.sse.emit_event(run_id, "run_status", {
            "status": final_exec_status.value,
            "verdict": final_verdict.value,
            "findings_summary": counts
        })

    async def _run_node_logic(self, run_id: str, node: WorkflowNode, inputs: Dict[str, Any],
                             default_artifact_id: Optional[str], trigger_type: str,
                             current_bundle: Optional[FindingsBundle],
                             current_policy: Optional[PolicyResult],
                             current_explain: Optional[ExplainResult],
                             workflow_id: str, revision_id: str,
                             workflow_name: str) -> Tuple[Dict[str, Any], Optional[bool]]:
        kind = node.kind
        config = node.config or {}

        if kind in (NodeKind.MANUAL_TRIGGER, NodeKind.SCHEDULE_TRIGGER):
            return {
                "out": {
                    "runId": run_id,
                    "triggerType": trigger_type,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
            }, None

        elif kind == NodeKind.PROJECT_INPUT:
            # The run-selected artifact is authoritative for every node. The
            # draft artifactId is only a fallback when no run artifact is set.
            art_id = default_artifact_id or config.get("artifactId")
            if not art_id:
                raise ValueError("Project Input: ID артефакта проекта не указан")
            artifact = self.db.get_artifact(art_id)
            if not artifact:
                raise ValueError(f"Project Input: Артефакт '{art_id}' не найден в хранилище")
            # Наружу уходит только публичный id/имя. extracted_path остаётся на сервере.
            return {
                "artifact": {
                    "artifactId": art_id,
                    "filename": artifact["filename"],
                }
            }, None

        elif kind in (NodeKind.SECRET_SCAN, NodeKind.SCA_SCAN, NodeKind.KOTLIN_SAST):
            art = inputs.get("artifact")
            if not art or not art.get("artifactId"):
                raise ValueError("Сканер: отсутствует входной артефакт проекта")
            artifact_path = self._resolve_artifact_path(art["artifactId"])
            category = {
                NodeKind.SECRET_SCAN: "SECRET",
                NodeKind.SCA_SCAN: "SCA",
                NodeKind.KOTLIN_SAST: "SAST",
            }[kind]
            artifact_row = self.db.get_artifact(art["artifactId"]) or {}
            # Fullest supported analysis: both engines run for the selected
            # scanner category. A failing engine becomes a diagnostic; it does
            # not erase the other engine's evidence.
            scan_res = await scan_category_full(
                category=category,
                artifact_path=artifact_path,
                artifact_row=artifact_row,
                run_id=run_id,
                revision_id=revision_id,
                adapter=self.team_b,
            )
            return {"scan_result": scan_res}, None

        elif kind == NodeKind.MERGE_FINDINGS:
            # Collect all scan results from inputs
            scan_results: List[ScanResult] = []
            for k, val in inputs.items():
                if isinstance(val, ScanResult):
                    scan_results.append(val)
                elif isinstance(val, dict) and "findings" in val:
                    scan_results.append(ScanResult(**val))

            if not scan_results:
                raise ValueError("Merge Findings: Не получено ни одного результата сканирования")

            all_findings: List[Finding] = []
            all_diagnostics: List[Diagnostic] = []
            seen_fingerprints: Set[str] = set()

            total_files = 0
            total_skipped = 0
            total_loc = 0
            all_rules: Set[str] = set()
            total_parser_errors = 0
            total_unresolved = 0
            durations: Dict[str, float] = {}

            for sr in scan_results:
                durations[sr.analyzer] = sr.duration_ms
                total_files = max(total_files, sr.coverage.files_scanned)
                total_skipped += sr.coverage.files_skipped
                total_loc = max(total_loc, sr.coverage.lines_of_code)
                all_rules.update(sr.coverage.rules_executed)
                total_parser_errors += sr.coverage.parser_errors
                total_unresolved += sr.coverage.unresolved_dependencies
                all_diagnostics.extend(sr.diagnostics)
                if sr.coverage.files_scanned == 0:
                    all_diagnostics.append(Diagnostic(
                        level="warning",
                        message=f"Анализатор {sr.analyzer} не просканировал ни одного файла",
                        source=f"{sr.analyzer}/coverage",
                        details={"coverage_gap": True, "analyzer": sr.analyzer},
                    ))

                for f in sr.findings:
                    if f.fingerprint not in seen_fingerprints:
                        seen_fingerprints.add(f.fingerprint)
                        all_findings.append(f)

            run_row = self.db.get_run(run_id) or {}
            artifact_id = default_artifact_id or run_row.get("artifact_id")
            codefacts_facts = None
            map_error = None
            if artifact_id:
                try:
                    all_findings, codefacts_facts, map_error = await asyncio.to_thread(
                        self._integrate_codefacts, run_id, artifact_id, all_findings, scan_results,
                    )
                except Exception as exc:  # noqa: BLE001 - карта не должна скрывать находки
                    logger.warning("Code facts integration failed for %s: %s", run_id, exc)
                    map_error = str(exc)
            if codefacts_facts is not None:
                for diag in codefacts_facts.diagnostics:
                    code = str(diag.get("code", "CF-DIAG"))
                    level = "error" if code in {"CF-DECODE-ERROR", "CF-EDGE-WITHOUT-EVIDENCE"} else "warning"
                    all_diagnostics.append(Diagnostic(
                        level=level, message=str(diag.get("message", code)),
                        source="codefacts", details={"code": code, "path": diag.get("path")},
                    ))
                all_rules.update(rule["id"] for rule in RULE_DEFINITIONS)
                total_parser_errors += sum(
                    1 for diag in codefacts_facts.diagnostics if diag.get("code") == "CF-DECODE-ERROR"
                )
            if map_error:
                all_diagnostics.append(Diagnostic(
                    level="error", message=f"Не удалось построить security map: {map_error}",
                    source="codefacts", details={"code": "CF-MAP-UNAVAILABLE"},
                ))

            agg_coverage = Coverage(
                files_scanned=total_files,
                files_skipped=total_skipped,
                lines_of_code=total_loc,
                rules_executed=sorted(all_rules),
                parser_errors=total_parser_errors,
                unresolved_dependencies=total_unresolved
            )

            bundle = FindingsBundle(
                findings=all_findings,
                diagnostics=all_diagnostics,
                coverage=agg_coverage,
                scan_durations=durations
            )

            # Persist findings to database (including codefacts findings).
            findings_dicts = [f.model_dump() for f in all_findings]
            self.db.save_findings(run_id, findings_dicts)

            # Emit findings summary event
            counts = {
                "total": len(all_findings),
                "critical": sum(1 for f in all_findings if f.severity == Severity.CRITICAL),
                "high": sum(1 for f in all_findings if f.severity == Severity.HIGH),
                "medium": sum(1 for f in all_findings if f.severity == Severity.MEDIUM),
                "low": sum(1 for f in all_findings if f.severity == Severity.LOW),
                "info": sum(1 for f in all_findings if f.severity == Severity.INFO)
            }
            await self.sse.emit_event(run_id, "finding_summary", counts)
            await self.sse.emit_event(run_id, "coverage", agg_coverage.model_dump())

            return {"findings_bundle": bundle}, None

        elif kind == NodeKind.POLICY_GATE:
            bundle = inputs.get("findings_bundle") or current_bundle
            if not bundle:
                raise ValueError("Policy Gate: Не найден Findings Bundle")

            fail_on_critical = config.get("failOnCritical", True)
            fail_on_high = config.get("failOnHigh", True)
            max_medium = config.get("maxAllowedMedium", 2)
            strict_cov = config.get("strictCoverageRequired", True)

            crit_count = sum(1 for f in bundle.findings if f.severity == Severity.CRITICAL)
            high_count = sum(1 for f in bundle.findings if f.severity == Severity.HIGH)
            med_count = sum(1 for f in bundle.findings if f.severity == Severity.MEDIUM)
            low_count = sum(1 for f in bundle.findings if f.severity == Severity.LOW)
            info_count = sum(1 for f in bundle.findings if f.severity == Severity.INFO)

            reasons = []
            verdict = SecurityVerdict.PASS

            if fail_on_critical and crit_count > 0:
                verdict = SecurityVerdict.FAIL
                reasons.append(f"Обнаружено {crit_count} критических уязвимостей (порог: 0)")
            if fail_on_high and high_count > 0:
                verdict = SecurityVerdict.FAIL
                reasons.append(f"Обнаружено {high_count} уязвимостей высокого риска (порог: 0)")
            if med_count > max_medium:
                verdict = SecurityVerdict.FAIL
                reasons.append(f"Превышен лимит среднего риска: {med_count} > {max_medium}")

            coverage_gaps: List[str] = []
            if bundle.coverage.files_scanned == 0:
                coverage_gaps.append("ни один файл не просканирован")
            if not bundle.coverage.rules_executed:
                coverage_gaps.append("ни одно правило не выполнено")
            missing_scanners = {"secret", "sca", "sast"} - set(bundle.scan_durations)
            if missing_scanners:
                coverage_gaps.append(f"не отработали сканеры: {', '.join(sorted(missing_scanners))}")
            if bundle.coverage.parser_errors > 0:
                coverage_gaps.append(f"{bundle.coverage.parser_errors} ошибок парсера")
            if bundle.coverage.unresolved_dependencies > 0:
                coverage_gaps.append(f"{bundle.coverage.unresolved_dependencies} неразрешенных зависимостей")
            failed_diags = [d for d in bundle.diagnostics if d.level == "error"]
            if failed_diags:
                coverage_gaps.append(f"{len(failed_diags)} ошибок анализа/карты")
            gap_diags = [d for d in bundle.diagnostics if d.details.get("coverage_gap")]
            if gap_diags:
                coverage_gaps.append(f"{len(gap_diags)} анализаторов без покрытия")

            canonical = self._canonical_coverage_facts(run_id)
            coverage_problems: List[str] = []
            if not canonical["verified"]:
                coverage_problems.append(canonical["reason"])
            if strict_cov and coverage_gaps:
                coverage_problems.append("Неполное покрытие: " + "; ".join(coverage_gaps))
            if verdict != SecurityVerdict.FAIL:
                # strictCoverageRequired controls runtime requirements only;
                # canonical evidence is required for every non-fail verdict.
                if coverage_problems:
                    verdict = SecurityVerdict.INCONCLUSIVE
                for problem in coverage_problems:
                    if problem not in reasons:
                        reasons.append(problem)
                if not reasons:
                    reasons.append("Все проверки пройдены: критических находок нет")
            else:
                # A fail stays a fail, but the report must not look exhaustive
                # when an engine or catalog was unavailable.
                for problem in coverage_problems:
                    if problem not in reasons:
                        reasons.append(problem)

            pol_res = PolicyResult(
                verdict=verdict,
                reasons=reasons,
                details={"canonicalCoverage": canonical["details"]},
                critical_count=crit_count,
                high_count=high_count,
                medium_count=med_count,
                low_count=low_count,
                info_count=info_count,
                findings_count=len(bundle.findings),
                coverage_warnings_count=len(bundle.coverage.rules_executed)
            )
            return {"policy_result": pol_res}, None

        elif kind == NodeKind.CONDITION:
            # Condition: If/Else evaluation
            field = config.get("field", "verdict")
            operator = config.get("operator", "equals")
            target_value = str(config.get("targetValue", "fail"))

            in_val = inputs.get("in")
            # Extract actual value from policy_result or dict
            actual_val = None
            if isinstance(in_val, PolicyResult):
                if field == "verdict":
                    actual_val = in_val.verdict.value
                elif hasattr(in_val, field):
                    actual_val = getattr(in_val, field)
            elif isinstance(in_val, dict):
                actual_val = in_val.get(field)
            elif in_val is not None:
                actual_val = str(in_val)

            if actual_val is None:
                raise ConditionEvaluationError(
                    f"Ошибка условия: поле '{field}' отсутствует во входящих данных ноды Condition"
                )

            # Evaluate operator
            decision = False
            try:
                if operator == "equals":
                    decision = (str(actual_val).lower() == target_value.lower())
                elif operator == "not_equals":
                    decision = (str(actual_val).lower() != target_value.lower())
                elif operator == "greater_than":
                    decision = float(actual_val) > float(target_value)
                elif operator == "less_than":
                    decision = float(actual_val) < float(target_value)
                elif operator == "regex":
                    pattern = re.compile(target_value)
                    decision = bool(pattern.search(str(actual_val)))
                else:
                    raise ConditionEvaluationError(f"Неизвестный оператор условия: '{operator}'")
            except Exception as e:
                raise ConditionEvaluationError(f"Ошибка вычисления условия ({field} {operator} {target_value}): {str(e)}")

            branch_name = "true_branch" if decision else "false_branch"
            return {
                branch_name: {"decision": decision, "field": field, "actualValue": actual_val, "targetValue": target_value}
            }, decision

        elif kind == NodeKind.BRANCH_MERGE:
            # Forwards whichever branch was active
            active_branch_data = inputs.get("branch_a") or inputs.get("branch_b") or {}
            return {"out": active_branch_data}, None

        elif kind == NodeKind.AI_SCAN:
            # Separate, opt-in hypothesis scan. It must never raise: deterministic
            # findings, policy and the report have to survive provider/source errors.
            bundle = current_bundle
            raw_bundle = inputs.get("findings_bundle")
            if isinstance(raw_bundle, FindingsBundle):
                bundle = raw_bundle
            elif isinstance(raw_bundle, dict) and "findings" in raw_bundle:
                bundle = FindingsBundle(**raw_bundle)
            findings = list(bundle.findings) if bundle else []

            raw_artifact = inputs.get("artifact")
            artifact_id = ""
            if isinstance(raw_artifact, dict):
                artifact_id = str(raw_artifact.get("artifactId") or "")
            artifact_id = artifact_id or str(default_artifact_id or "")
            if not artifact_id:
                result = AiScanResult(
                    status="degraded",
                    validationErrors=["AI_ARTIFACT_MISSING"],
                )
            else:
                try:
                    artifact_row = self.db.get_artifact(artifact_id)
                    artifact_path = self._resolve_artifact_path(artifact_id)
                    if not artifact_row:
                        raise ValueError("artifact row missing")
                    result = await asyncio.to_thread(
                        self.ai_scan_service.scan,
                        artifact_id=artifact_id,
                        artifact_row=artifact_row,
                        artifact_path=artifact_path,
                        deterministic_findings=findings,
                        config=config,
                        run_id=run_id,
                        # Per-execution workspace; do not cache at executor init.
                        workspace=workspace_id(),
                    )
                except Exception:  # noqa: BLE001 - AI block is advisory, never fatal
                    result = AiScanResult(
                        status="degraded",
                        validationErrors=["AI_SCAN_INTERNAL_ERROR"],
                    )

            await self.sse.emit_event(run_id, "ai_scan_result", {
                "status": result.status,
                "observations": len(result.observations),
                "suppressedDuplicates": result.suppressedDuplicates,
                "coverageStatus": result.coverage.status,
                "countsAsFindings": False,
            })
            return {"ai_scan_result": result}, None

        elif kind == NodeKind.AI_EXPLAIN:
            bundle = current_bundle
            raw_input = inputs.get("findings_bundle")
            if isinstance(raw_input, FindingsBundle):
                bundle = raw_input
            elif isinstance(raw_input, dict) and "findings" in raw_input:
                bundle = FindingsBundle(**raw_input)

            if not bundle:
                raise ValueError("AI Explain: Отсутствуют находки для объяснения")

            # Call AI/fallback in a worker thread: провайдер может быть сетевым.
            exp_res = await asyncio.to_thread(self.ai_service.explain_findings, bundle.findings)
            return {"explain_result": exp_res}, None

        elif kind == NodeKind.BUILD_REPORT:
            bundle = current_bundle
            raw_bundle = inputs.get("findings_bundle")
            if isinstance(raw_bundle, FindingsBundle):
                bundle = raw_bundle
            elif isinstance(raw_bundle, dict) and "findings" in raw_bundle:
                bundle = FindingsBundle(**raw_bundle)

            pol = current_policy
            raw_pol = inputs.get("policy_result")
            if isinstance(raw_pol, PolicyResult):
                pol = raw_pol
            elif isinstance(raw_pol, dict) and "verdict" in raw_pol:
                pol = PolicyResult(**raw_pol)

            exp = current_explain
            raw_exp = inputs.get("explain_result")
            if isinstance(raw_exp, ExplainResult):
                exp = raw_exp
            elif isinstance(raw_exp, dict) and "items" in raw_exp:
                exp = ExplainResult(**raw_exp)

            if not bundle or not pol:
                raise ValueError("Build Report: Для генерации отчета необходимы FindingsBundle и PolicyResult")

            ai_scan = inputs.get("ai_scan_result")
            if ai_scan is None:
                ai_scan = self._latest_ai_scan_result(run_id)

            report_status = str((self.db.get_run(run_id) or {}).get("execution_status") or "running")
            canonical_coverage = (pol.details or {}).get("canonicalCoverage")
            passport = self.report_builder.build_security_passport(
                bundle.coverage, pol, bundle.findings, canonical_coverage=canonical_coverage,
            )
            readable_md = self.report_builder.build_readable_markdown(
                run_id, workflow_name, passport, pol, bundle.findings, exp,
                execution_status=report_status, ai_scan=ai_scan,
            )
            official_json = self.report_builder.build_official_json(
                run_id, workflow_id, revision_id, passport, pol, bundle.findings, exp,
                execution_status=report_status, ai_scan=ai_scan,
            )

            # Save report to DB
            self.db.save_report(
                run_id=run_id,
                verdict=pol.verdict.value,
                passport=passport.model_dump(),
                ai_summary=exp.summary if exp else "",
                ai_items=[i.model_dump() for i in exp.items] if exp else [],
                ai_status=exp.status if exp else "disabled",
                readable_markdown=readable_md,
                official_json=official_json
            )

            report_ref = {
                "runId": run_id,
                "verdict": pol.verdict.value,
                "findingsCount": len(bundle.findings),
                "generatedAt": datetime.now(timezone.utc).isoformat()
            }
            return {"report": report_ref, "passport": passport}, None

        elif kind == NodeKind.NOTIFY:
            pol = current_policy
            webhook_url = str(config.get("webhookUrl") or "")
            send_on_fail_only = bool(config.get("sendOnFailOnly", False))
            secret_token = config.get("secretToken")
            # Никакой автоматической отправки: только явный enabled=true + разрешённый URL.
            enabled = config.get("enabled") is True
            allow_local = config.get("allowLocal") is True
            allow_external = config.get("allowExternal") is True
            verdict = pol.verdict if pol else SecurityVerdict.INCONCLUSIVE

            if not enabled:
                del_res = DeliveryResult(
                    channel="webhook",
                    status="skipped",
                    target="",
                    message=(
                        "Уведомление отключено: нужен явный config.enabled=true. "
                        "Sample/дефолтный сценарий не отправляет наружу автоматически."
                    ),
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
            elif not webhook_url:
                del_res = DeliveryResult(
                    channel="webhook",
                    status="failed",
                    target="",
                    message="config.enabled=true, но webhookUrl не задан",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
            else:
                decision = await asyncio.to_thread(
                    validate_webhook_url,
                    webhook_url,
                    allow_local=allow_local,
                    allow_external=allow_external,
                )
                if not decision.allowed:
                    del_res = DeliveryResult(
                        channel="webhook",
                        status="failed",
                        target="",
                        message=f"Отправка запрещена политикой: {decision.reason}",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                    )
                elif send_on_fail_only and verdict != SecurityVerdict.FAIL:
                    del_res = DeliveryResult(
                        channel="webhook",
                        status="skipped",
                        target="",
                        message="Отправка пропущена: настроена отправка только при SecurityVerdict=FAIL",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                    )
                else:
                    summary_data = {
                        "verdict": verdict.value,
                        "critical": pol.critical_count if pol else 0,
                        "high": pol.high_count if pol else 0,
                        "findingsTotal": pol.findings_count if pol else 0,
                    }
                    del_res = await asyncio.to_thread(
                        self.notifier.send,
                        webhook_url,
                        run_id,
                        verdict,
                        summary_data,
                        secret_token,
                    )

            # URL query/fragment могут содержать токены; в UI/SSE их не отдаём.
            if del_res.target:
                del_res.target = del_res.target.split("?", 1)[0].split("#", 1)[0]

            # Save delivery to DB and emit SSE
            self.db.save_delivery(
                delivery_id=f"del_{run_id}",
                run_id=run_id,
                channel=del_res.channel,
                status=del_res.status,
                target=del_res.target,
                status_code=del_res.status_code,
                message=del_res.message,
            )
            await self.sse.emit_event(run_id, "delivery_status", del_res.model_dump())

            return {"delivery_result": del_res}, None

        else:
            raise ValueError(f"Неизвестный тип ноды для исполнения: {kind}")

    def _canonical_coverage_facts(self, run_id: str) -> Dict[str, Any]:
        """Canonical evidence for a non-fail verdict; never manufactures completeness."""
        machine: Dict[str, Any] = {
            "coverageStatus": "unavailable",
            "expectedScopesVerified": False,
            "skippedScopes": [],
            "bundleHash": None,
            "parserHash": None,
            "rulesHash": None,
            "artifactHash": None,
        }
        try:
            payload = CodeFactsStore().load_run(workspace_id(), run_uuid(run_id))
        except Exception as exc:  # noqa: BLE001 - missing canonical facts are not a pass
            logger.warning("Canonical coverage read failed for %s: %s", run_id, exc)
            return {"verified": False, "details": machine,
                    "reason": "Полная проверка не подтверждена: данные покрытия недоступны"}
        if not payload:
            return {"verified": False, "details": machine,
                    "reason": "Полная проверка не подтверждена: данные покрытия отсутствуют"}
        analysis = payload.get("analysisBundle") or {}
        coverage = ((analysis.get("codeFacts") or {}).get("coverage") or {})
        provenance = analysis.get("provenance") or {}
        signature = analysis.get("signature") or {}
        status = coverage.get("status")
        if status not in {"complete", "partial", "unavailable"}:
            status = "unavailable"
        expected = provenance.get("expectedScopesVerified") is True
        machine.update({
            "coverageStatus": status,
            "expectedScopesVerified": expected,
            "skippedScopes": list(coverage.get("skippedScopes") or []),
            "bundleHash": (payload.get("map") or {}).get("bundleHash"),
            "parserHash": signature.get("parserHash"),
            "rulesHash": signature.get("rulesHash"),
            "artifactHash": (payload.get("identity") or {}).get("artifactHash"),
        })
        if status == "complete" and expected:
            return {"verified": True, "details": machine, "reason": ""}
        bits = ["покрытие частичное" if status == "partial" else "покрытие недоступно"]
        if not expected:
            bits.append("обязательные scopes не верифицированы")
        if machine["skippedScopes"]:
            bits.append("пропущены: " + ", ".join(machine["skippedScopes"]))
        return {"verified": False, "details": machine,
                "reason": "Полная проверка не подтверждена: " + "; ".join(bits)}

    def _rewrite_report_verdict(self, run_id: str, workflow_name: str, bundle: FindingsBundle,
                                policy: Optional[PolicyResult],
                                explain: Optional[ExplainResult],
                                execution_status: str) -> None:
        """Keep the stored report consistent with the final security verdict."""
        if policy is None or not self.db.get_report(run_id):
            return
        run_row = self.db.get_run(run_id) or {}
        canonical_coverage = (policy.details or {}).get("canonicalCoverage")
        passport = self.report_builder.build_security_passport(
            bundle.coverage, policy, bundle.findings, canonical_coverage=canonical_coverage,
        )
        ai_scan = self._latest_ai_scan_result(run_id)
        readable = self.report_builder.build_readable_markdown(
            run_id, workflow_name, passport, policy, bundle.findings, explain,
            execution_status=execution_status,
            ai_scan=ai_scan,
        )
        official = self.report_builder.build_official_json(
            run_id,
            str(run_row.get("workflow_id") or "workflow-unknown"),
            str(run_row.get("revision_id") or "revision-unknown"),
            passport, policy, bundle.findings, explain,
            execution_status=execution_status,
            ai_scan=ai_scan,
        )
        self.db.save_report(
            run_id=run_id,
            verdict=policy.verdict.value,
            passport=passport.model_dump(),
            ai_summary=explain.summary if explain else "",
            ai_items=[item.model_dump() for item in explain.items] if explain else [],
            ai_status=explain.status if explain else "disabled",
            readable_markdown=readable,
            official_json=official,
        )

    def _publish_canonical(self, run_id: str, workflow_id: str, revision_id: str,
                          final_status: Optional[str] = None,
                          final_verdict: Optional[str] = None) -> None:
        workspace = workspace_id()
        store = CodeFactsStore()
        payload = store.load_run(workspace, run_uuid(run_id))
        if not payload:
            return
        run = self.db.get_run(run_id) or {}
        if final_status:
            run = {**run, "execution_status": final_status}
        if final_verdict:
            run = {**run, "security_verdict": final_verdict}
        artifact_id = str(run.get("artifact_id") or "")
        artifact = self.db.get_artifact(artifact_id) if artifact_id else None
        workflow = self.db.get_workflow(workflow_id) or {}
        if not artifact:
            payload["publication"] = {"published": False, "reason": "artifact row missing"}
            store.save_run(workspace, run_uuid(run_id), payload)
            return
        meta = store.load_artifact_meta(workspace, artifact_id) or {}
        if meta.get("expiresAt") and not artifact.get("expires_at"):
            artifact = {**artifact, "expires_at": meta["expiresAt"]}
        snapshot = store.load_snapshot(workspace, str(payload["identity"]["snapshotId"]))
        if not snapshot:
            payload["publication"] = {"published": False, "reason": "snapshot missing"}
            store.save_run(workspace, run_uuid(run_id), payload)
            return
        result = publish_run_artifact(
            workspace=workspace,
            run_artifact=payload,
            snapshot=snapshot,
            artifact=artifact,
            workflow=workflow,
            run=run,
            revision_id=revision_id,
        )
        payload["publication"] = result
        store.save_run(workspace, run_uuid(run_id), payload)
        if not result.get("published"):
            logger.info("Immutable bundle not published for %s: %s", run_id, result.get("reason"))

    def _resolve_artifact_path(self, artifact_id: str) -> str:
        artifact = self.db.get_artifact(artifact_id)
        if not artifact:
            raise ValueError(f"Артефакт '{artifact_id}' не найден в хранилище")
        path = artifact.get("extracted_path")
        if not path or not Path(path).is_dir():
            raise ValueError("Распакованный артефакт недоступен")
        return str(path)

    def _integrate_codefacts(self, run_id: str, artifact_id: str,
                             findings: List[Finding],
                             scan_results: Optional[List[ScanResult]] = None) -> Tuple[List[Finding], Any, Optional[str]]:
        """Строит CF-находки и immutable run-карту. Вызывается в отдельном потоке."""
        run_row = self.db.get_run(run_id) or {}
        artifact = self.db.get_artifact(artifact_id)
        if not artifact:
            return findings, None, "artifact missing"
        artifact_path = artifact.get("extracted_path")
        if not artifact_path or not Path(artifact_path).is_dir():
            return findings, None, "artifact path unavailable"
        workspace = workspace_id()
        store = CodeFactsStore()
        facts = None
        merged = list(findings)
        error: Optional[str] = None
        try:
            meta = ensure_artifact_meta(store, workspace, artifact)
            snapshot = store.load_snapshot(workspace, str(meta.get("snapshotId")))
            if not snapshot:
                return findings, None, "snapshot missing"
            sources, source_diags = load_snapshot_sources(Path(artifact_path), snapshot)
            facts = extract_facts(sources, len(snapshot.get("files", [])))
            for diag in source_diags:
                facts.diagnostics.append(diag)
            merged = merge_extra_findings(merged, facts.findings, "SAST")
            secret_files = secret_ranges(merged)
            sanitize_fact_evidence(facts, secret_files)
            map_sources = sanitize_sources(sources, secret_files)
            build_run_map(
                workspace=workspace,
                internal_run_id=run_id,
                run_id=run_uuid(run_id),
                workflow_revision_id=str(run_row.get("revision_id") or "unknown"),
                artifact_row=artifact,
                backend_findings=merged,
                store=store,
                meta=meta,
                snapshot=snapshot,
                sources=map_sources,
                facts=facts,
                scan_results=scan_results,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Security map build failed for %s: %s", run_id, exc)
            error = str(exc)
        return merged, facts, error

    def _record_node_run(self, run_id: str, node: WorkflowNode, status: NodeRunStatus,
                         duration_ms: float, error_msg: Optional[str],
                         in_data: Dict[str, Any], out_data: Dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.db.save_node_run({
            "id": f"nr_{run_id}_{node.id}",
            "run_id": run_id,
            "node_id": node.id,
            "kind": node.kind.value,
            "status": status.value,
            "started_at": now,
            "finished_at": now,
            "duration_ms": round(duration_ms, 2),
            "error_message": error_msg,
            "input_data_masked": in_data,
            "output_data_masked": out_data
        })

    def _latest_ai_scan_result(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Read the advisory AI_SCAN output already stored for this run."""
        try:
            node_runs = self.db.get_node_runs(run_id)
        except Exception:  # noqa: BLE001 - report must survive a storage hiccup
            return None
        for node_run in reversed(node_runs):
            if node_run.get("kind") != NodeKind.AI_SCAN.value:
                continue
            output = node_run.get("output_data_masked") or {}
            value = output.get("ai_scan_result") if isinstance(output, dict) else None
            if isinstance(value, dict):
                return value
        return None

    def _mask_data_for_ui(self, obj: Any) -> Any:
        """
        Recursively converts Pydantic models or dicts to json-compatible dicts,
        ensuring secrets and raw keys are masked.
        """
        if hasattr(obj, "model_dump"):
            obj = obj.model_dump()

        if isinstance(obj, dict):
            masked = {}
            for k, v in obj.items():
                if "secret" in k.lower() or "token" in k.lower() or "password" in k.lower():
                    if isinstance(v, str) and len(v) > 4:
                        masked[k] = v[:2] + "****" + v[-2:]
                    else:
                        masked[k] = "****"
                else:
                    masked[k] = self._mask_data_for_ui(v)
            return masked
        elif isinstance(obj, list):
            return [self._mask_data_for_ui(item) for item in obj]
        elif isinstance(obj, (int, float, bool, str)) or obj is None:
            return obj
        else:
            return str(obj)
