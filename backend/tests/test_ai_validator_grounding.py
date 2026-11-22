import json

from backend.app.ai.validator import AiResponseValidator
from backend.app.contracts.schemas import (
    Confidence,
    Finding,
    Location,
    Severity,
)


def make_finding(analyzer="sast", path="src/App.kt", advisory_ids=None) -> Finding:
    return Finding(
        id="f1",
        fingerprint="fp1",
        analyzer=analyzer,
        ruleId="RULE-1",
        severity=Severity.HIGH,
        severityBasis="test",
        confidence=Confidence.CONFIRMED,
        confidenceBasis="test",
        primaryLocation=Location(path=path, startLine=10, endLine=20, startColumn=1, endColumn=40),
        redactedEvidence="***",
        advisoryIds=advisory_ids or [],
    )


def make_validator(tmp_path, advisories=None) -> AiResponseValidator:
    path = tmp_path / "advisories.json"
    path.write_text(json.dumps(advisories or []), encoding="utf-8")
    return AiResponseValidator(advisories_path=path)


def item(explanation, remediation, version=None):
    return {
        "findingId": "f1",
        "explanation": explanation,
        "remediation": remediation,
        "suggestedFixedVersion": version,
    }


def test_evidence_path_and_line_are_accepted(tmp_path):
    validator = make_validator(tmp_path)
    ok, errors, items = validator.validate(
        {"items": [item("Проблема в src/App.kt:12 — конкатенация SQL.", "Используйте параметризованный запрос.")]},
        [make_finding()],
    )
    assert ok is True, errors
    assert len(items) == 1


def test_invented_path_is_rejected(tmp_path):
    validator = make_validator(tmp_path)
    ok, errors, items = validator.validate(
        {"items": [item("Смотрите Missing.kt:999.", "Исправьте.")]},
        [make_finding()],
    )
    assert ok is False
    assert items == []
    assert any("Missing.kt" in error and "вне её evidence" in error for error in errors)


def test_line_outside_evidence_is_rejected(tmp_path):
    validator = make_validator(tmp_path)
    ok, errors, _ = validator.validate(
        {"items": [item("Смотрите src/App.kt:900.", "Исправьте.")]},
        [make_finding()],
    )
    assert ok is False
    assert any("строка 900" in error for error in errors)


def test_invented_update_version_is_rejected(tmp_path):
    validator = make_validator(tmp_path)
    ok, errors, _ = validator.validate(
        {"items": [item("Есть риск.", "Обновите до 99.9.9.")]},
        [make_finding()],
    )
    assert ok is False
    assert any("99.9.9" in error for error in errors)


def test_context_version_without_update_claim_is_allowed(tmp_path):
    validator = make_validator(tmp_path)
    ok, errors, _ = validator.validate(
        {"items": [item("Зависимость spring-boot 2.5.0 уязвима.", "Проверьте advisory.")]},
        [make_finding()],
    )
    assert ok is True, errors


def test_sca_fixed_version_from_advisory_is_allowed(tmp_path):
    validator = make_validator(tmp_path, [{"id": "CVE-2021-1", "fixed_versions": ["2.9.10.4"]}])
    finding = make_finding(analyzer="sca", path="build.gradle.kts", advisory_ids=["CVE-2021-1"])
    ok, errors, _ = validator.validate(
        {"items": [item("Уязвимая версия.", "Обновите до 2.9.10.4.", "2.9.10.4")]},
        [finding],
    )
    assert ok is True, errors


def test_sca_invented_version_is_rejected(tmp_path):
    validator = make_validator(tmp_path, [{"id": "CVE-2021-1", "fixed_versions": ["2.9.10.4"]}])
    finding = make_finding(analyzer="sca", path="build.gradle.kts", advisory_ids=["CVE-2021-1"])
    ok, errors, _ = validator.validate(
        {"items": [item("Уязвимая версия.", "Обновите до 99.9.9.", "99.9.9")]},
        [finding],
    )
    assert ok is False
    assert any("99.9.9" in error for error in errors)
