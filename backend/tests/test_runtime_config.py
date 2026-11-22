"""Статическая проверка конфигурации и честность coverage-skipped."""

from backend.app.codefacts.config_static import is_config_path, scan_config_file, scan_configs
from backend.app.codefacts.source import SourceFile, sha256_hex


def source(path: str, text: str) -> SourceFile:
    raw = text.encode("utf-8")
    return SourceFile(path=path, text=text, raw=raw, sha256=sha256_hex(raw), size_bytes=len(raw))


INSECURE = """\
management:
  endpoints:
    web:
      exposure:
        include: "*"
debug: true
server:
  ssl:
    enabled: false
spring:
  security:
    user:
      password: changeme
"""

SAFE = """\
server:
  port: 8080
  ssl:
    enabled: true
spring:
  datasource:
    url: jdbc:postgresql://db:5432/app
    password: ${DB_PASSWORD}
management:
  endpoints:
    web:
      exposure:
        include: health,info
"""


def test_insecure_application_yml_yields_findings():
    findings, skipped = scan_config_file(source("src/main/resources/application.yml", INSECURE))
    rules = {f.ruleId for f in findings}
    assert "CF-CONFIG-ACTUATOR-EXPOSED" in rules
    assert "CF-CONFIG-DEBUG-ENABLED" in rules
    assert "CF-CONFIG-SSL-DISABLED" in rules
    assert "CF-CONFIG-DEFAULT-CREDS" in rules
    for finding in findings:
        assert finding.analyzer == "codefacts"
        assert finding.primaryLocation.path == "src/main/resources/application.yml"
        assert finding.primaryLocation.startLine >= 1
    assert skipped == []


def test_safe_config_is_quiet():
    findings, skipped = scan_config_file(
        source("src/main/resources/application.yml", SAFE)
    )
    # Безопасный конфиг не даёт находок; единственная неразрешённая
    # плейсхолдер-причина названа конкретным путём и строкой.
    assert findings == []
    assert len(skipped) == 1
    assert "application.yml:8" in skipped[0]
    assert "unresolved security-relevant placeholder ${DB_PASSWORD}" in skipped[0]


def test_unresolved_security_placeholder_is_explicit_skip():
    findings, skipped = scan_config_file(
        source("src/main/resources/application-prod.yml", "app:\n  token: ${APP_TOKEN}\n")
    )
    assert findings == []
    assert len(skipped) == 1
    assert "unresolved security-relevant placeholder" in skipped[0]
    assert "application-prod.yml" in skipped[0]


def test_config_path_detection():
    assert is_config_path("src/main/resources/application.yml")
    assert is_config_path("src/main/resources/bootstrap.properties")
    assert is_config_path(".env")
    assert is_config_path("Dockerfile")
    assert is_config_path("docker-compose.yml")
    assert not is_config_path("src/main/kotlin/App.kt")
    assert not is_config_path("README.md")


def test_scan_configs_only_visits_config_files():
    sources = {
        "src/main/kotlin/demo/App.kt": source("src/main/kotlin/demo/App.kt", "class App\n"),
        "src/main/resources/application.yml": source(
            "src/main/resources/application.yml", "debug: true\n"
        ),
        "README.md": source("README.md", "# readme\n"),
    }
    findings, paths, skipped = scan_configs(sources)
    assert paths == ["src/main/resources/application.yml"]
    assert [f.ruleId for f in findings] == ["CF-CONFIG-DEBUG-ENABLED"]
    assert skipped == []


def test_coverage_has_no_hardcoded_runtime_configuration_skip():
    """runtime-configuration больше не вписывается руками в skippedScopes."""
    from backend.app.codefacts.canonical import CanonicalMapBuilder
    from backend.app.codefacts.extractor import extract_facts

    config = source("src/main/resources/application.yml", "debug: true\n")
    sources = {config.path: config}
    snapshot_files = [
        {"path": config.path, "sha256": config.sha256, "sizeBytes": config.size_bytes}
    ]
    facts = extract_facts(sources, len(snapshot_files))
    builder = CanonicalMapBuilder(
        workspace_id="ws", run_id="run", snapshot_id="snap",
        artifact_hash="0" * 64, snapshot_manifest_hash="1" * 64,
        workflow_revision_id="rev", facts=facts, sources=sources,
        snapshot_files=snapshot_files, backend_findings=facts.findings,
    )
    coverage, _parser_errors = builder._build_coverage()

    assert "runtime-configuration" not in coverage["skippedScopes"]
    assert coverage["skippedScopes"] == []
    assert coverage["scopes"][0]["ruleId"] == "CF-CONFIG-STATIC"
    assert coverage["scopes"][0]["status"] == "checked"
    assert coverage["status"] == "complete"
    # Находка уходит тем же потоком, что и остальные codefacts-findings.
    assert [f.ruleId for f in facts.findings] == ["CF-CONFIG-DEBUG-ENABLED"]
