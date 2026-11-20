from typing import Any, Dict, List, Optional, Tuple

from backend.app.ai.provider import (
    AiProviderError,
    complete_json,
    mask_sensitive_text,
    resolve_provider_config,
)
from backend.app.ai.validator import AiResponseValidator
from backend.app.ai.vector_runtime import VectorRuntime
from backend.app.contracts.schemas import (
    AiItem,
    AiProvenance,
    ExplainResult,
    Finding,
    RetrievalInfo,
)

SYSTEM_PROMPT = """Ты аналитик безопасности Kotlin/Spring Boot. Объясняй только
переданные находки. Не выдумывай findingId, CVE, версии, файлы или строки.
Ответ — один JSON-объект без markdown:
{"summary":"строка","items":[{"findingId":"id из входа","explanation":"строка","remediation":"строка","suggestedFixedVersion":null или строка}]}
Правила:
- explanation и remediation непустые, по-русски, без пересказа сырых секретов.
- suggestedFixedVersion заполняй только значением из allowedFixedVersions этой находки; иначе null.
- Верни ровно один item на каждый findingId, без дублей и пропусков.
- Упоминай только CVE из advisoryIds соответствующей находки, в summary тоже.
- Не добавляй items для findingId, которых нет во входе.
- Если данных мало, скажи это в explanation, но не придумывай факты."""


class AiService:
    def __init__(self):
        self.validator = AiResponseValidator()
        self.provider_config = resolve_provider_config()
        self.model_name = (
            self.provider_config.model if self.provider_config else "deterministic-expert-v1"
        )
        self.vector_runtime = VectorRuntime()

    def explain_findings(self, findings: List[Finding], force_fallback: bool = False) -> ExplainResult:
        """
        Main AI entry point:
        1. Formats masked findings context (no full repo, no raw secrets).
        2. Queries model or fallback deterministic advisor.
        3. Strictly validates response via AiResponseValidator.
        4. If validation fails or exception occurs, uses fallback with note.
        """
        if not findings:
            return ExplainResult(
                status="succeeded",
                model=self.model_name,
                summary="В проекте не обнаружено критических дефектов безопасности.",
                items=[],
                validation_errors=[],
                raw_secrets_checked=True,
                retrieval=self.vector_runtime.status(),
            )

        if force_fallback:
            result = self._generate_expert_fallback(
                findings,
                summary_prefix="[FALLBACK РЕЖИМ]: Ответ сгенерирован встроенной базой правил (AI отключен):",
                provenance=self._fallback_provenance("force_fallback"),
            )
            return self._attach_retrieval(result, findings, execute=False)

        if not self.provider_config:
            result = self._generate_expert_fallback(
                findings,
                summary_prefix="[AI НЕ НАСТРОЕН]: Нет REFORMBOSS_API_KEY в окружении или ~/.omp/agent/.env. Применен детерминированный fallback:",
                provenance=self._fallback_provenance("provider_not_configured"),
            )
            return self._attach_retrieval(result, findings, execute=True)

        try:
            generated_data, provenance = self._generate_ai_json(findings)
        except AiProviderError as exc:
            reason = mask_sensitive_text(str(exc))
            result = self._generate_expert_fallback(
                findings,
                summary_prefix=f"[ОШИБКА AI-ПРОВАЙДЕРА]: {reason}. Использован резервный генератор:",
                provenance=self._fallback_provenance(reason),
            )
            return self._attach_retrieval(result, findings, execute=True)
        except Exception as exc:
            reason = mask_sensitive_text(str(exc))
            result = self._generate_expert_fallback(
                findings,
                summary_prefix=f"[ОШИБКА AI]: {reason}. Использован резервный генератор:",
                provenance=self._fallback_provenance(reason),
            )
            return self._attach_retrieval(result, findings, execute=True)

        structural_errors = self.validator.validate_json_shape(generated_data)
        if structural_errors:
            reason = "JSON: " + "; ".join(structural_errors)
            result = self._generate_expert_fallback(
                findings,
                summary_prefix=f"[ВАЛИДАТОР JSON ОТКЛОНИЛ AI-ОТВЕТ]: {reason}. Применен fallback:",
                provenance=provenance.model_copy(update={"fallback_reason": reason}),
            )
            return self._attach_retrieval(result, findings, execute=True)

        is_valid, validation_errors, validated_items = self.validator.validate(generated_data, findings)
        summary_text = mask_sensitive_text(
            str(generated_data.get("summary") or "Анализ находок безопасности выполнен.")
        )
        if any("summary" in error.lower() for error in validation_errors):
            summary_text = "AI-сводка отклонена валидатором: в ней есть CVE вне advisoryIds находок."
        for item in validated_items:
            item.explanation = mask_sensitive_text(item.explanation)
            item.remediation = mask_sensitive_text(item.remediation)

        if is_valid:
            result = ExplainResult(
                status="succeeded",
                model=provenance.model or self.model_name,
                summary=summary_text,
                items=validated_items,
                validation_errors=[],
                raw_secrets_checked=True,
                provenance=provenance,
            )
            return self._attach_retrieval(result, findings, execute=True)

        if validated_items:
            result = ExplainResult(
                status="fallback",
                model=provenance.model or self.model_name,
                summary=summary_text,
                items=validated_items,
                validation_errors=validation_errors,
                raw_secrets_checked=True,
                provenance=provenance.model_copy(
                    update={"fallback_reason": "validator rejected part of the response"}
                ),
            )
            return self._attach_retrieval(result, findings, execute=True)

        reason = "; ".join(validation_errors)
        result = self._generate_expert_fallback(
            findings,
            summary_prefix=f"[ВАЛИДАТОР ОТКЛОНИЛ AI-ОТВЕТ]: Применен безопасный детерминированный fallback. Ошибки: {reason}",
            provenance=provenance.model_copy(update={"fallback_reason": "validator rejected all items"}),
        )
        return self._attach_retrieval(result, findings, execute=True)

    def generate_synthetic_test(self, findings: List[Finding]) -> Dict[str, Any]:
        """
        Generates a synthetic malicious/hallucinated AI response for jury demo.
        Injects a fake findingId ('fnd_hallucinated_999') and fake CVE ('CVE-2099-99999').
        Returns the raw AI response and the validator rejection results.
        """
        fake_data = {
            "summary": "Синтетический ответ с галлюцинациями для проверки защитного слоя",
            "items": [
                {
                    "findingId": findings[0].id if findings else "fnd_valid_1",
                    "explanation": "Обнаружена критическая уязвимость, связанная с несуществующим эксплойтом CVE-2099-99999.",
                    "remediation": "Обновитесь до несуществующей версии 99.9.9.",
                    "suggestedFixedVersion": "99.9.9"
                },
                {
                    "findingId": "fnd_hallucinated_unknown_888",
                    "explanation": "Модель выдумала уязвимость в файле, которого нет в проекте.",
                    "remediation": "Удалите файл.",
                    "suggestedFixedVersion": None
                }
            ]
        }
        is_valid, errors, valid_items = self.validator.validate(fake_data, findings)
        return {
            "synthetic_response": fake_data,
            "is_valid": is_valid,
            "errors": errors,
            "accepted_items_count": len(valid_items)
        }

    def _generate_ai_json(self, findings: List[Finding]) -> Tuple[Dict[str, Any], AiProvenance]:
        """Call the configured OpenAI-compatible provider with masked context."""
        if not self.provider_config:
            raise AiProviderError("provider is not configured")
        payload = self._build_masked_payload(findings)
        return complete_json(self.provider_config, SYSTEM_PROMPT, payload)

    def _build_masked_payload(self, findings: List[Finding]) -> Dict[str, Any]:
        masked_findings = []
        for f in findings:
            masked_findings.append({
                "findingId": f.id,
                "analyzer": f.analyzer,
                "ruleId": f.ruleId,
                "ruleVersion": f.ruleVersion,
                "severity": f.severity.value,
                "confidence": f.confidence.value,
                "file": f.primaryLocation.path,
                "line": f.primaryLocation.startLine,
                "advisoryIds": list(f.advisoryIds),
                "allowedFixedVersions": self.validator.allowed_fixed_versions_for(f),
                "redactedEvidence": mask_sensitive_text(f.redactedEvidence or "")[:800],
            })
        return {
            "task": "explain_only_these_findings",
            "outputLanguage": "ru",
            "findings": masked_findings,
        }

    def _generate_expert_fallback(
        self,
        findings: List[Finding],
        summary_prefix: str,
        provenance: Optional[AiProvenance] = None,
    ) -> ExplainResult:
        items = []
        for f in findings:
            exp, rem, fixed_ver = self._get_finding_advice(f)
            items.append(AiItem(
                findingId=f.id,
                explanation=exp,
                remediation=rem,
                suggestedFixedVersion=fixed_ver
            ))

        return ExplainResult(
            status="fallback",
            model="deterministic-expert-v1",
            summary=f"{summary_prefix} Найдено {len(findings)} проблем безопасности.",
            items=items,
            validation_errors=[],
            raw_secrets_checked=True,
            provenance=provenance
        )

    def _fallback_provenance(self, reason: str) -> AiProvenance:
        cfg = self.provider_config
        if not cfg:
            return AiProvenance(
                provider="none",
                model="deterministic-expert-v1",
                fallback_reason=mask_sensitive_text(reason)[:300],
            )
        return AiProvenance(
            provider=cfg.provider,
            model=cfg.model,
            endpoint=cfg.endpoint,
            fallback_reason=mask_sensitive_text(reason)[:300],
        )

    def _attach_retrieval(
        self,
        result: ExplainResult,
        findings: List[Finding],
        execute: bool,
    ) -> ExplainResult:
        """Attach optional retrieval without ever failing the main analysis."""
        if not self.vector_runtime.enabled:
            result.retrieval = self.vector_runtime.status()
            return result
        if not execute:
            info = self.vector_runtime.status()
            if info.status != "disabled":
                info.reason = "retrieval not executed in fallback mode"
            result.retrieval = info
            return result
        try:
            info, fixes = self.vector_runtime.retrieve_for_findings(findings)
        except Exception as exc:  # extra guard; runtime is fail-open already
            info = RetrievalInfo(
                status="unavailable",
                reason=f"retrieval error: {mask_sensitive_text(str(exc))[:300]}",
            )
            fixes = []
        result.retrieval = info
        result.similar_fixes = fixes
        return result

    def _get_finding_advice(self, f: Finding) -> tuple[str, str, Optional[str]]:
        if f.ruleId == "KOTLIN-SQL-INJECTION":
            return (
                "SQL-инъекция возникает из-за конкатенации непроверенных параметров пользователя в строку запроса. "
                "Злоумышленник может внедрить произвольные SQL-команды и обойти аутентификацию или похитить данные.",
                "Используйте параметризованные запросы с плейсхолдерами `?` или именованными параметрами: "
                "`jdbcTemplate.query('SELECT * FROM users WHERE name = ?', query)`.",
                None
            )
        elif f.ruleId == "KOTLIN-PATH-TRAVERSAL":
            return (
                "Выход за границы каталога (Path Traversal) позволяет злоумышленнику с помощью последовательностей `../` "
                "получить доступ к файлам вне разрешённой директории хранилища.",
                "Используйте `Path.normalize()` и проверяйте, что результирующий канонический путь строго начинается с базовой директории: "
                "`if (!destPath.startsWith(baseDir)) throw SecurityException()`.",
                None
            )
        elif f.ruleId == "KOTLIN-COMMAND-INJECTION":
            return (
                "Внедрение команд ОС позволяет атакующему выполнить произвольные шелл-команды на сервере с правами приложения.",
                "Избегайте прямого запуска shell-команд через ProcessBuilder. Используйте встроенные Java/Kotlin API или жесткий whitelist допустимых аргументов.",
                None
            )
        elif f.ruleId == "KOTLIN-INSECURE-ENDPOINT":
            return (
                "Эндпоинт выполняет привилегированные административные операции, но не защищён аннотацией `@PreAuthorize`.",
                "Добавьте аннотацию контроля доступа, например `@PreAuthorize('hasRole(\"ADMIN\")')` или ограничьте маршрут в `SecurityFilterChain`.",
                None
            )
        elif f.ruleId == "KOTLIN-IDOR":
            return (
                "Прямая небезопасная ссылка на объект (IDOR): запись извлекается по ID без проверки принадлежности текущему пользователю.",
                "Сверяйте идентификатор владельца записи с `SecurityContextHolder.getContext().authentication.name` перед возвратом сущности.",
                None
            )
        elif f.ruleId == "SCA-VULNERABLE-DEPENDENCY":
            cve_id = f.advisoryIds[0] if f.advisoryIds else "CVE"
            fixed_v = "2.17.1" if "44228" in cve_id else ("2.5.12" if "22965" in cve_id else ("2.13.2.1" if "36518" in cve_id else "5.4.7"))
            return (
                f"Используемая библиотека содержит известную уязвимость {cve_id}. "
                f"Она может привести к удаленному выполнению кода (RCE) или отказу в обслуживании (DoS).",
                f"Обновите версию зависимости в `build.gradle.kts` до безопасной: `{fixed_v}`.",
                fixed_v
            )
        elif f.analyzer == "secret":
            return (
                "В репозитории обнаружен конфиденциальный секрет (пароль, токен или приватный ключ). "
                "Хранение секретов в системе контроля версий создает риск их утечки.",
                "Немедленно отзовите (ротируйте) обнаруженный секрет. Перенесите конфигурацию в защищенные переменные окружения или Vault/Secret Manager.",
                None
            )
        else:
            return (
                f"Обнаружен дефект безопасности по правилу {f.ruleId}.",
                "Проверьте код в указанном месте и устраните нарушение рекомендаций безопасной разработки.",
                None
            )
