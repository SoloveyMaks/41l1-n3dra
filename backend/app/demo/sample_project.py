import io
import zipfile
from pathlib import Path


def create_sample_project_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # build.gradle.kts
        build_gradle_kts = """plugins {
    kotlin("jvm") version "1.9.22"
    id("org.springframework.boot") version "2.5.0"
    id("io.spring.dependency-management") version "1.1.4"
}

group = "com.bank.cyber"
version = "1.0.0-SNAPSHOT"

repositories {
    mavenCentral()
}

dependencies {
    implementation("org.springframework.boot:spring-boot-starter-web:2.5.0")
    implementation("org.apache.logging.log4j:log4j-core:2.14.1")
    implementation("com.fasterxml.jackson.core:jackson-databind:2.9.8")
    implementation(libs.spring.security)
    testImplementation("org.springframework.boot:spring-boot-starter-test:2.5.0")
}
"""
        zf.writestr("build.gradle.kts", build_gradle_kts)

        # settings.gradle.kts
        settings_gradle_kts = """rootProject.name = "cyber-bank-core"
"""
        zf.writestr("settings.gradle.kts", settings_gradle_kts)

        # gradle/libs.versions.toml
        libs_toml = """[versions]
spring = "5.4.0"
log4j = "2.14.1"

[libraries]
spring-security = { module = "org.springframework.security:spring-security-web", version.ref = "spring" }
"""
        zf.writestr("gradle/libs.versions.toml", libs_toml)

        # application.properties with secrets
        app_properties = """# Bank Core Service Configuration
spring.application.name=cyber-bank-core
server.port=8080

# Database credentials (vulnerable to secret leaks)
spring.datasource.url=jdbc:postgresql://postgres:db_master_pass2026@db.bank.internal:5432/core
spring.datasource.username=bank_admin
spring.datasource.password=SuperSecretBankPass2026!

# Third-party payment gateway integration
payment.gateway.api_key=gateway_token_mock_984729184719284719284712
payment.gateway.secret=gateway_secret_mock_847192847192847192847123

# Default environment secret
jwt.secret.key=${AUTH_SECRET:DefaultHardcodedSecretKey2026!}
"""
        zf.writestr("src/main/resources/application.properties", app_properties)

        # PEM private key block
        pem_key = """-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA0Y3y...[REDACTED SAMPLE KEY DATA]...
-----END RSA PRIVATE KEY-----
"""
        zf.writestr("src/main/resources/certs/service-key.pem", pem_key)

        # UserController.kt with SQL injection
        user_controller = """package com.bank.controller

import org.springframework.web.bind.annotation.*
import org.springframework.jdbc.core.JdbcTemplate

@RestController
@RequestMapping("/api/users")
class UserController(private val jdbcTemplate: JdbcTemplate) {

    @GetMapping("/search")
    fun searchUsers(@RequestParam query: String): List<Map<String, Any>> {
        // VULNERABLE: Direct string concatenation into SQL query
        val sql = "SELECT * FROM users WHERE username = '" + query + "' AND active = true"
        return jdbcTemplate.queryForList(sql)
    }

    @GetMapping("/{id}")
    fun getUserProfile(@PathVariable id: Long): Map<String, Any> {
        // Potential IDOR: fetches object without checking authenticated owner
        val sql = "SELECT id, name, email, balance FROM accounts WHERE id = " + id
        return jdbcTemplate.queryForMap(sql)
    }
}
"""
        zf.writestr("src/main/kotlin/com/bank/controller/UserController.kt", user_controller)

        # DocumentController.kt with Path Traversal
        doc_controller = """package com.bank.controller

import org.springframework.web.bind.annotation.*
import java.io.File
import java.io.FileInputStream

@RestController
@RequestMapping("/api/documents")
class DocumentController {

    @GetMapping("/download")
    fun downloadDocument(@RequestParam filename: String): ByteArray {
        // VULNERABLE: Path Traversal (no boundary check or canonicalPath validation)
        val file = File("/var/bank/storage/docs/" + filename)
        val fis = FileInputStream(file)
        return fis.readAllBytes()
    }
}
"""
        zf.writestr("src/main/kotlin/com/bank/controller/DocumentController.kt", doc_controller)

        # AdminController.kt with sensitive endpoint and command execution
        admin_controller = """package com.bank.controller

import org.springframework.web.bind.annotation.*

@RestController
@RequestMapping("/api/admin")
class AdminController {

    // VULNERABLE: Sensitive admin endpoint without @PreAuthorize
    @GetMapping("/dump-database")
    fun dumpDatabase(@RequestParam targetPath: String): String {
        // VULNERABLE: Command Injection via ProcessBuilder
        val process = ProcessBuilder("backup_tool.sh", targetPath).start()
        process.waitFor()
        return "Backup created at " + targetPath
    }
}
"""
        zf.writestr("src/main/kotlin/com/bank/controller/AdminController.kt", admin_controller)

    return buf.getvalue()


if __name__ == "__main__":
    content = create_sample_project_zip()
    demo_dir = Path(__file__).resolve().parent
    out_file = demo_dir / "sample_bank_app.zip"
    out_file.write_bytes(content)
    print(f"Created sample project zip: {out_file} ({len(content)} bytes)")
