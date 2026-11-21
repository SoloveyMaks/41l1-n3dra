"""Self-test scenarios for the analyzers.

Each scenario writes a small, fixed project into a temporary directory and
checks the analyzer output.  The scenarios cover formatting changes, renames,
placeholders, safe neighbours, ownership checks and secret masking.

Run with::

    python -m analyzers.selftest
"""

from __future__ import annotations

import json
import hashlib
import sys
import tempfile
import textwrap
from pathlib import Path

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from analyzers.cg26.contract import build_bundle
    from analyzers.cg26.project import Project
    from analyzers.scan import run
else:
    from .cg26.contract import build_bundle
    from .cg26.project import Project
    from .scan import run

_HERE = Path(__file__).resolve().parent
_DATABASE_CANDIDATES = [
    _HERE / "fixtures" / "vulnerabilities.json",
    _HERE.parent / "vulnerability-database" / "vulnerabilities.json",
]
DATABASE = next((path for path in _DATABASE_CANDIDATES if path.is_file()), _DATABASE_CANDIDATES[0])

SECRETS = {
    "src/main/kotlin/lab/selftest/Credentials.kt": """
        package lab.selftest

        internal class Credentials {
            // Комментарий не должен влиять на поиск.
            private val legacyImportPassword =
                "SYNTHETIC_PASSWORD_9f3Kd"

            private val pricingApiKey = "SYNTHETIC_API_KEY_7h2Lm"

            fun headers(): Map<String, String> = mapOf(
                "X-Import-Password" to legacyImportPassword,
                "X-Api-Key" to pricingApiKey,
            )

            fun fallback(): String {
                val access = System.getenv("DEPOT_TOKEN")
                    ?: "SYNTHETIC_FALLBACK_5n8Q"
                return access
            }
        }
    """,
    "src/main/kotlin/lab/selftest/Composite.kt": """
        package lab.selftest

        import org.springframework.http.HttpHeaders

        internal class Composite {
            fun build(): HttpHeaders {
                val series = "SYNTHETIC_WARRANTY_"
                val grant = "A1b2C3d4E5f6"
                val access = series + grant
                return HttpHeaders().apply { setBearerAuth(access) }
            }
        }
    """,
    "src/main/kotlin/lab/selftest/Placeholders.kt": """
        package lab.selftest

        internal class Placeholders {
            private val pricingApiKey = "YOUR_API_KEY"
            private val legacyPassword = "changeme"
        }
    """,
}

SAST = {
    "src/main/kotlin/lab/selftest/A.kt": """
        package lab.selftest

        import org.springframework.jdbc.core.JdbcTemplate
        import org.springframework.web.bind.annotation.GetMapping
        import org.springframework.web.bind.annotation.RequestMapping
        import org.springframework.web.bind.annotation.RequestParam
        import org.springframework.web.bind.annotation.RestController

        @RestController
        @RequestMapping("/api/items")
        class Lookup(private val jdbc: JdbcTemplate) {
            @GetMapping("/unsafe")
            fun unsafe(@RequestParam value: String): List<String> =
                jdbc.queryForList(
                    "SELECT name FROM item WHERE name = '$value'",
                    String::class.java,
                )

            @GetMapping("/safe")
            fun safe(@RequestParam value: String): List<String> =
                jdbc.queryForList("SELECT name FROM item WHERE name = ?", String::class.java, value)
        }
    """,
    "src/main/kotlin/lab/selftest/B.kt": """
        package lab.selftest

        import java.net.URI
        import javax.net.ssl.HostnameVerifier
        import javax.net.ssl.HttpsURLConnection

        internal class BadTls {
            fun open(): HttpsURLConnection {
                val url = URI("https://example.invalid").toURL()
                return (url.openConnection() as HttpsURLConnection).apply {
                    hostnameVerifier = HostnameVerifier { _, _ -> true }
                }
            }
        }

        internal class GoodTls {
            fun open(): HttpsURLConnection =
                URI("https://example.invalid").toURL().openConnection() as HttpsURLConnection
        }
    """,
    "src/main/kotlin/lab/selftest/C.kt": """
        package lab.selftest

        import org.springframework.context.annotation.Bean
        import org.springframework.context.annotation.Configuration
        import org.springframework.web.cors.CorsConfiguration
        import org.springframework.web.cors.UrlBasedCorsConfigurationSource

        @Configuration
        class BadCors {
            @Bean
            fun source(): UrlBasedCorsConfigurationSource {
                val config = CorsConfiguration().apply {
                    allowedOriginPatterns = listOf("*")
                    allowCredentials = true
                }
                return UrlBasedCorsConfigurationSource().apply {
                    registerCorsConfiguration("/api/items/unsafe", config)
                }
            }
        }

        @Configuration
        class SafeCors {
            @Bean
            fun source(): UrlBasedCorsConfigurationSource {
                val config = CorsConfiguration().apply {
                    allowedOrigins = listOf("https://app.example")
                    allowCredentials = true
                }
                return UrlBasedCorsConfigurationSource().apply {
                    registerCorsConfiguration("/api/items/safe", config)
                }
            }
        }
    """,
    "src/main/kotlin/lab/selftest/D.kt": """
        package lab.selftest

        import org.springframework.context.annotation.Bean
        import org.springframework.context.annotation.Configuration
        import org.springframework.http.HttpMethod.GET
        import org.springframework.security.config.annotation.web.builders.HttpSecurity
        import org.springframework.security.web.SecurityFilterChain

        @Configuration
        class Security {
            @Bean
            fun chain(http: HttpSecurity): SecurityFilterChain = http
                .authorizeHttpRequests { rules ->
                    rules.requestMatchers(GET, "/api/items/unsafe").permitAll()
                    rules.requestMatchers(GET, "/api/items/safe").permitAll()
                    rules.requestMatchers("/api/orders/**").authenticated()
                    rules.anyRequest().denyAll()
                }
                .build()
        }
    """,
    "src/main/kotlin/lab/selftest/E.kt": """
        package lab.selftest

        import org.springframework.jdbc.core.JdbcTemplate
        import org.springframework.web.bind.annotation.GetMapping
        import org.springframework.web.bind.annotation.RestController

        @RestController
        class Export(private val jdbc: JdbcTemplate) {
            @GetMapping("/api/items/unsafe")
            fun export(): List<Map<String, Any?>> =
                jdbc.queryForList("SELECT name, customer_id FROM item")
        }

        @RestController
        class Catalog {
            @GetMapping("/api/items/safe")
            fun names(): List<String> = listOf("a", "b")
        }
    """,
    "src/main/kotlin/lab/selftest/F.kt": """
        package lab.selftest

        import java.security.Principal
        import org.springframework.jdbc.core.JdbcTemplate
        import org.springframework.stereotype.Repository
        import org.springframework.web.bind.annotation.GetMapping
        import org.springframework.web.bind.annotation.PathVariable
        import org.springframework.web.bind.annotation.RequestMapping
        import org.springframework.web.bind.annotation.RestController

        @RestController
        @RequestMapping("/api/orders")
        class Orders(private val reader: OrderReader) {
            @GetMapping("/{orderId}")
            fun get(@PathVariable orderId: Long): Any? = reader.read(orderId)

            @GetMapping("/mine/{orderId}")
            fun mine(@PathVariable orderId: Long, principal: Principal): Any? =
                reader.readOwned(orderId, principal.name)
        }

        @Repository
        class OrderReader(private val jdbc: JdbcTemplate) {
            fun read(orderId: Long): Any? = jdbc.query(
                "SELECT customer_id, note FROM orders WHERE order_id = ?",
                { row, _ -> row.getString(1) },
                orderId,
            ).firstOrNull()

            fun readOwned(orderId: Long, customerId: String): Any? = jdbc.query(
                "SELECT customer_id, note FROM orders WHERE order_id = ? AND customer_id = ?",
                { row, _ -> row.getString(1) },
                orderId,
                customerId,
            ).firstOrNull()
        }
    """,
}

SCA = {
    "build.gradle.kts": """
        val archiveFormatVersion = "1.25.0"
        val manifestVersion = providers.gradleProperty("legacyManifestJsonVersion").get()

        dependencies {
            implementation("org.apache.commons:commons-text:1.9")
            implementation("org.apache.commons:commons-compress:$archiveFormatVersion")
            add(
                "implementation",
                "commons-net:commons-net:3.8.0",
            )
            implementation("org.json:json:$manifestVersion")
            implementation("org.apache.commons:commons-lang3:3.20.0")
            implementation("org.example:unknown:$missingVersion")
        }
    """,
    "gradle.properties": "legacyManifestJsonVersion=20230227\n",
    "gradle.lockfile": """
        org.apache.commons:commons-text:1.10.0=runtimeClasspath
        org.apache.commons:commons-compress:1.25.0=runtimeClasspath
        commons-net:commons-net:3.8.0=runtimeClasspath
        org.json:json:20230227=runtimeClasspath
        org.apache.commons:commons-lang3:3.20.0=runtimeClasspath
    """,
}


def _write(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")


def _by_class(findings, class_code: str):
    return [finding for finding in findings if finding.class_code == class_code]


CHECKS: list[str] = []


def _check(condition: bool, message: str, failures: list[str]) -> None:
    CHECKS.append(message)
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="cg26-selftest-") as temp:
        root = Path(temp) / "project"
        root.mkdir()
        _write(root, SECRETS)
        _write(root, SAST)
        _write(root, SCA)

        result = run(root, DATABASE)
        findings = result.findings
        submission_text = json.dumps(result.to_submission(), ensure_ascii=False)
        full_text = json.dumps(result.to_full(), ensure_ascii=False)

        passwords = _by_class(findings, "password")
        api_keys = _by_class(findings, "api-key")
        fallbacks = _by_class(findings, "secret-fallback")
        recoverable = _by_class(findings, "recoverable-token")
        configuration = _by_class(findings, "configuration-token")

        _check(len(passwords) == 1, f"password: ожидалась 1 находка, получено {len(passwords)}", failures)
        _check(len(api_keys) == 1, f"api-key: ожидалась 1 находка, получено {len(api_keys)}", failures)
        _check(len(fallbacks) == 1, f"secret-fallback: ожидалась 1 находка, получено {len(fallbacks)}", failures)
        _check(len(recoverable) == 1, f"recoverable-token: ожидалась 1 находка, получено {len(recoverable)}", failures)
        _check(not configuration, "configuration-token: не должно быть находок в синтетическом проекте", failures)
        _check(
            all("Placeholders.kt" not in finding.path for finding in findings),
            "плейсхолдеры YOUR_API_KEY/changeme не должны давать находок",
            failures,
        )
        _check(
            "SYNTHETIC_" not in submission_text and "SYNTHETIC_" not in full_text,
            "значения секретов не должны попадать в отчёт",
            failures,
        )

        sql = _by_class(findings, "sql-injection")
        tls = _by_class(findings, "tls-verification")
        cors = _by_class(findings, "credentialed-cors")
        route = _by_class(findings, "route-authorization")
        object_auth = _by_class(findings, "object-authorization")

        _check(len(sql) == 1 and "A.kt" in sql[0].path, "sql-injection: ровно один небезопасный запрос", failures)
        _check(len(tls) == 1 and "B.kt" in tls[0].path, "tls-verification: только клиент с отключённой проверкой", failures)
        _check(len(cors) == 1 and "C.kt" in cors[0].path, "credentialed-cors: только wildcard + credentials", failures)
        _check(len(route) == 1 and "D.kt" in route[0].path, "route-authorization: только публичный экспорт", failures)
        _check(
            len(object_auth) == 1 and "F.kt" in object_auth[0].path,
            "object-authorization: только чтение без owner-предиката",
            failures,
        )

        sca = [finding for finding in findings if finding.category == "SCA"]
        artifacts = {finding.artifact for finding in sca}
        _check("commons-compress" in artifacts, "SCA: локальная константа версии должна разрешаться", failures)
        _check("commons-net" in artifacts, "SCA: многострочный add(...) должен разрешаться", failures)
        _check("json" in artifacts, "SCA: Gradle property должна разрешаться", failures)
        _check("commons-text" not in artifacts, "SCA: версия из lock-файла имеет приоритет", failures)
        _check("commons-lang3" not in artifacts, "SCA: версия выше границы не должна попадать в отчёт", failures)
        _check(
            any(diagnostic.code == "sca.unknown-version" for diagnostic in result.diagnostics),
            "SCA: неизвестная версия должна давать диагностику, а не находку",
            failures,
        )

        # Renames must not change results: copy one controller under a different
        # name and compare the class/count signature.
        renamed_root = Path(temp) / "renamed"
        renamed_root.mkdir()
        renamed = dict(SAST)
        renamed["src/main/kotlin/lab/selftest/Renamed.kt"] = renamed.pop("src/main/kotlin/lab/selftest/A.kt")
        _write(renamed_root, renamed)
        renamed_result = run(renamed_root, DATABASE)
        renamed_sql = _by_class(renamed_result.findings, "sql-injection")
        _check(len(renamed_sql) == 1, "переименование файла не должно менять результат sql-injection", failures)

        # Contract export: identifiers, coordinates, fingerprints and redaction.
        project = Project(root)
        scan_result = run(root, DATABASE, project=project)
        bundle = build_bundle(root, project, scan_result, DATABASE)
        bundle_text = json.dumps(bundle, ensure_ascii=False)

        categories = [item["category"] for item in bundle["scanResults"]]
        _check(categories == ["SECRET", "SCA", "SAST"], "bundle: три отдельных результата семейств", failures)
        _check(
            "SYNTHETIC_" not in bundle_text and "A1b2C3d4E5f6" not in bundle_text,
            "bundle: значения секретов не должны попадать в выгрузку",
            failures,
        )

        def recompute(finding: dict) -> str:
            anchor = finding["anchor"]
            payload = [
                "fp/v1",
                finding["category"],
                finding["ruleId"],
                anchor["normalizedPath"],
                anchor["symbolKey"],
                anchor["normalizedContextHash"],
                anchor["role"],
                anchor["secretHmac"],
            ]
            return hashlib.sha256(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ).hexdigest()

        contract_findings = [finding for item in bundle["scanResults"] for finding in item["findings"]]
        _check(
            all(finding["fingerprint"] == recompute(finding) for finding in contract_findings),
            "bundle: fingerprint fp/v1 пересчитывается независимо",
            failures,
        )
        secret_findings = [item for item in contract_findings if item["category"] == "SECRET"]
        other_findings = [item for item in contract_findings if item["category"] != "SECRET"]
        _check(
            bool(secret_findings)
            and all(item["anchor"]["secretHmac"] for item in secret_findings)
            and all(item["anchor"]["secretHmac"] is None for item in other_findings),
            "bundle: HMAC только у SECRET",
            failures,
        )

        mismatched = []
        for item in bundle["scanResults"]:
            for evidence in item["evidence"]:
                location = evidence["location"]
                text = (root / location["path"]).read_bytes().decode("utf-8")
                lines = text.split("\n")
                start = sum(len(line) + 1 for line in lines[: location["line"] - 1])
                row = lines[location["endLine"] - 1]
                if row.endswith("\r"):
                    row = row[:-1]
                end = sum(len(line) + 1 for line in lines[: location["endLine"] - 1]) + len(row)
                if (
                    location["byteStart"] != len(text[:start].encode("utf-8"))
                    or location["byteEnd"] != len(text[:end].encode("utf-8"))
                ):
                    mismatched.append(evidence["id"])
        _check(not mismatched, f"bundle: координаты bytes/line расходятся ({len(mismatched)})", failures)

    if failures:
        print("SELFTEST FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print(f"SELFTEST PASSED: {len(CHECKS)} проверок")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
