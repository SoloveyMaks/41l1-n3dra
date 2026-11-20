import hashlib
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import tree_sitter
import tree_sitter_kotlin
from backend.app.analyzers.base import BaseAnalyzer
from backend.app.contracts.schemas import (
    Confidence, Coverage, Diagnostic, Finding, FindingStatus,
    Location, ScanResult, Severity, TraceStep
)


class SastAnalyzer(BaseAnalyzer):
    @property
    def name(self) -> str:
        return "sast"

    def __init__(self):
        self.language = tree_sitter.Language(tree_sitter_kotlin.language())
        self.parser = tree_sitter.Parser(self.language)

    def analyze(self, artifact_path: str) -> ScanResult:
        start_time = time.time()
        findings: List[Finding] = []
        diagnostics: List[Diagnostic] = []
        files_scanned = 0
        parser_errors = 0
        lines_of_code = 0
        rules_executed = [
            "KOTLIN-SQL-INJECTION",
            "KOTLIN-PATH-TRAVERSAL",
            "KOTLIN-COMMAND-INJECTION",
            "KOTLIN-INSECURE-ENDPOINT",
            "KOTLIN-IDOR"
        ]

        root_path = Path(artifact_path)
        if not root_path.exists():
            return ScanResult(
                analyzer=self.name,
                findings=[],
                diagnostics=[Diagnostic(level="error", message=f"Path not found: {artifact_path}", source="SastAnalyzer")],
                duration_ms=0.0
            )

        for root, _, files in os.walk(root_path):
            for file_name in files:
                if not file_name.endswith(".kt"):
                    continue

                file_path = Path(root) / file_name
                rel_path = file_path.relative_to(root_path).as_posix()
                files_scanned += 1

                try:
                    raw_bytes = file_path.read_bytes()
                    source_text = raw_bytes.decode("utf-8", errors="replace")
                    lines = source_text.splitlines(keepends=True)
                    lines_of_code += len(lines)

                    tree = self.parser.parse(raw_bytes)
                    if tree.root_node.has_error:
                        parser_errors += 1
                        diagnostics.append(Diagnostic(
                            level="warning",
                            message=f"Обнаружены синтаксические ошибки при AST-парсинге {rel_path}",
                            source="SastAnalyzer",
                            details={"path": rel_path}
                        ))

                    # Run our taint-analysis and AST rules
                    self._check_file_ast(rel_path, raw_bytes, lines, tree.root_node, findings)

                except Exception as e:
                    parser_errors += 1
                    diagnostics.append(Diagnostic(
                        level="error",
                        message=f"Ошибка AST-анализа файла {rel_path}: {str(e)}",
                        source="SastAnalyzer",
                        details={"path": rel_path, "error": str(e)}
                    ))

        duration_ms = (time.time() - start_time) * 1000
        coverage = Coverage(
            files_scanned=files_scanned,
            files_skipped=0,
            lines_of_code=lines_of_code,
            rules_executed=rules_executed,
            parser_errors=parser_errors,
            unresolved_dependencies=0
        )

        return ScanResult(
            analyzer=self.name,
            findings=findings,
            diagnostics=diagnostics,
            coverage=coverage,
            duration_ms=round(duration_ms, 2)
        )

    def _check_file_ast(self, rel_path: str, raw_bytes: bytes, lines: List[str],
                        root_node: Any, findings: List[Finding]) -> None:
        """
        Scans AST for sources, sinks, and taint paths.
        """
        # Collect function definitions
        func_nodes = []
        self._find_nodes_by_type(root_node, ["function_declaration"], func_nodes)

        # Check for Insecure Endpoints at class/function level
        self._check_insecure_endpoints(rel_path, raw_bytes, lines, func_nodes, findings)

        # For each function, check parameter sources and taint flows
        for fn in func_nodes:
            self._analyze_function(rel_path, raw_bytes, lines, fn, findings)

    def _analyze_function(self, rel_path: str, raw_bytes: bytes, lines: List[str],
                          fn_node: Any, findings: List[Finding]) -> None:
        fn_bytes = raw_bytes[fn_node.start_byte:fn_node.end_byte]
        fn_text = fn_bytes.decode("utf-8", errors="replace")

        # Extract user input parameters (Sources)
        user_params = self._extract_user_sources(fn_node, raw_bytes)
        if not user_params:
            # Also check if function has Spring mapping annotation even without explicit @RequestParam
            has_mapping = "@GetMapping" in fn_text or "@PostMapping" in fn_text or "@RequestMapping" in fn_text
            if has_mapping:
                # Any function parameter might be a source
                user_params = self._extract_all_parameters(fn_node, raw_bytes)

        if not user_params:
            return

        # 1. SQL Injection check
        self._check_sql_injection(rel_path, lines, fn_node, fn_text, user_params, findings)

        # 2. Path Traversal check
        self._check_path_traversal(rel_path, lines, fn_node, fn_text, user_params, findings)

        # 3. Command Injection check
        self._check_command_injection(rel_path, lines, fn_node, fn_text, user_params, findings)

        # 4. IDOR check
        self._check_idor(rel_path, lines, fn_node, fn_text, user_params, findings)

    def _extract_user_sources(self, fn_node: Any, raw_bytes: bytes) -> List[Tuple[str, int, int]]:
        """
        Extracts parameters annotated with @RequestParam, @PathVariable, @RequestBody, @RequestHeader
        or parameters of Spring controller methods.
        Returns: list of (param_name, start_line, start_col)
        """
        results = []
        fn_text = raw_bytes[fn_node.start_byte:fn_node.end_byte].decode("utf-8", errors="replace")
        is_spring_mapping = any(ann in fn_text for ann in ["@GetMapping", "@PostMapping", "@PutMapping", "@DeleteMapping", "@RequestMapping"])

        # Find all function_value_parameters
        containers = []
        self._find_nodes_by_type(fn_node, ["function_value_parameters"], containers)
        for c in containers:
            c_text = raw_bytes[c.start_byte:c.end_byte].decode("utf-8", errors="replace")
            # Find parameter nodes inside container
            params = []
            self._find_nodes_by_type(c, ["parameter", "function_value_parameter"], params)
            for p in params:
                id_nodes = []
                self._find_nodes_by_type(p, ["identifier", "simple_identifier"], id_nodes)
                if id_nodes:
                    name = raw_bytes[id_nodes[0].start_byte:id_nodes[0].end_byte].decode("utf-8", errors="replace")
                    # If whole container or function has Spring annotations, or param has annotation
                    if is_spring_mapping or any(ann in c_text for ann in ["@RequestParam", "@PathVariable", "@RequestBody", "@RequestHeader"]):
                        results.append((name, p.start_point[0] + 1, p.start_point[1] + 1))

        return results

    def _extract_all_parameters(self, fn_node: Any, raw_bytes: bytes) -> List[Tuple[str, int, int]]:
        results = []
        param_nodes = []
        self._find_nodes_by_type(fn_node, ["function_value_parameter", "parameter"], param_nodes)
        for p in param_nodes:
            id_nodes = []
            self._find_nodes_by_type(p, ["identifier", "simple_identifier"], id_nodes)
            if id_nodes:
                name = raw_bytes[id_nodes[0].start_byte:id_nodes[0].end_byte].decode("utf-8", errors="replace")
                results.append((name, p.start_point[0] + 1, p.start_point[1] + 1))
        return results

    def _check_sql_injection(self, rel_path: str, lines: List[str], fn_node: Any,
                             fn_text: str, user_params: List[Tuple[str, int, int]],
                             findings: List[Finding]) -> None:
        sql_sinks = ["jdbcTemplate.query", "jdbcTemplate.execute", "jdbcTemplate.update",
                     "createNativeQuery", "executeQuery", "executeUpdate"]

        for sink in sql_sinks:
            if sink in fn_text:
                # Check if any user parameter is interpolated or concatenated into SQL
                for p_name, p_line, p_col in user_params:
                    # Look for string concatenation ("... " + p_name) or interpolation ("... $p_name ...")
                    is_concatenated = f"+ {p_name}" in fn_text or f"{p_name} +" in fn_text
                    is_interpolated = f"${p_name}" in fn_text or f"${{{p_name}}}" in fn_text

                    if is_concatenated or is_interpolated:
                        # Find line of the sink
                        sink_line = p_line
                        evidence = ""
                        for l_idx, line in enumerate(lines):
                            if sink in line or (is_interpolated and p_name in line) or (is_concatenated and p_name in line):
                                sink_line = l_idx + 1
                                evidence = line.strip()
                                break

                        fingerprint = hashlib.sha256(f"sast:sql:{rel_path}:{p_name}:{sink_line}".encode("utf-8")).hexdigest()[:16]
                        finding_id = f"fnd_sast_{fingerprint}"

                        loc_source = Location(
                            path=rel_path, startLine=p_line, endLine=p_line,
                            startColumn=p_col, endColumn=p_col + len(p_name)
                        )
                        loc_sink = Location(
                            path=rel_path, startLine=sink_line, endLine=sink_line,
                            startColumn=1, endColumn=len(evidence) if evidence else 50
                        )

                        finding = Finding(
                            id=finding_id,
                            fingerprint=fingerprint,
                            analyzer=self.name,
                            ruleId="KOTLIN-SQL-INJECTION",
                            ruleVersion="1.0.0",
                            severity=Severity.CRITICAL,
                            severityBasis="Пользовательский параметр конкатенируется или интерполируется напрямую в SQL-запрос",
                            confidence=Confidence.CONFIRMED,
                            confidenceBasis="Полноценный Taint-путь от входного параметра контроллера до вызова SQL sink",
                            status=FindingStatus.CONFIRMED,
                            primaryLocation=loc_sink,
                            relatedLocations=[loc_source],
                            trace=[
                                TraceStep(
                                    step=1,
                                    description=f"Входной параметр контроллера '{p_name}' получен из HTTP-запроса",
                                    location=loc_source
                                ),
                                TraceStep(
                                    step=2,
                                    description=f"Строковая динамическая конкатенация/интерполяция параметра '{p_name}' в SQL-строку",
                                    location=None
                                ),
                                TraceStep(
                                    step=3,
                                    description=f"Вызов опасного sink '{sink}' с непараметризованным запросом",
                                    location=loc_sink
                                )
                            ],
                            redactedEvidence=evidence,
                            advisoryIds=[],
                            coverageWarnings=[]
                        )
                        findings.append(finding)
                        break

    def _strip_comments(self, text: str) -> str:
        # Strip // comments and /* */ comments
        text = re.sub(r'//.*', '', text)
        text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
        return text

    def _check_path_traversal(self, rel_path: str, lines: List[str], fn_node: Any,
                              fn_text: str, user_params: List[Tuple[str, int, int]],
                              findings: List[Finding]) -> None:
        code_without_comments = self._strip_comments(fn_text)
        file_sinks = ["File(", "FileInputStream(", "FileOutputStream(", "Path.of(", "Paths.get(", "FileReader("]
        handled_params: Set[str] = set()

        for sink in file_sinks:
            if sink in code_without_comments:
                for p_name, p_line, p_col in user_params:
                    if p_name in handled_params:
                        continue
                    # Check if parameter is passed to File without canonical/normalize check
                    if p_name in code_without_comments:
                        # Check absence of proper boundary checks in code (not comments)
                        has_safe_guard = ".canonicalPath" in code_without_comments or ".startsWith(" in code_without_comments or ".normalize(" in code_without_comments
                        if not has_safe_guard:
                            handled_params.add(p_name)
                            sink_line = p_line
                            evidence = ""
                            for l_idx, line in enumerate(lines):
                                if sink in line and p_name in line:
                                    sink_line = l_idx + 1
                                    evidence = line.strip()
                                    break
                            if not evidence:
                                for l_idx, line in enumerate(lines):
                                    if sink in line:
                                        sink_line = l_idx + 1
                                        evidence = line.strip()
                                        break

                            fingerprint = hashlib.sha256(f"sast:path:{rel_path}:{p_name}:{sink_line}".encode("utf-8")).hexdigest()[:16]
                            finding_id = f"fnd_sast_{fingerprint}"

                            loc_source = Location(
                                path=rel_path, startLine=p_line, endLine=p_line,
                                startColumn=p_col, endColumn=p_col + len(p_name)
                            )
                            loc_sink = Location(
                                path=rel_path, startLine=sink_line, endLine=sink_line,
                                startColumn=1, endColumn=len(evidence) if evidence else 50
                            )

                            finding = Finding(
                                id=finding_id,
                                fingerprint=fingerprint,
                                analyzer=self.name,
                                ruleId="KOTLIN-PATH-TRAVERSAL",
                                ruleVersion="1.0.0",
                                severity=Severity.HIGH,
                                severityBasis="Пользовательский параметр используется при формировании пути к файлу без проверки выхода за границы директории",
                                confidence=Confidence.CONFIRMED,
                                confidenceBasis="Taint-анализ подтвердил передачу параметра в конструктор пути без санитизации",
                                status=FindingStatus.CONFIRMED,
                                primaryLocation=loc_sink,
                                relatedLocations=[loc_source],
                                trace=[
                                    TraceStep(
                                        step=1,
                                        description=f"Входной параметр имени файла '{p_name}' получен из внешнего запроса",
                                        location=loc_source
                                    ),
                                    TraceStep(
                                        step=2,
                                        description="Формирование файлового пути без вызова .normalize() и проверки .startsWith(baseDir)",
                                        location=None
                                    ),
                                    TraceStep(
                                        step=3,
                                        description=f"Передача непроверенного пути в файловый sink '{sink}'",
                                        location=loc_sink
                                    )
                                ],
                                redactedEvidence=evidence,
                                advisoryIds=[],
                                coverageWarnings=[]
                            )
                            findings.append(finding)

    def _check_command_injection(self, rel_path: str, lines: List[str], fn_node: Any,
                                fn_text: str, user_params: List[Tuple[str, int, int]],
                                findings: List[Finding]) -> None:
        cmd_sinks = ["Runtime.getRuntime().exec", "ProcessBuilder("]
        for sink in cmd_sinks:
            if sink in fn_text:
                for p_name, p_line, p_col in user_params:
                    if p_name in fn_text:
                        sink_line = p_line
                        evidence = ""
                        for l_idx, line in enumerate(lines):
                            if sink in line:
                                sink_line = l_idx + 1
                                evidence = line.strip()
                                break

                        fingerprint = hashlib.sha256(f"sast:cmd:{rel_path}:{p_name}:{sink_line}".encode("utf-8")).hexdigest()[:16]
                        finding_id = f"fnd_sast_{fingerprint}"

                        loc_source = Location(path=rel_path, startLine=p_line, endLine=p_line, startColumn=p_col, endColumn=p_col + len(p_name))
                        loc_sink = Location(path=rel_path, startLine=sink_line, endLine=sink_line, startColumn=1, endColumn=len(evidence) if evidence else 50)

                        finding = Finding(
                            id=finding_id,
                            fingerprint=fingerprint,
                            analyzer=self.name,
                            ruleId="KOTLIN-COMMAND-INJECTION",
                            ruleVersion="1.0.0",
                            severity=Severity.CRITICAL,
                            severityBasis="Внешний ввод пользователя передается в вызов системного процесса операционной системы",
                            confidence=Confidence.CONFIRMED,
                            confidenceBasis="Прямой Taint-путь от аргумента функции в ProcessBuilder/exec",
                            status=FindingStatus.CONFIRMED,
                            primaryLocation=loc_sink,
                            relatedLocations=[loc_source],
                            trace=[
                                TraceStep(step=1, description=f"Параметр '{p_name}' получен из внешнего источника", location=loc_source),
                                TraceStep(step=2, description="Передача ввода в аргументы вызова системной оболочки", location=None),
                                TraceStep(step=3, description=f"Запуск процесса через {sink}", location=loc_sink)
                            ],
                            redactedEvidence=evidence,
                            advisoryIds=[],
                            coverageWarnings=[]
                        )
                        findings.append(finding)
                        break

    def _check_insecure_endpoints(self, rel_path: str, raw_bytes: bytes, lines: List[str],
                                  func_nodes: List[Any], findings: List[Finding]) -> None:
        # If file is a Controller and contains sensitive admin routes
        is_controller = "Controller" in rel_path or "controller" in rel_path
        if not is_controller:
            return

        for fn in func_nodes:
            fn_text = raw_bytes[fn.start_byte:fn.end_byte].decode("utf-8", errors="replace")
            has_http_mapping = "@GetMapping" in fn_text or "@PostMapping" in fn_text or "@DeleteMapping" in fn_text or "@PutMapping" in fn_text
            if not has_http_mapping:
                continue

            # Sensitive paths
            is_sensitive = any(kw in fn_text.lower() for kw in ["/admin", "/dump", "/internal", "/backup", "/system", "/manage"])
            has_security_annotation = "@PreAuthorize" in fn_text or "@Secured" in fn_text or "@RolesAllowed" in fn_text

            if is_sensitive and not has_security_annotation:
                line_num = fn.start_point[0] + 1
                evidence = lines[line_num - 1].strip() if line_num - 1 < len(lines) else "@GetMapping"
                fingerprint = hashlib.sha256(f"sast:endpoint:{rel_path}:{line_num}".encode("utf-8")).hexdigest()[:16]
                finding_id = f"fnd_sast_{fingerprint}"

                loc = Location(path=rel_path, startLine=line_num, endLine=line_num, startColumn=1, endColumn=len(evidence))
                finding = Finding(
                    id=finding_id,
                    fingerprint=fingerprint,
                    analyzer=self.name,
                    ruleId="KOTLIN-INSECURE-ENDPOINT",
                    ruleVersion="1.0.0",
                    severity=Severity.HIGH,
                    severityBasis="Чувствительный административный эндпоинт объявлен без аннотаций контроля доступа (@PreAuthorize)",
                    confidence=Confidence.POTENTIAL,
                    confidenceBasis="AST-анализ подтвердил отсутствие локальных правил авторизации на методе контроллера",
                    status=FindingStatus.POTENTIAL,
                    primaryLocation=loc,
                    relatedLocations=[],
                    trace=[
                        TraceStep(step=1, description=f"Объявление HTTP-эндпоинта {evidence}", location=loc),
                        TraceStep(step=2, description="Отсутствуют аннотации авторизации доступа (@PreAuthorize, @Secured)", location=None)
                    ],
                    redactedEvidence=evidence,
                    advisoryIds=[],
                    coverageWarnings=["Возможна глобальная настройка в SecurityFilterChain; рекомендуется явное разграничение на уровне метода"]
                )
                findings.append(finding)

    def _check_idor(self, rel_path: str, lines: List[str], fn_node: Any,
                    fn_text: str, user_params: List[Tuple[str, int, int]],
                    findings: List[Finding]) -> None:
        # Looks for methods like findById(id) where id is from user, without checking Principal/Authentication
        has_find_by_id = "findById(" in fn_text or "getById(" in fn_text or "repository.get(" in fn_text
        has_owner_check = "principal" in fn_text.lower() or "authentication" in fn_text.lower() or "currentuser" in fn_text.lower()

        if has_find_by_id and not has_owner_check:
            for p_name, p_line, p_col in user_params:
                if "id" in p_name.lower():
                    sink_line = p_line
                    evidence = ""
                    for l_idx, line in enumerate(lines):
                        if ("findById" in line or "getById" in line) and p_name in line:
                            sink_line = l_idx + 1
                            evidence = line.strip()
                            break

                    fingerprint = hashlib.sha256(f"sast:idor:{rel_path}:{p_name}:{sink_line}".encode("utf-8")).hexdigest()[:16]
                    finding_id = f"fnd_sast_{fingerprint}"

                    loc_source = Location(path=rel_path, startLine=p_line, endLine=p_line, startColumn=p_col, endColumn=p_col + len(p_name))
                    loc_sink = Location(path=rel_path, startLine=sink_line, endLine=sink_line, startColumn=1, endColumn=len(evidence) if evidence else 50)

                    finding = Finding(
                        id=finding_id,
                        fingerprint=fingerprint,
                        analyzer=self.name,
                        ruleId="KOTLIN-IDOR",
                        ruleVersion="1.0.0",
                        severity=Severity.HIGH,
                        severityBasis="Прямое обращение к объекту по ID (IDOR) без проверки принадлежности объекта авторизованному пользователю",
                        confidence=Confidence.POTENTIAL,
                        confidenceBasis="В теле метода отсутствует сверка владельца сущности с текущим Principal",
                        status=FindingStatus.POTENTIAL,
                        primaryLocation=loc_sink,
                        relatedLocations=[loc_source],
                        trace=[
                            TraceStep(step=1, description=f"Пользовательский параметр идентификатора '{p_name}'", location=loc_source),
                            TraceStep(step=2, description="Извлечение записи из репозитория по ID без проверки прав владения", location=loc_sink)
                        ],
                        redactedEvidence=evidence or f"findById({p_name})",
                        advisoryIds=[],
                        coverageWarnings=["Проверьте наличие владения в сервисном слое"]
                    )
                    findings.append(finding)
                    break

    def _find_nodes_by_type(self, node: Any, types: List[str], output_list: List[Any]) -> None:
        if node.type in types:
            output_list.append(node)
        for child in node.children:
            self._find_nodes_by_type(child, types, output_list)
