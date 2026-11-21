import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.app.ai.provider import mask_sensitive_text
from backend.app.codefacts.source import mask_text
from backend.app.contracts.schemas import (
    Coverage, ExplainResult, Finding, PolicyResult, SecurityPassport, SecurityVerdict
)


def _safe_finding_data(finding: Finding) -> Dict[str, Any]:
    data = finding.model_dump()
    if isinstance(data.get("redactedEvidence"), str):
        data["redactedEvidence"] = mask_text(data["redactedEvidence"])
    for key in ("severityBasis", "confidenceBasis"):
        if isinstance(data.get(key), str):
            data[key] = mask_sensitive_text(data[key])
    for step in data.get("trace") or []:
        if isinstance(step, dict) and isinstance(step.get("description"), str):
            step["description"] = mask_sensitive_text(step["description"])
    return data


def _safe_ai_item(item: Any) -> Dict[str, Any]:
    data = item.model_dump() if hasattr(item, "model_dump") else dict(item)
    for key in ("explanation", "remediation"):
        if isinstance(data.get(key), str):
            data[key] = mask_sensitive_text(data[key])
    return data


class ReportBuilder:
    def build_security_passport(self, bundle_coverage: Coverage, policy_result: PolicyResult,
                                findings: List[Finding],
                                canonical_coverage: Optional[Dict[str, Any]] = None) -> SecurityPassport:
        confirmed = sum(1 for f in findings if f.status.value == "confirmed")
        potential = sum(1 for f in findings if f.status.value == "potential")
        inconclusive = sum(1 for f in findings if f.status.value == "inconclusive")

        # Legacy parser/AST ratio: kept in JSON, but never presented as the
        # overall coverage verdict.
        penalty = (bundle_coverage.parser_errors * 0.1) + (bundle_coverage.unresolved_dependencies * 0.05)
        parser_score = max(0.0, min(1.0, 1.0 - penalty))

        canonical = canonical_coverage or {}
        coverage_status = str(canonical.get("coverageStatus") or "unknown")
        expected_verified = canonical.get("expectedScopesVerified") is True
        skipped_scopes = [str(item) for item in (canonical.get("skippedScopes") or [])]
        completeness_known = coverage_status == "complete" and expected_verified
        coverage_reasons: List[str] = []
        if not completeness_known:
            if coverage_status == "partial":
                reason = "Полнота проверки не установлена: canonical coverage partial"
                if skipped_scopes:
                    reason += "; пропущены: " + ", ".join(skipped_scopes)
            elif coverage_status == "complete" and not expected_verified:
                reason = "Полнота проверки не установлена: expected scopes не верифицированы"
            else:
                reason = "Полнота проверки не установлена: canonical coverage отсутствует"
            coverage_reasons.append(reason)

        warnings = []
        if bundle_coverage.parser_errors > 0:
            warnings.append(f"Обнаружено {bundle_coverage.parser_errors} ошибок парсера при синтаксическом разборе AST")
        if bundle_coverage.unresolved_dependencies > 0:
            warnings.append(f"Обнаружено {bundle_coverage.unresolved_dependencies} неразрешённых зависимостей")
        if bundle_coverage.files_skipped > 0:
            warnings.append(f"Пропущено {bundle_coverage.files_skipped} бинарных или избыточных файлов")

        return SecurityPassport(
            files_scanned=bundle_coverage.files_scanned,
            files_skipped=bundle_coverage.files_skipped,
            lines_of_code=bundle_coverage.lines_of_code,
            rules_executed=bundle_coverage.rules_executed,
            parser_errors=bundle_coverage.parser_errors,
            unresolved_dependencies=bundle_coverage.unresolved_dependencies,
            confirmed_findings=confirmed,
            potential_findings=potential,
            inconclusive_findings=inconclusive,
            verdict=policy_result.verdict,
            completeness_score=1.0 if completeness_known else 0.0,
            completeness_known=completeness_known,
            coverage_status=coverage_status,
            coverage_reasons=coverage_reasons,
            skipped_scopes=skipped_scopes,
            parser_completeness_score=round(parser_score, 2),
            coverage_warnings=warnings
        )

    def build_readable_markdown(self, run_id: str, workflow_name: str,
                                passport: SecurityPassport, policy: PolicyResult,
                                findings: List[Finding], ai_result: Optional[ExplainResult],
                                execution_status: str = "running") -> str:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        verdict_badge = "❌ FAIL" if passport.verdict == SecurityVerdict.FAIL else ("✅ PASS" if passport.verdict == SecurityVerdict.PASS else "⚠️ INCONCLUSIVE")

        md = []
        md.append(f"# Отчет проверки безопасности кода: {workflow_name}")
        md.append(f"**Идентификатор запуска:** `{run_id}`  ")
        md.append(f"**Дата и время:** {now}  ")
        md.append(f"**Статус выполнения:** `{execution_status}`  ")
        md.append(f"**Итоговый вердикт:** {verdict_badge}  \n")

        md.append("## 1. Карта безопасности (Security Passport)")
        md.append("| Метрика | Значение |")
        md.append("| :--- | :--- |")
        md.append(f"| Проверено файлов | **{passport.files_scanned}** |")
        md.append(f"| Пропущено файлов | {passport.files_skipped} |")
        md.append(f"| Строк кода (LOC) | {passport.lines_of_code} |")
        md.append(f"| Запущено правил | {len(passport.rules_executed)} |")
        md.append(f"| Ошибок парсера | {passport.parser_errors} |")
        md.append(f"| Неразрешенных зависимостей | {passport.unresolved_dependencies} |")
        md.append(f"| Подтвержденных находок | **{passport.confirmed_findings}** |")
        md.append(f"| Потенциальных находок | {passport.potential_findings} |")
        if passport.completeness_known:
            md.append("| Полнота canonical coverage | **100%** |")
        else:
            md.append("| Полнота проверки | **неизвестна** |\n")

        if passport.coverage_reasons:
            md.append("### ⚠️ Полнота проверки")
            for reason in passport.coverage_reasons:
                md.append(f"- {reason}")
            md.append("")

        if passport.coverage_warnings:
            md.append("### ⚠️ Предупреждения по покрытию:")
            for w in passport.coverage_warnings:
                md.append(f"- {w}")
            md.append("")

        md.append("## 2. Сводка вердикта политики (Policy Gate)")
        md.append(f"- Критических находок: **{policy.critical_count}**")
        md.append(f"- Высокого риска: **{policy.high_count}**")
        md.append(f"- Среднего риска: {policy.medium_count}")
        md.append(f"- Низкого риска: {policy.low_count}")
        if policy.reasons:
            md.append("\n**Основания вердикта:**")
            for r in policy.reasons:
                md.append(f"- {r}")
        md.append("")

        if ai_result:
            md.append("## 3. Заключение AI-ассистента")
            status_text = "✅ Успешно проверено валидатором" if ai_result.status == "succeeded" else f"⚠️ Режим fallback ({ai_result.status})"
            md.append(f"**Статус интеграции:** {status_text} (модель: `{ai_result.model}`)\n")
            if ai_result.provenance:
                prov = ai_result.provenance
                fallback = f"; fallback: {prov.fallback_reason}" if prov.fallback_reason else ""
                md.append(
                    f"**Провайдер:** `{prov.provider}/{prov.model}`; endpoint: `{prov.endpoint or '—'}`; "
                    f"request: `{prov.request_id or '—'}`; {prov.latency_ms:.0f} ms{fallback}\n"
                )
            if ai_result.batch_provenance:
                batches = ", ".join(
                    f"{batch.batchIndex}:{batch.status} ({batch.itemCount} items)"
                    for batch in ai_result.batch_provenance
                )
                md.append(f"**AI batches:** {batches}\n")
            if ai_result.retrieval and ai_result.retrieval.status != "disabled":
                retrieval = ai_result.retrieval
                md.append(
                    f"**Exact retrieval:** `{retrieval.status}`"
                    + (f" — {retrieval.reason}" if retrieval.reason else "")
                    + "\n"
                )
            md.append(f"> {mask_sensitive_text(ai_result.summary)}\n")

        md.append(f"## 4. Обнаруженные дефекты ({len(findings)})")
        if not findings:
            md.append("Дефектов безопасности не обнаружено.")
        else:
            ai_item_map = {item.findingId: item for item in (ai_result.items if ai_result else [])}
            for idx, f in enumerate(findings, 1):
                sev_icon = "🔴" if f.severity.value == "critical" else ("🟠" if f.severity.value == "high" else "🟡")
                md.append(f"### {idx}. {sev_icon} [{f.severity.value.upper()}] {f.ruleId}")
                md.append(f"- **ID:** `{f.id}`")
                md.append(f"- **Анализатор:** `{f.analyzer}`")
                md.append(f"- **Файл:** `{f.primaryLocation.path}:{f.primaryLocation.startLine}`")
                md.append(f"- **Обоснование риска:** {mask_sensitive_text(f.severityBasis)}")
                md.append(f"- **Уверенность детектора:** {f.confidence.value} ({mask_sensitive_text(f.confidenceBasis)})")
                if f.advisoryIds:
                    md.append(f"- **CVE:** {', '.join(f.advisoryIds)}")

                if f.redactedEvidence:
                    md.append(f"```text\n{mask_text(f.redactedEvidence)}\n```")

                if f.trace:
                    md.append("**Evidence Trace (Цепочка вызовов):**")
                    for t in f.trace:
                        md.append(f"  {t.step}. {mask_sensitive_text(t.description)}")

                if f.id in ai_item_map:
                    item = ai_item_map[f.id]
                    md.append("\n**Объяснение AI:**")
                    md.append(mask_sensitive_text(item.explanation))
                    md.append("\n**Рекомендация по исправлению:**")
                    md.append(f"`{mask_sensitive_text(item.remediation)}`")
                    if item.suggestedFixedVersion:
                        md.append(f"\n*Рекомендуемая безопасная версия:* `{item.suggestedFixedVersion}`")
                md.append("\n---\n")

        return "\n".join(md)

    def build_official_json(self, run_id: str, workflow_id: str, revision_id: str,
                            passport: SecurityPassport, policy: PolicyResult,
                            findings: List[Finding], ai_result: Optional[ExplainResult],
                            execution_status: str = "running") -> str:
        ai_item_map = {
            item.findingId: _safe_ai_item(item)
            for item in (ai_result.items if ai_result else [])
        }

        findings_json = []
        for f in findings:
            item_data = _safe_finding_data(f)
            if f.id in ai_item_map:
                item_data["ai_enrichment"] = ai_item_map[f.id]
            findings_json.append(item_data)

        official_doc = {
            "schemaVersion": "1.0.0",
            "metadata": {
                "runId": run_id,
                "workflowId": workflow_id,
                "revisionId": revision_id,
                "generatedAt": datetime.now(timezone.utc).isoformat(),
                "verdict": passport.verdict.value,
                "executionStatus": execution_status
            },
            "securityPassport": passport.model_dump(),
            "policyResult": policy.model_dump(),
            "aiAnalysis": {
                "status": ai_result.status if ai_result else "disabled",
                "model": ai_result.model if ai_result else "none",
                "summary": mask_sensitive_text(ai_result.summary) if ai_result else "",
                "validationErrors": [
                    mask_sensitive_text(str(item))
                    for item in (ai_result.validation_errors if ai_result else [])
                ],
                "provenance": (
                    ai_result.provenance.model_dump() if ai_result and ai_result.provenance else None
                ),
                "batchProvenance": (
                    [batch.model_dump() for batch in ai_result.batch_provenance]
                    if ai_result else []
                ),
                "retrieval": (
                    ai_result.retrieval.model_dump() if ai_result and ai_result.retrieval else None
                ),
                "similarFixesCount": len(ai_result.similar_fixes) if ai_result else 0
            },
            "findingsCount": len(findings),
            "findings": findings_json
        }
        return json.dumps(official_doc, ensure_ascii=False, indent=2)
