import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
        if not root_path.exists():
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
        if catalog_path.exists():
            files_scanned += 1
            try:
                lines = catalog_path.read_text(encoding="utf-8").splitlines()
                lines_of_code += len(lines)
                current_section = ""
                for line in lines:
                    line_clean = line.strip()
                    if line_clean.startswith("[") and line_clean.endswith("]"):
                        current_section = line_clean[1:-1].strip()
                        continue
                    if current_section == "versions" and "=" in line_clean:
                        parts = line_clean.split("=", 1)
                        v_key = parts[0].strip()
                        v_val = parts[1].strip().strip('"\'')
                        version_catalog_versions[v_key] = v_val
                    elif current_section == "libraries" and "=" in line_clean:
                        parts = line_clean.split("=", 1)
                        lib_alias = parts[0].strip()
                        lib_val = parts[1].strip()
                        # parse e.g. { module = "org.apache.logging.log4j:log4j-core", version.ref = "log4j" }
                        # or "group:name:version"
                        mod_match = re.search(r'module\s*=\s*["\']([^"\']+)["\']', lib_val)
                        ref_match = re.search(r'version\.ref\s*=\s*["\']([^"\']+)["\']', lib_val)
                        ver_match = re.search(r'version\s*=\s*["\']([^"\']+)["\']', lib_val)

                        if mod_match:
                            module = mod_match.group(1)
                            if ":" in module:
                                g, n = module.split(":", 1)
                                resolved_v = ""
                                if ref_match and ref_match.group(1) in version_catalog_versions:
                                    resolved_v = version_catalog_versions[ref_match.group(1)]
                                elif ver_match:
                                    resolved_v = ver_match.group(1)
                                if resolved_v:
                                    version_catalog_libs[lib_alias] = (g, n, resolved_v)
            except Exception as e:
                diagnostics.append(Diagnostic(
                    level="warning",
                    message=f"Ошибка парсинга libs.versions.toml: {str(e)}",
                    source="ScaAnalyzer"
                ))

        # 2. Parse all *.gradle.kts and *.gradle files
        dep_pattern_single = re.compile(
            r"""(?:implementation|api|compileOnly|runtimeOnly|testImplementation)\s*\(\s*["']([^:"']+):([^:"']+):([^:"']+)["']\s*\)"""
        )
        dep_pattern_args = re.compile(
            r"""(?:implementation|api|compileOnly|runtimeOnly|testImplementation)\s*\(\s*group\s*=\s*["']([^"']+)["']\s*,\s*name\s*=\s*["']([^"']+)["']\s*,\s*version\s*=\s*["']([^"']+)["']\s*\)"""
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
                files_scanned += 1

                try:
                    raw_lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
                    lines_of_code += len(raw_lines)

                    for line_idx, line in enumerate(raw_lines):
                        line_num = line_idx + 1

                        # Single string dependency: "group:name:version"
                        for match in dep_pattern_single.finditer(line):
                            group, name, version = match.groups()
                            self._check_dependency(
                                group=group, name=name, version=version,
                                provenance="requested_exact",
                                rel_path=rel_path, line_num=line_num, line=line,
                                match=match, findings=findings
                            )

                        # Named arguments dependency: group = "...", name = "...", version = "..."
                        for match in dep_pattern_args.finditer(line):
                            group, name, version = match.groups()
                            self._check_dependency(
                                group=group, name=name, version=version,
                                provenance="requested_exact",
                                rel_path=rel_path, line_num=line_num, line=line,
                                match=match, findings=findings
                            )

                        # Version catalog alias: libs.some.lib
                        for match in dep_pattern_catalog.finditer(line):
                            alias = match.group(1).replace(".", "_").replace("-", "_")
                            if alias in version_catalog_libs:
                                group, name, version = version_catalog_libs[alias]
                                self._check_dependency(
                                    group=group, name=name, version=version,
                                    provenance="catalog_recorded",
                                    rel_path=rel_path, line_num=line_num, line=line,
                                    match=match, findings=findings
                                )
                            else:
                                unresolved_deps += 1
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
                          rel_path: str, line_num: int, line: str, match: Any,
                          findings: List[Finding]) -> None:
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
                fingerprint = hashlib.sha256(f"sca:{coord}:{version}:{cve_id}".encode("utf-8")).hexdigest()[:16]
                finding_id = f"fnd_sca_{fingerprint}"

                col_start = match.start() + 1
                col_end = match.end() + 1
                location = Location(
                    path=rel_path,
                    startLine=line_num,
                    endLine=line_num,
                    startColumn=col_start,
                    endColumn=col_end,
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
                    confidence=Confidence.CONFIRMED,
                    confidenceBasis=f"Версия явно объявлена в файле сборки (provenance: {provenance})",
                    status=FindingStatus.CONFIRMED,
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
                                f"{', '.join(fixed_versions) if fixed_versions else 'latest'}."
                                + (f" Источники: {', '.join(sources)}." if sources else "")
                            ),
                            location=None
                        )
                    ],
                    redactedEvidence=line.strip(),
                    advisoryIds=advisory_ids,
                    coverageWarnings=[]
                )
                findings.append(finding)
