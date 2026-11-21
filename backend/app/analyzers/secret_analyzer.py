import hashlib
import hmac
import os
import re
import time
from pathlib import Path
from typing import List, Tuple

from backend.app.analyzers.base import BaseAnalyzer
from backend.app.codefacts.source import mask_text
from backend.app.contracts.schemas import (
    Confidence, Coverage, Diagnostic, Finding, FindingStatus,
    Location, ScanResult, Severity, TraceStep
)

# Secret HMAC key for internal session hashing (prevents rainbow table attacks on short passwords)
HMAC_SECRET_KEY = b"cyber-garden-2026-secret-hmac-salt"

TEXT_EXTENSIONS = {
    ".kt", ".kts", ".java", ".properties", ".yaml", ".yml",
    ".json", ".env", ".xml", ".conf", ".sql", ".txt", ".md", ".toml",
    ".pem", ".key", ".crt", ".cer"
}

MAX_FILE_SIZE = 1 * 1024 * 1024  # 1 MB


def compute_secret_hmac(secret: str) -> str:
    return hmac.new(HMAC_SECRET_KEY, secret.encode("utf-8", errors="ignore"), hashlib.sha256).hexdigest()[:16]


class SecretAnalyzer(BaseAnalyzer):
    @property
    def name(self) -> str:
        return "secret"

    def __init__(self):
        # Compiled patterns
        self.rules = [
            (
                "SECRET-API-KEY",
                re.compile(r"""(?:\b[a-zA-Z0-9._-]*?(?:api[_-]?key|access[_-]?token|secret[_-]?key|client[_-]?secret))\s*[:=]\s*["']?([A-Za-z0-9_\-]{16,})["']?""", re.IGNORECASE),
                Severity.CRITICAL,
                "Обнаружен жестко зашитый API-ключ или Access Token в кодовой базе"
            ),
            (
                "SECRET-PASSWORD-LITERAL",
                re.compile(r"""(?:\b[a-zA-Z0-9._-]*?(?:password|passwd|db_pass|db_password))\s*[:=]\s*["']?([^"'\s]{5,})["']?""", re.IGNORECASE),
                Severity.HIGH,
                "Обнаружен пароль в виде строкового литерала в исходном коде или конфигурационном файле"
            ),
            (
                "SECRET-PRIVATE-KEY",
                re.compile(r"""(-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----)"""),
                Severity.CRITICAL,
                "В репозитории обнаружен приватный криптографический ключ (PEM-блок)"
            ),
            (
                "SECRET-URI-CREDENTIALS",
                re.compile(r"""(?:jdbc:[a-z]+|https?|postgres(?:ql)?|mysql|amqp|redis)://([^:\s'"]+):([^@\s'"]+)@""", re.IGNORECASE),
                Severity.CRITICAL,
                "URI подключения к сервису или базе данных содержит явные учетные данные (логин и пароль)"
            ),
            (
                "SECRET-BEARER-TOKEN",
                re.compile(r"""["']?Authorization["']?\s*[:=]\s*["']?Bearer\s+([A-Za-z0-9\-_=]{20,})["']?""", re.IGNORECASE),
                Severity.HIGH,
                "Обнаружен жестко зашитый Bearer токен авторизации в заголовках"
            ),
            (
                "SECRET-ENV-DEFAULT",
                re.compile(r"""\$\{[A-Za-z0-9_]+:([A-Za-z0-9_!@#$%^&*()+\-=]{6,})\}"""),
                Severity.MEDIUM,
                "В плейсхолдере переменной окружения ${VAR:default} задан секрет по умолчанию"
            )
        ]

    def analyze(self, artifact_path: str) -> ScanResult:
        start_time = time.time()
        findings: List[Finding] = []
        diagnostics: List[Diagnostic] = []
        files_scanned = 0
        files_skipped = 0
        lines_of_code = 0
        rules_executed = [r[0] for r in self.rules]

        root_path = Path(artifact_path)
        if not root_path.exists():
            return ScanResult(
                analyzer=self.name,
                findings=[],
                diagnostics=[Diagnostic(level="error", message=f"Path not found: {artifact_path}", source="SecretAnalyzer")],
                duration_ms=0.0
            )

        for root, _, files in os.walk(root_path):
            for file_name in files:
                file_path = Path(root) / file_name
                rel_path = file_path.relative_to(root_path).as_posix()

                # Skip non-text or oversized
                ext = file_path.suffix.lower()
                if ext not in TEXT_EXTENSIONS and not file_name.startswith(".env"):
                    files_skipped += 1
                    continue

                try:
                    size = file_path.stat().st_size
                    if size > MAX_FILE_SIZE:
                        files_skipped += 1
                        diagnostics.append(Diagnostic(
                            level="warning",
                            message=f"Файл {rel_path} пропущен (размер {size} > {MAX_FILE_SIZE})",
                            source="SecretAnalyzer",
                            details={"path": rel_path, "size": size}
                        ))
                        continue

                    raw_bytes = file_path.read_bytes()
                    # Decode as utf-8, replace bad chars
                    content = raw_bytes.decode("utf-8", errors="replace")
                    lines = content.splitlines(keepends=True)
                    files_scanned += 1
                    lines_of_code += len(lines)

                    # Scan line by line with byte offsets
                    current_byte_offset = 0
                    for line_idx, line in enumerate(lines):
                        line_num = line_idx + 1
                        line_bytes_len = len(line.encode("utf-8"))

                        for rule_id, pattern, severity, desc in self.rules:
                            matches = pattern.finditer(line)
                            for match in matches:
                                full_match_str = match.group(0)
                                # The secret exists only for the fingerprint; it never leaves this scope.
                                if rule_id == "SECRET-PRIVATE-KEY":
                                    raw_for_hmac = "PRIVATE_KEY_PEM"
                                elif rule_id == "SECRET-URI-CREDENTIALS":
                                    raw_for_hmac = full_match_str
                                else:
                                    raw_for_hmac = (
                                        match.group(1)
                                        if match.lastindex and match.lastindex >= 1
                                        else full_match_str
                                    )

                                # Mask the whole line, including every @ in userinfo.
                                redacted_evidence = mask_text(line.strip())

                                col_start = match.start() + 1
                                col_end = match.end() + 1
                                byte_start = current_byte_offset + len(line[:match.start()].encode("utf-8"))
                                byte_end = current_byte_offset + len(line[:match.end()].encode("utf-8"))

                                secret_hmac_id = compute_secret_hmac(raw_for_hmac)
                                fingerprint = hashlib.sha256(f"{rule_id}:{rel_path}:{secret_hmac_id}".encode("utf-8")).hexdigest()[:16]
                                finding_id = f"fnd_sec_{fingerprint}"

                                location = Location(
                                    path=rel_path,
                                    startLine=line_num,
                                    endLine=line_num,
                                    startColumn=col_start,
                                    endColumn=col_end,
                                    byteRange=[byte_start, byte_end]
                                )

                                finding = Finding(
                                    id=finding_id,
                                    fingerprint=fingerprint,
                                    analyzer=self.name,
                                    ruleId=rule_id,
                                    ruleVersion="1.0.0",
                                    severity=severity,
                                    severityBasis=desc,
                                    confidence=Confidence.CONFIRMED,
                                    confidenceBasis="Прямое совпадение со специализированным шаблоном секрета с валидным литералом",
                                    status=FindingStatus.CONFIRMED,
                                    primaryLocation=location,
                                    relatedLocations=[],
                                    trace=[
                                        TraceStep(
                                            step=1,
                                            description=f"В файле {rel_path} обнаружен секрет по правилу {rule_id}",
                                            location=location
                                        )
                                    ],
                                    redactedEvidence=redacted_evidence,
                                    advisoryIds=[],
                                    coverageWarnings=[]
                                )
                                findings.append(finding)

                        current_byte_offset += line_bytes_len

                except Exception as e:
                    diagnostics.append(Diagnostic(
                        level="warning",
                        message=f"Ошибка чтения файла {rel_path}: {str(e)}",
                        source="SecretAnalyzer",
                        details={"path": rel_path, "error": str(e)}
                    ))

        duration_ms = (time.time() - start_time) * 1000
        coverage = Coverage(
            files_scanned=files_scanned,
            files_skipped=files_skipped,
            lines_of_code=lines_of_code,
            rules_executed=rules_executed,
            parser_errors=len(diagnostics),
            unresolved_dependencies=0
        )

        return ScanResult(
            analyzer=self.name,
            findings=findings,
            diagnostics=diagnostics,
            coverage=coverage,
            duration_ms=round(duration_ms, 2)
        )
