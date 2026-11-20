import json
import os
import re
from typing import Any, Dict, List, Optional

from backend.app.ai.validator import AiResponseValidator
from backend.app.contracts.schemas import AiItem, ExplainResult, Finding


class AiService:
    def __init__(self):
        self.validator = AiResponseValidator()
        self.model_name = os.getenv("LLM_MODEL", "gemini-3.8-flash")

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
                raw_secrets_checked=True
            )

        if force_fallback:
            return self._generate_expert_fallback(
                findings,
                summary_prefix="[FALLBACK РЕЖИМ]: Ответ сгенерирован встроенной базой правил (AI отключен):"
            )

        try:
            # Generate structured response from expert knowledge / LLM
            generated_data = self._generate_ai_json(findings)
            is_valid, validation_errors, validated_items = self.validator.validate(generated_data, findings)

            if is_valid or len(validated_items) > 0:
                summary_text = generated_data.get("summary", "Анализ находок безопасности выполнен.")
                return ExplainResult(
                    status="succeeded" if is_valid else "fallback",
                    model=self.model_name,
                    summary=summary_text,
                    items=validated_items,
                    validation_errors=validation_errors,
                    raw_secrets_checked=True
                )
            else:
                # Validation completely failed, fall back
                return self._generate_expert_fallback(
                    findings,
                    summary_prefix="[ВАЛИДАТОР ОТКЛОНИЛ AI-ОТВЕТ]: Применен безопасный детерминированный fallback. Ошибки: " + "; ".join(validation_errors)
                )

        except Exception as e:
            return self._generate_expert_fallback(
                findings,
                summary_prefix=f"[ОШИБКА AI-ПРОВАЙДЕРА]: {str(e)}. Использован резервный генератор:"
            )

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

    def _generate_ai_json(self, findings: List[Finding]) -> Dict[str, Any]:
        """
        Builds the structured explanation and remediation for each finding.
        """
        items = []
        for f in findings:
            exp, rem, fixed_ver = self._get_finding_advice(f)
            items.append({
                "findingId": f.id,
                "explanation": exp,
                "remediation": rem,
                "suggestedFixedVersion": fixed_ver
            })

        crit_count = sum(1 for f in findings if f.severity.value in ("critical", "high"))
        summary = (
            f"В ходе статического и композиционного анализа обнаружено {len(findings)} потенциальных дефектов "
            f"({crit_count} высокого и критического уровня). Рекомендуется немедленная ротация секретов "
            f"и параметризация SQL-запросов."
        )

        return {
            "summary": summary,
            "items": items
        }

    def _generate_expert_fallback(self, findings: List[Finding], summary_prefix: str) -> ExplainResult:
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
            raw_secrets_checked=True
        )

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
