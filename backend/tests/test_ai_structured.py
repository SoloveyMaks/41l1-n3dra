import json

from backend.app.ai.service import AiService
from backend.app.ai.validator import AiResponseValidator
from backend.app.contracts.schemas import (
    Confidence, Finding, FindingStatus, Location, Severity,
)


def make_finding(analyzer="sast", path="src/App.kt", advisory_ids=None, evidence="***") -> Finding:
    return Finding(
        id="f1", fingerprint="fp1", analyzer=analyzer, ruleId="KOTLIN-SQL-INJECTION",
        severity=Severity.HIGH, severityBasis="taint", confidence=Confidence.CONFIRMED,
        confidenceBasis="source-to-sink",
        primaryLocation=Location(path=path, startLine=10, endLine=20, startColumn=1, endColumn=40),
        redactedEvidence=evidence, advisoryIds=advisory_ids or [],
    )


def item(**overrides):
    data = {
        "findingId": "f1",
        "problem": "Параметр попадает в SQL без плейсхолдера.",
        "attackScenario": "Пользователь передаёт значение в src/App.kt:12 и получает данные другой записи.",
        "impact": "Чтение чужих записей.",
        "preconditions": "Доступ к endpoint без проверки владельца.",
        "verification": "Повторить запрос на тестовом стенде.",
        "explanation": "Конкатенация в SQL.",
        "remediation": "Использовать параметризованный запрос.",
        "suggestedFixedVersion": None,
    }
    data.update(overrides)
    return data


def test_structured_item_is_accepted(tmp_path):
    path = tmp_path / "adv.json"
    path.write_text("[]", encoding="utf-8")
    validator = AiResponseValidator(advisories_path=path)
    ok, errors, items = validator.validate({"items": [item()]}, [make_finding()])
    assert ok is True, errors
    assert items[0].attackScenario.startswith("Пользователь")


def test_partial_structured_item_is_rejected(tmp_path):
    path = tmp_path / "adv.json"
    path.write_text("[]", encoding="utf-8")
    validator = AiResponseValidator(advisories_path=path)
    data = item()
    data.pop("verification")
    ok, errors, _ = validator.validate({"items": [data]}, [make_finding()])
    assert ok is False
    assert any("verification" in error for error in errors)


def test_invented_path_in_attack_scenario_is_rejected(tmp_path):
    path = tmp_path / "adv.json"
    path.write_text("[]", encoding="utf-8")
    validator = AiResponseValidator(advisories_path=path)
    ok, errors, _ = validator.validate(
        {"items": [item(attackScenario="Смотрите Missing.kt:999")]}, [make_finding()]
    )
    assert ok is False
    assert any("Missing.kt" in error for error in errors)


def test_fallback_has_all_sections_and_no_secret_value():
    service = AiService()
    finding = make_finding(analyzer="secret", path="application.properties")
    sections = service._fallback_sections(finding)
    for key in ("problem", "attackScenario", "impact", "preconditions", "verification", "remediation"):
        assert isinstance(sections[key], str) and sections[key]
    assert "application.properties" in sections["problem"]


def test_invented_http_route_is_rejected(tmp_path):
    path = tmp_path / "adv.json"
    path.write_text("[]", encoding="utf-8")
    validator = AiResponseValidator(advisories_path=path)
    finding = make_finding(evidence='@GetMapping("/admin/dump") class C')
    ok, errors, _ = validator.validate(
        {"items": [item(attackScenario="Отправить POST /invented-jury-export")]}, [finding]
    )
    assert ok is False
    assert any("invented-jury-export" in error for error in errors)


def test_confirmed_http_route_is_accepted(tmp_path):
    path = tmp_path / "adv.json"
    path.write_text("[]", encoding="utf-8")
    validator = AiResponseValidator(advisories_path=path)
    finding = make_finding(evidence='@GetMapping("/admin/dump") class C')
    ok, errors, _ = validator.validate(
        {"items": [item(attackScenario="Отправить GET /admin/dump")]}, [finding]
    )
    assert ok is True, errors


def test_potential_endpoint_fallback_does_not_claim_proof():
    service = AiService()
    finding = make_finding(path="AdminController.kt")
    finding.ruleId = "KOTLIN-INSECURE-ENDPOINT"
    finding.confidence = Confidence.POTENTIAL
    finding.status = FindingStatus.POTENTIAL
    sections = service._fallback_sections(finding)
    text = " ".join(value for value in sections.values() if isinstance(value, str)).lower()
    assert "потенциальная" in text
    assert "глобальные securityfilterchain" in text
    assert "не проверялись" in text
    assert "при подтверждении" in text
