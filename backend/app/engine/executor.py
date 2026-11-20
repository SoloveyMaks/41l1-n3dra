import asyncio
import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.app.ai.service import AiService
from backend.app.analyzers.sast_analyzer import SastAnalyzer
from backend.app.analyzers.sca_analyzer import ScaAnalyzer
from backend.app.analyzers.secret_analyzer import SecretAnalyzer
from backend.app.contracts.node_registry import NODE_REGISTRY
from backend.app.contracts.schemas import (
    Coverage, DeliveryResult, Diagnostic, ExecutionStatus, ExplainResult,
    Finding, FindingsBundle, NodeKind, NodeRunStatus, PolicyResult,
    ScanResult, SecurityPassport, SecurityVerdict, Severity, WorkflowDraft, WorkflowNode
)
from backend.app.engine.sse import SSEBroker
from backend.app.engine.validator import WorkflowValidator
from backend.app.notifications.webhook import WebhookNotifier
from backend.app.reports.builder import ReportBuilder
from backend.app.storage.database import DatabaseRepository


class ConditionEvaluationError(Exception):
    pass


class WorkflowExecutor:
    def __init__(self, db: DatabaseRepository, sse: SSEBroker):
        self.db = db
        self.sse = sse
        self.validator = WorkflowValidator()
        self.ai_service = AiService()
        self.report_builder = ReportBuilder()
        self.notifier = WebhookNotifier()

    async def execute_run(self, run_id: str, draft: WorkflowDraft, artifact_id: Optional[str],
                          trigger_type: str = "manual") -> None:
        start_time_iso = datetime.now(timezone.utc).isoformat()
        self.db.update_run_status(run_id, execution_status="running", started_at=start_time_iso)
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
        security_passport: Optional[SecurityPassport] = None

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
                    findings_bundle, policy_result, explain_result
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
                if "passport" in outputs:
                    security_passport = outputs["passport"]

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

                # If this was condition, condition error must fail the run visibly
                if curr_node.kind == NodeKind.CONDITION:
                    self.db.update_run_status(
                        run_id, execution_status="failed", security_verdict="inconclusive",
                        finished_at=datetime.now(timezone.utc).isoformat()
                    )
                    await self.sse.emit_event(run_id, "run_status", {
                        "status": "failed", "verdict": "inconclusive", "error": f"Ошибка условия: {err_msg}"
                    })
                    return

                # For analyzer failure: mark downstream edges as failed/inactive, but let others continue
                for out_e in outgoing_edges[ready_node_id]:
                    edge_active_states[out_e.id] = False

        # Finalize run status and verdict
        final_verdict = policy_result.verdict if policy_result else SecurityVerdict.INCONCLUSIVE
        has_failed_nodes = any(s == NodeRunStatus.FAILED for s in node_statuses.values())
        final_exec_status = ExecutionStatus.FAILED if has_failed_nodes and not findings_bundle else ExecutionStatus.SUCCEEDED

        counts = {
            "total": len(findings_bundle.findings) if findings_bundle else 0,
            "critical": policy_result.critical_count if policy_result else 0,
            "high": policy_result.high_count if policy_result else 0,
            "medium": policy_result.medium_count if policy_result else 0,
            "low": policy_result.low_count if policy_result else 0,
            "info": policy_result.info_count if policy_result else 0
        }

        finished_iso = datetime.now(timezone.utc).isoformat()
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
                             current_explain: Optional[ExplainResult]) -> Tuple[Dict[str, Any], Optional[bool]]:
        kind = node.kind
        config = node.config or {}
        branch_decision = None

        if kind in (NodeKind.MANUAL_TRIGGER, NodeKind.SCHEDULE_TRIGGER):
            return {
                "out": {
                    "runId": run_id,
                    "triggerType": trigger_type,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
            }, None

        elif kind == NodeKind.PROJECT_INPUT:
            art_id = config.get("artifactId") or default_artifact_id
            if not art_id:
                raise ValueError("Project Input: ID артефакта проекта не указан")
            artifact = self.db.get_artifact(art_id)
            if not artifact:
                raise ValueError(f"Project Input: Артефакт '{art_id}' не найден в хранилище")
            return {
                "artifact": {
                    "artifactId": art_id,
                    "extractedPath": artifact["extracted_path"],
                    "filename": artifact["filename"]
                }
            }, None

        elif kind == NodeKind.SECRET_SCAN:
            art = inputs.get("artifact")
            if not art or "extractedPath" not in art:
                raise ValueError("Secret Scan: Отсутствует входной артефакт проекта")
            try:
                scan_res = SecretAnalyzer().analyze(art["extractedPath"])
            except Exception as e:
                scan_res = ScanResult(
                    analyzer="secret",
                    findings=[],
                    diagnostics=[Diagnostic(level="error", message=f"Сбой анализатора секретов: {str(e)}", source="SecretAnalyzer")],
                    duration_ms=0.0
                )
            return {"scan_result": scan_res}, None

        elif kind == NodeKind.SCA_SCAN:
            art = inputs.get("artifact")
            if not art or "extractedPath" not in art:
                raise ValueError("SCA Scan: Отсутствует входной артефакт проекта")
            try:
                scan_res = ScaAnalyzer().analyze(art["extractedPath"])
            except Exception as e:
                scan_res = ScanResult(
                    analyzer="sca",
                    findings=[],
                    diagnostics=[Diagnostic(level="error", message=f"Сбой анализатора зависимостей: {str(e)}", source="ScaAnalyzer")],
                    duration_ms=0.0
                )
            return {"scan_result": scan_res}, None

        elif kind == NodeKind.KOTLIN_SAST:
            art = inputs.get("artifact")
            if not art or "extractedPath" not in art:
                raise ValueError("Kotlin SAST: Отсутствует входной артефакт проекта")
            try:
                scan_res = SastAnalyzer().analyze(art["extractedPath"])
            except Exception as e:
                scan_res = ScanResult(
                    analyzer="sast",
                    findings=[],
                    diagnostics=[Diagnostic(level="error", message=f"Сбой статического анализатора SAST: {str(e)}", source="SastAnalyzer")],
                    duration_ms=0.0
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

                for f in sr.findings:
                    if f.fingerprint not in seen_fingerprints:
                        seen_fingerprints.add(f.fingerprint)
                        all_findings.append(f)

            agg_coverage = Coverage(
                files_scanned=total_files,
                files_skipped=total_skipped,
                lines_of_code=total_loc,
                rules_executed=list(all_rules),
                parser_errors=total_parser_errors,
                unresolved_dependencies=total_unresolved
            )

            bundle = FindingsBundle(
                findings=all_findings,
                diagnostics=all_diagnostics,
                coverage=agg_coverage,
                scan_durations=durations
            )

            # Persist findings to database
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

            if verdict != SecurityVerdict.FAIL:
                if strict_cov and (bundle.coverage.parser_errors > 0 or bundle.coverage.unresolved_dependencies > 0):
                    verdict = SecurityVerdict.INCONCLUSIVE
                    reasons.append(f"Неполное покрытие: {bundle.coverage.parser_errors} ошибок парсера, {bundle.coverage.unresolved_dependencies} неразрешенных зависимостей")
                elif not reasons:
                    reasons.append("Все проверки пройдены: критических находок нет")

            pol_res = PolicyResult(
                verdict=verdict,
                reasons=reasons,
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

        elif kind == NodeKind.AI_EXPLAIN:
            bundle = current_bundle
            raw_input = inputs.get("findings_bundle")
            if isinstance(raw_input, FindingsBundle):
                bundle = raw_input
            elif isinstance(raw_input, dict) and "findings" in raw_input:
                bundle = FindingsBundle(**raw_input)

            if not bundle:
                raise ValueError("AI Explain: Отсутствуют находки для объяснения")

            # Call AI Service with validation
            exp_res = self.ai_service.explain_findings(bundle.findings)
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

            passport = self.report_builder.build_security_passport(bundle.coverage, pol, bundle.findings)
            readable_md = self.report_builder.build_readable_markdown(
                run_id, "Security Scan", passport, pol, bundle.findings, exp
            )
            official_json = self.report_builder.build_official_json(
                run_id, "wf_main", "rev_1", passport, pol, bundle.findings, exp
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
            report_ref = inputs.get("report")
            webhook_url = config.get("webhookUrl", "http://127.0.0.1:8000/api/webhook-test")
            send_on_fail_only = config.get("sendOnFailOnly", False)
            secret_token = config.get("secretToken")

            verdict = pol.verdict if pol else SecurityVerdict.INCONCLUSIVE
            if send_on_fail_only and verdict != SecurityVerdict.FAIL:
                del_res = DeliveryResult(
                    channel="webhook",
                    status="succeeded",
                    target=webhook_url,
                    message="Отправка пропущена: настроена отправка только при SecurityVerdict=FAIL",
                    timestamp=datetime.now(timezone.utc).isoformat()
                )
            else:
                summary_data = {
                    "verdict": verdict.value,
                    "critical": pol.critical_count if pol else 0,
                    "high": pol.high_count if pol else 0,
                    "findingsTotal": pol.findings_count if pol else 0
                }
                del_res = self.notifier.send(
                    url=webhook_url,
                    run_id=run_id,
                    verdict=verdict,
                    passport_summary=summary_data,
                    secret_token=secret_token
                )

            # Save delivery to DB and emit SSE
            self.db.save_delivery(
                delivery_id=f"del_{run_id}",
                run_id=run_id,
                channel=del_res.channel,
                status=del_res.status,
                target=del_res.target,
                status_code=del_res.status_code,
                message=del_res.message
            )
            await self.sse.emit_event(run_id, "delivery_status", del_res.model_dump())

            return {"delivery_result": del_res}, None

        else:
            raise ValueError(f"Неизвестный тип ноды для исполнения: {kind}")

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
