import bisect
import hashlib
import json
import os
import re
import time
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from analyzers.cg26.kotlin_lexer import lex
from backend.app.analyzers.base import BaseAnalyzer
from backend.app.analyzers.file_discovery import prune_scan_dirs
from backend.app.analyzers.version_comparator import matches_version_range
from backend.app.contracts.schemas import (
    Confidence, Coverage, Diagnostic, Finding, FindingStatus,
    Location, ScanResult, Severity, TraceStep
)

ADVISORIES_FILE = Path(__file__).resolve().parent / "advisories.json"


class ScaAnalyzer(BaseAnalyzer):
    @property
    def name(self) -> str:
        return "sca"

    def __init__(self, advisories_path: Optional[Path] = None):
        path = advisories_path or ADVISORIES_FILE
        if path.exists():
            self.advisories = json.loads(path.read_text(encoding="utf-8"))
        else:
            self.advisories = []

    def analyze(self, artifact_path: str) -> ScanResult:
        start_time = time.time()
        findings: List[Finding] = []
        diagnostics: List[Diagnostic] = []
        files_scanned = 0
        lines_of_code = 0
        unresolved_deps = 0

        root_path = Path(artifact_path)
        if not root_path.is_dir():
            return ScanResult(
                analyzer=self.name,
                findings=[],
                diagnostics=[Diagnostic(level="error", message=f"Path not found: {artifact_path}", source="ScaAnalyzer")],
                duration_ms=0.0
            )
        if not self.advisories:
            diagnostics.append(Diagnostic(
                level="error",
                message="Справочник уязвимостей пуст или недоступен; SCA-сопоставление не выполнялось",
                source="ScaAnalyzer",
                details={"code": "sca.database-missing"},
            ))

        # 1. Parse Version Catalog (gradle/libs.versions.toml) if present
        version_catalog_versions: Dict[str, str] = {}
        version_catalog_libs: Dict[str, Tuple[str, str, str]] = {}  # alias -> (group, name, version)

        catalog_path = root_path / "gradle" / "libs.versions.toml"
        if catalog_path.is_symlink():
            diagnostics.append(Diagnostic(level="warning", message="Символическая ссылка libs.versions.toml пропущена", source="ScaAnalyzer"))
        elif catalog_path.exists():
            files_scanned += 1
            try:
                lines = catalog_path.read_text(encoding="utf-8").splitlines()
                lines_of_code += len(lines)
                catalog = tomllib.loads("\n".join(lines))
                version_catalog_versions = catalog.get("versions", {})
                for alias, entry in catalog.get("libraries", {}).items():
                    normalized_alias = re.sub(r"[._-]", "_", alias)
                    if isinstance(entry, str):
                        parts = entry.split(":")
                        if len(parts) == 3:
                            version_catalog_libs[normalized_alias] = tuple(parts)
                        continue
                    if not isinstance(entry, dict):
                        continue
                    module = entry.get("module")
                    if not module and entry.get("group") and entry.get("name"):
                        module = f"{entry['group']}:{entry['name']}"
                    version = entry.get("version")
                    if isinstance(version, dict):
                        version = version_catalog_versions.get(version.get("ref"))
                    if isinstance(module, str) and module.count(":") == 1 and isinstance(version, str):
                        group, name = module.split(":")
                        version_catalog_libs[normalized_alias] = (group, name, version)
            except Exception as e:
                diagnostics.append(Diagnostic(
                    level="warning",
                    message=f"Ошибка парсинга libs.versions.toml: {str(e)}",
                    source="ScaAnalyzer"
                ))

        # 2. Parse all *.gradle.kts and *.gradle files
        # Both Kotlin DSL calls and Groovy declarations are valid Gradle input.
        dep_pattern_single = re.compile(
            r"""(?<![\w.])(?:implementation|api|compileOnly|runtimeOnly|testImplementation)\s*(?:\(\s*)?["']([^:"']+):([^:"']+):([^:"']+)["']"""
        )
        dep_pattern_args = re.compile(
            r"""(?<![\w.])(?:implementation|api|compileOnly|runtimeOnly|testImplementation)\s*(?:\(\s*)?group\s*[:=]\s*["']([^"']+)["']\s*,\s*name\s*[:=]\s*["']([^"']+)["']\s*,\s*version\s*[:=]\s*["']([^"']+)["']"""
        )
        dep_pattern_catalog = re.compile(
            r"""(?:implementation|api|compileOnly|runtimeOnly)\s*\(\s*libs\.([a-zA-Z0-9._]+)\s*\)"""
        )

        for root, dirnames, files in os.walk(root_path):
            prune_scan_dirs(dirnames)
            for file_name in files:
                if not (file_name.endswith(".gradle.kts") or file_name.endswith(".gradle")):
                    continue

                file_path = Path(root) / file_name
                rel_path = file_path.relative_to(root_path).as_posix()
                if file_path.is_symlink():
                    diagnostics.append(Diagnostic(level="warning", message=f"Символическая ссылка пропущена: {rel_path}", source="ScaAnalyzer"))
                    continue
                files_scanned += 1

                try:
                    content = file_path.read_text(encoding="utf-8")
                    raw_lines = content.splitlines(keepends=True)
                    code = list(content)
                    for token in lex(content).tokens:
                        if token.kind == "COMMENT":
                            code[token.start:token.end] = ["\n" if char == "\n" else " " for char in token.text]
                    code_text = "".join(code)
                    code_line_starts = [0]
                    for offset, char in enumerate(code_text):
                        if char == "\n":
                            code_line_starts.append(offset + 1)
                    lines_of_code += len(raw_lines)

                    def _location(offset: int) -> Tuple[int, int]:
                        index = bisect.bisect_right(code_line_starts, offset) - 1
                        return index, offset - code_line_starts[index]

                    def _emit(match: Any, group: str, name: str, version: str, provenance: str) -> None:
                        start_index, start_col = _location(match.start())
                        end_index, end_col = _location(match.end())
                        self._check_dependency(
                            group=group, name=name, version=version,
                            provenance=provenance,
                            rel_path=rel_path,
                            line_num=start_index + 1,
                            end_line=end_index + 1,
                            line=raw_lines[start_index] if start_index < len(raw_lines) else "",
                            start_col=start_col,
                            end_col=end_col,
                            findings=findings, diagnostics=diagnostics
                        )

                    # Match the comment-stripped text, not one physical line: Gradle
                    # declarations routinely span lines and must keep real coordinates.
                    for match in dep_pattern_single.finditer(code_text):
                        _emit(match, *match.groups(), "requested_exact")

                    # Named arguments dependency: group = "...", name = "...", version = "..."
                    for match in dep_pattern_args.finditer(code_text):
                        _emit(match, *match.groups(), "requested_exact")

                    # Version catalog alias: libs.some.lib
                    for match in dep_pattern_catalog.finditer(code_text):
                        alias = match.group(1).replace(".", "_").replace("-", "_")
                        if alias in version_catalog_libs:
                            group, name, version = version_catalog_libs[alias]
                            _emit(match, group, name, version, "catalog_recorded")
                        else:
                            unresolved_deps += 1
                            line_num = _location(match.start())[0] + 1
                            diagnostics.append(Diagnostic(
                                level="info",
                                message=f"Не удалось разрешить alias зависимости: libs.{match.group(1)} в {rel_path}:{line_num}",
                                source="ScaAnalyzer"
                            ))

                except Exception as e:
                    diagnostics.append(Diagnostic(
                        level="warning",
                        message=f"Ошибка разбора {rel_path}: {str(e)}",
                        source="ScaAnalyzer"
                    ))

        unresolved_deps += sum(1 for diagnostic in diagnostics if diagnostic.details and diagnostic.details.get("code") == "sca.unresolved-version")
        duration_ms = (time.time() - start_time) * 1000
        coverage = Coverage(
            files_scanned=files_scanned,
            files_skipped=0,
            lines_of_code=lines_of_code,
            rules_executed=["SCA-VULNERABLE-DEPENDENCY"],
            parser_errors=len(diagnostics),
            unresolved_dependencies=unresolved_deps
        )

        return ScanResult(
            analyzer=self.name,
            findings=findings,
            diagnostics=diagnostics,
            coverage=coverage,
            duration_ms=round(duration_ms, 2)
        )

    def _check_dependency(self, group: str, name: str, version: str, provenance: str,
                          rel_path: str, line_num: int, end_line: int, line: str,
                          start_col: int, end_col: int,
                          findings: List[Finding], diagnostics: List[Diagnostic]) -> None:
        if not re.fullmatch(r"[0-9]+(?:[.][0-9]+)*(?:[-.](?:alpha|beta|milestone|rc|cr|snapshot|final|ga|release|sp)[.-]?[0-9]*)?", version, re.IGNORECASE):
            diagnostics.append(Diagnostic(
                level="warning", message=f"Неразрешённая или неподдерживаемая версия зависимости в {rel_path}:{line_num}",
                source="ScaAnalyzer", details={"code": "sca.unresolved-version", "path": rel_path, "line": line_num},
            ))
            return
        coord = f"{group}:{name}"
        for adv in self.advisories:
            if adv.get("package") != coord:
                continue

            # Check if version matches any vulnerable range
            is_vulnerable = False
            for v_range in adv.get("vulnerable_ranges", []):
                if matches_version_range(version, v_range):
                    is_vulnerable = True
                    break

            if is_vulnerable:
                cve_id = adv.get("id", "CVE-UNKNOWN")
                advisory_ids = [cve_id, *[str(alias) for alias in adv.get("aliases", []) if str(alias)]]
                fixed_versions = [str(item) for item in adv.get("fixed_versions", []) if str(item)]
                if not fixed_versions and adv.get("fixed_version"):
                    fixed_versions = [str(adv["fixed_version"])]
                sources = [str(item) for item in adv.get("sources", []) if str(item)]
                fingerprint = hashlib.sha256(f"sca:{rel_path}:{line_num}:{start_col}:{coord}:{version}:{cve_id}".encode("utf-8")).hexdigest()[:16]
                finding_id = f"fnd_sca_{fingerprint}"

                location = Location(
                    path=rel_path,
                    startLine=line_num,
                    endLine=end_line,
                    startColumn=start_col + 1,
                    endColumn=end_col + 1,
                    byteRange=None
                )

                sev_str = adv.get("severity", "high").lower()
                severity_map = {
                    "critical": Severity.CRITICAL,
                    "high": Severity.HIGH,
                    "medium": Severity.MEDIUM,
                    "low": Severity.LOW,
                    "info": Severity.INFO
                }
                sev = severity_map.get(sev_str, Severity.HIGH)

                finding = Finding(
                    id=finding_id,
                    fingerprint=fingerprint,
                    analyzer=self.name,
                    ruleId="SCA-VULNERABLE-DEPENDENCY",
                    ruleVersion="1.0.0",
                    severity=sev,
                    severityBasis=f"Уязвимая зависимость {coord}:{version} подпадает под {cve_id} ({adv.get('title', '')})",
                    confidence=Confidence.POTENTIAL,
                    confidenceBasis=f"Запрошенная версия объявлена в файле сборки (provenance: {provenance}); resolved dependency graph не проверен",
                    status=FindingStatus.POTENTIAL,
                    primaryLocation=location,
                    relatedLocations=[],
                    trace=[
                        TraceStep(
                            step=1,
                            description=f"В файле сборки {rel_path} объявлена зависимость {coord}:{version}",
                            location=location
                        ),
                        TraceStep(
                            step=2,
                            description=(
                                f"Сопоставление со справочником уязвимостей: обнаружена известная уязвимость "
                                f"{', '.join(advisory_ids)}. Рекомендуемая безопасная версия: "
                                f"{', '.join(fixed_versions) if fixed_versions else 'не указана в справочнике'}."
                                + (f" Источники: {', '.join(sources)}." if sources else "")
                            ),
                            location=None
                        )
                    ],
                    redactedEvidence=line.strip(),
                    advisoryIds=advisory_ids,
                    coverageWarnings=["Статическое объявление не доказывает итоговую resolved-версию Gradle"]
                )
                findings.append(finding)
