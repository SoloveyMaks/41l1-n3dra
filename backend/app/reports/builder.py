import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.app.ai.provider import mask_sensitive_text
from backend.app.codefacts.source import mask_text
from backend.app.reports.language import naturalize
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


_VERDICT_LABELS = {
    "pass": "нарушений не обнаружено",
    "fail": "требуется исправление",
    "inconclusive": "нужна дополнительная проверка",
    "pending": "итог ещё не определён",
}
_STATUS_LABELS = {
    "queued": "в очереди",
    "running": "выполняется",
    "succeeded": "завершено",
    "failed": "завершилось ошибкой",
    "interrupted": "прервано",
    "cancelled": "отменено",
}
_SEVERITY_LABELS = {
    "critical": "критический",
    "high": "высокий",
    "medium": "средний",
    "low": "низкий",
    "info": "информационный",
}
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
_CONFIDENCE_LABELS = {
    "confirmed": "подтверждено",
    "potential": "потенциально",
    "inconclusive": "не определено",
}

_SKIPPED_SCOPE_LABELS = {
    "runtime-configuration": "конфигурация времени выполнения",
    "runtime-guards": "защита во время выполнения",
    "binaries-and-archives": "бинарники и архивы",
    "dependency-resolution": "разрешение зависимостей",
    "generated-code": "сгенерированный код",
}


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
                reason = "Полнота проверки не установлена: проверена только часть областей"
                if skipped_scopes:
                    labels = [_SKIPPED_SCOPE_LABELS.get(scope, scope) for scope in skipped_scopes]
                    reason += "; не проверено: " + ", ".join(labels)
                reason += "."
            elif coverage_status == "complete" and not expected_verified:
                reason = "Полнота проверки не установлена: обязательные области проверки не подтверждены."
            elif coverage_status == "unavailable":
                reason = "Полнота проверки недоступна: данные покрытия недоступны."
            else:
                reason = "Полнота проверки неизвестна: данные покрытия не предоставлены."
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
        now = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
        verdict = _VERDICT_LABELS.get(passport.verdict.value, "итог не определён")
        status = _STATUS_LABELS.get(execution_status, execution_status)

        md = []
        md.append("# Отчёт о проверке безопасности кода")
        md.append(f"**Сценарий:** {workflow_name}  ")
        md.append(f"**Номер запуска:** `{run_id}`  ")
        md.append(f"**Дата:** {now}  ")
        md.append(f"**Статус:** {status}  ")
        md.append(f"**Итог:** {verdict}  \n")
        md.append(
            "Отчёт собран по сохранённым результатам проверки. Повторный запуск сканеров "
            "и обращения к модели не выполнялись. Итог относится к проверенному покрытию, "
            "а не ко всему проекту."
        )
        md.append("")

        md.append("## Основные показатели")
        md.append("| Показатель | Значение |")
        md.append("| :--- | :--- |")
        md.append(f"| Всего находок | **{len(findings)}** |")
        md.append(f"| Критические | {sum(1 for f in findings if f.severity.value == 'critical')} |")
        md.append(f"| Высокие | {sum(1 for f in findings if f.severity.value == 'high')} |")
        md.append(f"| Средние | {sum(1 for f in findings if f.severity.value == 'medium')} |")
        md.append(f"| Низкие | {sum(1 for f in findings if f.severity.value == 'low')} |")
        md.append(f"| Информационные | {sum(1 for f in findings if f.severity.value == 'info')} |")
        md.append(f"| Подтверждено | {passport.confirmed_findings} |")
        md.append(f"| Потенциально | {passport.potential_findings} |")
        md.append(f"| Проверено файлов | {passport.files_scanned} |")
        md.append(f"| Пропущено файлов | {passport.files_skipped} |")
        md.append(f"| Строк кода | {passport.lines_of_code} |")
        md.append(f"| Запущено правил | {len(passport.rules_executed)} |")
        md.append(f"| Ошибок парсера | {passport.parser_errors} |")
        md.append(f"| Неразрешённых зависимостей | {passport.unresolved_dependencies} |")
        if passport.completeness_known:
            md.append("| Полнота проверки | подтверждена |")
        else:
            if passport.coverage_status == "partial" or (
                passport.coverage_status == "complete" and not passport.completeness_known
            ):
                completeness_label = "не установлена"
            elif passport.coverage_status == "unavailable":
                completeness_label = "недоступна"
            else:
                completeness_label = "неизвестна"
            md.append(f"| Полнота проверки | {completeness_label} |")
        md.append("")

        if passport.coverage_reasons:
            md.append("## Что осталось за пределами проверки")
            for reason in passport.coverage_reasons:
                md.append(f"- {mask_sensitive_text(naturalize(reason))}")
            md.append("")

        if passport.coverage_warnings:
            md.append("### Предупреждения по покрытию")
            for warning in passport.coverage_warnings:
                md.append(f"- {mask_sensitive_text(naturalize(warning))}")
            md.append("")

        md.append("## Почему такой итог")
        md.append(f"- Критических находок: {policy.critical_count}")
        md.append(f"- Высокого риска: {policy.high_count}")
        md.append(f"- Среднего риска: {policy.medium_count}")
        md.append(f"- Низкого риска: {policy.low_count}")
        if policy.reasons:
            md.append("")
            for reason in policy.reasons:
                md.append(f"- {mask_sensitive_text(naturalize(reason))}")
        md.append("")

        if ai_result:
            md.append("## Пояснения к находкам")
            if ai_result.status == "succeeded":
                md.append("Пояснения сформированы и проверены валидатором.")
            else:
                md.append("Модель недоступна, поэтому показаны базовые рекомендации.")
            md.append(f"**Модель:** {ai_result.model or 'не указана'}")
            md.append(f"> {mask_sensitive_text(ai_result.summary)}\n")

        md.append(f"## Находки ({len(findings)})")
        if not findings:
            md.append(
                "Находок не обнаружено. Это не доказывает отсутствие уязвимостей за "
                "пределами проверенного покрытия."
            )
        else:
            ai_item_map = {item.findingId: item for item in (ai_result.items if ai_result else [])}
            ordered = sorted(
                findings,
                key=lambda item: (
                    _SEVERITY_ORDER.get(item.severity.value, 9),
                    item.ruleId,
                    item.id,
                ),
            )
            for idx, finding in enumerate(ordered, 1):
                severity = _SEVERITY_LABELS.get(finding.severity.value, finding.severity.value)
                confidence = _CONFIDENCE_LABELS.get(finding.confidence.value, finding.confidence.value)
                md.append(f"### {idx}. {severity.capitalize()}: {finding.ruleId}")
                md.append(f"- **ID:** `{finding.id}`")
                md.append(f"- **Правило:** `{finding.ruleId}` ({finding.analyzer})")
                md.append(f"- **Где:** `{finding.primaryLocation.path}:{finding.primaryLocation.startLine}`")
                md.append(f"- **Почему это важно:** {mask_sensitive_text(naturalize(finding.severityBasis))}")
                md.append(f"- **Достоверность:** {confidence} ({mask_sensitive_text(naturalize(finding.confidenceBasis))})")
                if finding.advisoryIds:
                    md.append(f"- **Связанные записи безопасности:** {', '.join(finding.advisoryIds)}")

                if finding.redactedEvidence:
                    md.append(f"```text\n{mask_text(finding.redactedEvidence)}\n```")

                if finding.trace:
                    md.append("**Цепочка доказательств:**")
                    for step in finding.trace:
                        md.append(f"- {step.step}. {mask_sensitive_text(naturalize(step.description))}")

                if finding.id in ai_item_map:
                    item = ai_item_map[finding.id]
                    md.append("")
                    md.append("**Пояснение:**")
                    md.append(mask_sensitive_text(item.explanation))
                    md.append("")
                    md.append("**Рекомендация:**")
                    md.append(f"`{mask_sensitive_text(item.remediation)}`")
                    if item.suggestedFixedVersion:
                        md.append(f"\n*Рекомендуемая безопасная версия:* `{item.suggestedFixedVersion}`")
                md.append("")

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
