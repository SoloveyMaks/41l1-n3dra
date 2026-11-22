"""Статическая проверка method security (runtime-guards)."""

from backend.app.codefacts.guards_static import scan_guards
from backend.app.codefacts.source import SourceFile, sha256_hex


def source(path: str, text: str) -> SourceFile:
    raw = text.encode("utf-8")
    return SourceFile(path=path, text=text, raw=raw, sha256=sha256_hex(raw), size_bytes=len(raw))


PRE_AUTHORIZE_ONLY = """\
package demo

import org.springframework.security.access.prepost.PreAuthorize
import org.springframework.web.bind.annotation.RestController

@RestController
class Orders {
    @PreAuthorize("hasRole('ADMIN')")
    fun delete(id: Long) = id
}
"""

ENABLED = """\
package demo

import org.springframework.security.config.annotation.method.configuration.EnableMethodSecurity
import org.springframework.context.annotation.Configuration

@Configuration
@EnableMethodSecurity
class SecurityConfig
"""

ENABLED_GLOBAL = """\
package demo

import org.springframework.security.config.annotation.method.configuration.EnableGlobalMethodSecurity
import org.springframework.context.annotation.Configuration

@Configuration
@EnableGlobalMethodSecurity(prePostEnabled = true)
class SecurityConfig
"""

SECURED_ONLY = """\
package demo

import org.springframework.security.access.annotation.Secured

class Reports {
    @Secured("ROLE_AUDITOR")
    fun read() = Unit
}
"""


def test_preauthorize_without_enablement_is_finding():
    findings, scopes = scan_guards({
        "src/main/kotlin/demo/Orders.kt": source("src/main/kotlin/demo/Orders.kt", PRE_AUTHORIZE_ONLY),
    })
    assert [f.ruleId for f in findings] == ["CF-GUARD-NOT-ENABLED"]
    finding = findings[0]
    assert finding.severity.value == "high"
    assert finding.analyzer == "codefacts"
    assert finding.primaryLocation.path == "src/main/kotlin/demo/Orders.kt"
    assert finding.primaryLocation.startLine == 8
    # Scope остаётся честным: причина называет отсутствие включения.
    assert len(scopes) == 1
    assert scopes[0]["ruleId"] == "CF-GUARD-STATIC"
    assert scopes[0]["status"] == "checked"
    assert "не найдено" in scopes[0]["reason"]


def test_enablement_method_security_yields_no_findings():
    findings, scopes = scan_guards({
        "src/main/kotlin/demo/Orders.kt": source("src/main/kotlin/demo/Orders.kt", PRE_AUTHORIZE_ONLY),
        "src/main/kotlin/demo/SecurityConfig.kt": source(
            "src/main/kotlin/demo/SecurityConfig.kt", ENABLED
        ),
    })
    assert findings == []
    assert len(scopes) == 1
    assert scopes[0]["status"] == "checked"
    assert scopes[0]["reason"] == (
        "Объявление проверено статически, runtime enforcement не исполнялся."
    )


def test_enablement_global_prepost_yields_no_findings():
    findings, _ = scan_guards({
        "src/main/kotlin/demo/Orders.kt": source("src/main/kotlin/demo/Orders.kt", PRE_AUTHORIZE_ONLY),
        "src/main/kotlin/demo/Cfg.kt": source("src/main/kotlin/demo/Cfg.kt", ENABLED_GLOBAL),
    })
    assert findings == []


def test_global_without_prepost_does_not_enable_preauthorize():
    cfg = """\
package demo
import org.springframework.security.config.annotation.method.configuration.EnableGlobalMethodSecurity
@Configuration
@EnableGlobalMethodSecurity(securedEnabled = true)
class Cfg
"""
    findings, _ = scan_guards({
        "src/main/kotlin/demo/Orders.kt": source("src/main/kotlin/demo/Orders.kt", PRE_AUTHORIZE_ONLY),
        "src/main/kotlin/demo/Cfg.kt": source("src/main/kotlin/demo/Cfg.kt", cfg),
    })
    assert [f.ruleId for f in findings] == ["CF-GUARD-NOT-ENABLED"]


def test_secured_needs_secured_enabled():
    findings, _ = scan_guards({
        "src/main/kotlin/demo/Reports.kt": source("src/main/kotlin/demo/Reports.kt", SECURED_ONLY),
    })
    assert [f.ruleId for f in findings] == ["CF-GUARD-NOT-ENABLED"]

    findings, _ = scan_guards({
        "src/main/kotlin/demo/Reports.kt": source("src/main/kotlin/demo/Reports.kt", SECURED_ONLY),
        "src/main/kotlin/demo/Cfg.kt": source(
            "src/main/kotlin/demo/Cfg.kt",
            "@EnableGlobalMethodSecurity(securedEnabled = true)\nclass Cfg\n",
        ),
    })
    assert findings == []


def test_files_without_annotations_get_no_guard_scope():
    findings, scopes = scan_guards({
        "src/main/kotlin/demo/Plain.kt": source("src/main/kotlin/demo/Plain.kt", "class Plain\n"),
    })
    assert findings == []
    assert scopes == []


def test_java_annotations_are_detected():
    java = """\
package demo;
import org.springframework.security.access.prepost.PreAuthorize;
class Orders {
    @PreAuthorize("hasRole('ADMIN')")
    void delete(Long id) {}
}
"""
    findings, scopes = scan_guards({"src/main/java/demo/Orders.java": source(
        "src/main/java/demo/Orders.java", java
    )})
    assert [f.ruleId for f in findings] == ["CF-GUARD-NOT-ENABLED"]
    assert len(scopes) == 1
