import hashlib
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import tree_sitter
import tree_sitter_kotlin
from analyzers.cg26.kotlin_lexer import lex
from backend.app.analyzers.base import BaseAnalyzer
from backend.app.analyzers.file_discovery import prune_scan_dirs
from backend.app.contracts.schemas import (
    Confidence, Coverage, Diagnostic, Finding, FindingStatus,
    Location, RuleObservation, ScanResult, Severity, TraceStep
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
        if not root_path.is_dir():
            return ScanResult(
                analyzer=self.name,
                findings=[],
                diagnostics=[Diagnostic(level="error", message=f"Path not found: {artifact_path}", source="SastAnalyzer")],
                duration_ms=0.0
            )

        rule_observations: List[RuleObservation] = []

        for root, dirnames, files in os.walk(root_path):
            prune_scan_dirs(dirnames)
            for file_name in files:
                if not file_name.endswith(".kt"):
                    continue

                file_path = Path(root) / file_name
                rel_path = file_path.relative_to(root_path).as_posix()
                if file_path.is_symlink():
                    parser_errors += 1
                    diagnostics.append(Diagnostic(level="warning", message=f"Символическая ссылка пропущена: {rel_path}", source="SastAnalyzer"))
                    continue
                files_scanned += 1
                sql_observation: Dict[str, Any] = {
                    "rule_id": "KOTLIN-SQL-INJECTION",
                    "rule_version": "1.0.0",
                    "path": rel_path,
                    "status": "not_applicable",
                    "invocations": 0,
                    "findings": 0,
                    "reason": "Правило не вызывалось: в файле нет пользовательского источника для SQL-правила.",
                }

                try:
                    raw_bytes = file_path.read_bytes()
                    source_text = raw_bytes.decode("utf-8")
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
                        sql_observation["status"] = "skipped"
                        sql_observation["reason"] = (
                            "AST содержит синтаксические ошибки; правило не считается checked."
                        )

                    # Run our taint-analysis and AST rules
                    before = len(findings)
                    self._check_file_ast(
                        rel_path, raw_bytes, lines, tree.root_node, findings,
                        sql_observation=sql_observation,
                    )
                    if tree.root_node.has_error:
                        for finding in findings[before:]:
                            finding.confidence = Confidence.POTENTIAL
                            finding.status = FindingStatus.POTENTIAL
                            finding.coverageWarnings.append("Kotlin AST содержит ошибки; результат требует ручной проверки")

                except Exception as e:
                    if sql_observation["status"] != "error":
                        sql_observation["status"] = "skipped"
                        sql_observation["reason"] = (
                            f"Файл не прочитан или анализ прерван: {type(e).__name__}"
                        )
                    parser_errors += 1
                    diagnostics.append(Diagnostic(
                        level="error",
                        message=f"Ошибка AST-анализа файла {rel_path}: {str(e)}",
                        source="SastAnalyzer",
                        details={"path": rel_path, "error": str(e)}
                    ))
                finally:
                    rule_observations.append(RuleObservation(**sql_observation))

        rule_observations.sort(key=lambda item: item.path)
        duration_ms = (time.time() - start_time) * 1000
        coverage = Coverage(
            files_scanned=files_scanned,
            files_skipped=0,
            lines_of_code=lines_of_code,
            rules_executed=rules_executed,
            parser_errors=parser_errors,
            unresolved_dependencies=0,
            rule_observations=rule_observations,
        )

        return ScanResult(
            analyzer=self.name,
            findings=findings,
            diagnostics=diagnostics,
            coverage=coverage,
            duration_ms=round(duration_ms, 2)
        )

    def _check_file_ast(self, rel_path: str, raw_bytes: bytes, lines: List[str],
                        root_node: Any, findings: List[Finding],
                        sql_observation: Optional[Dict[str, Any]] = None) -> None:
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
            self._analyze_function(
                rel_path, raw_bytes, lines, fn, findings,
                sql_observation=sql_observation,
            )

    def _analyze_function(self, rel_path: str, raw_bytes: bytes, lines: List[str],
                          fn_node: Any, findings: List[Finding],
                          sql_observation: Optional[Dict[str, Any]] = None) -> None:
        fn_bytes = raw_bytes[fn_node.start_byte:fn_node.end_byte]
        fn_text = fn_bytes.decode("utf-8", errors="replace")

        # Extract user input parameters (Sources)
        user_params = self._extract_user_sources(fn_node, raw_bytes)
        if not user_params:
            return

        # 1. SQL Injection check
        self._check_sql_injection(
            rel_path, lines, fn_node, fn_text, user_params, findings,
            observation=sql_observation,
        )

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
        modifiers = next((child for child in fn_node.children if child.type == "modifiers"), None)
        header = raw_bytes[modifiers.start_byte:modifiers.end_byte].decode("utf-8") if modifiers else ""
        is_spring_mapping = any(ann in header for ann in ["@GetMapping", "@PostMapping", "@PutMapping", "@DeleteMapping", "@PatchMapping", "@RequestMapping"])

        # Find all function_value_parameters
        containers = [child for child in fn_node.children if child.type == "function_value_parameters"]
        for c in containers:
            modifiers = ""
            for p in c.children:
                if p.type == "parameter_modifiers":
                    modifiers = raw_bytes[p.start_byte:p.end_byte].decode("utf-8", errors="replace")
                    continue
                if p.type not in {"parameter", "function_value_parameter"}:
                    continue
                id_nodes = []
                self._find_nodes_by_type(p, ["identifier", "simple_identifier"], id_nodes)
                if id_nodes:
                    name = raw_bytes[id_nodes[0].start_byte:id_nodes[0].end_byte].decode("utf-8", errors="replace")
                    if is_spring_mapping or any(ann in modifiers for ann in ["@RequestParam", "@PathVariable", "@RequestBody", "@RequestHeader"]):
                        ident = id_nodes[0]
                        line_start = raw_bytes.rfind(b"\n", 0, ident.start_byte) + 1
                        column = len(raw_bytes[line_start:ident.start_byte].decode("utf-8")) + 1
                        results.append((name, ident.start_point[0] + 1, column))
                modifiers = ""

        return results

    def _sink_calls(self, fn_node: Any, fn_text: str, sinks: List[str]):
        raw = fn_text.encode("utf-8")
        calls: List[Any] = []

        def visit(node: Any) -> None:
            if node != fn_node and node.type == "function_declaration":
                return
            if node.type == "call_expression":
                calls.append(node)
            for child in node.children:
                visit(child)

        visit(fn_node)
        for call in calls:
            arguments = next((child for child in call.children if child.type == "value_arguments"), None)
            if arguments is None:
                continue
            callee = raw[call.start_byte - fn_node.start_byte:arguments.start_byte - fn_node.start_byte].decode("utf-8").strip()
            sink = next((name for name in sinks if callee == name.rstrip("(") or callee.endswith("." + name.rstrip("("))), None)
            if sink is None:
                continue
            args = [raw[arg.start_byte - fn_node.start_byte:arg.end_byte - fn_node.start_byte].decode("utf-8")
                    for arg in arguments.children if arg.type == "value_argument"]
            yield sink, call, args

    @staticmethod
    def _argument_uses(argument: str, name: str, *, sql: bool = False) -> bool:
        tokens = lex(argument).tokens
        interpolated = any(
            re.search(r"(?<![\w])" + re.escape(name) + r"(?![\w])", part.expr)
            for token in tokens if token.kind == "STRING" for part in token.interpolations
        )
        direct = any(token.kind == "IDENT" and token.text == name for token in tokens)
        if sql:
            return interpolated or (direct and any(token.is_op("+") for token in tokens))
        return interpolated or direct

    @staticmethod
    def _sink_location(rel_path: str, lines: List[str], node: Any) -> Location:
        start_line, end_line = node.start_point[0] + 1, node.end_point[0] + 1
        start_column = len(lines[start_line - 1].encode("utf-8")[:node.start_point[1]].decode("utf-8")) + 1
        end_column = len(lines[end_line - 1].encode("utf-8")[:node.end_point[1]].decode("utf-8")) + 1
        start_byte = sum(len(line.encode("utf-8")) for line in lines[:start_line - 1]) + node.start_point[1]
        end_byte = sum(len(line.encode("utf-8")) for line in lines[:end_line - 1]) + node.end_point[1]
        return Location(path=rel_path, startLine=start_line, endLine=end_line,
                        startColumn=start_column, endColumn=end_column, byteRange=[start_byte, end_byte])

    def _check_sql_injection(self, rel_path: str, lines: List[str], fn_node: Any,
                             fn_text: str, user_params: List[Tuple[str, int, int]],
                             findings: List[Finding],
                             observation: Optional[Dict[str, Any]] = None) -> None:
        """Run KOTLIN-SQL-INJECTION and record its real completion per file."""
        before = len(findings)
        if observation is not None:
            observation["invocations"] += 1
        try:
            self._check_sql_injection_rules(
                rel_path, lines, fn_node, fn_text, user_params, findings,
            )
        except Exception as exc:
            if observation is not None:
                observation["status"] = "error"
                observation["reason"] = (
                    f"Ошибка правила KOTLIN-SQL-INJECTION: {type(exc).__name__}"
                )
            raise
        else:
            if observation is not None and observation["status"] != "skipped":
                observation["status"] = "checked"
                observation["reason"] = (
                    "Правило вызвано и завершилось без ошибки; "
                    f"findings={len(findings) - before}."
                )
        finally:
            if observation is not None:
                observation["findings"] += len(findings) - before

    def _check_sql_injection_rules(self, rel_path: str, lines: List[str], fn_node: Any,
                                   fn_text: str, user_params: List[Tuple[str, int, int]],
                                   findings: List[Finding]) -> None:
        sql_sinks = ["jdbcTemplate.query", "jdbcTemplate.execute", "jdbcTemplate.update",
                     "createNativeQuery", "executeQuery", "executeUpdate"]

        for sink, call, args in self._sink_calls(fn_node, fn_text, sql_sinks):
            if args:
                # Check if any user parameter is interpolated or concatenated into SQL
                for p_name, p_line, p_col in user_params:
                    # Look for string concatenation ("... " + p_name) or interpolation ("... $p_name ...")
                    if self._argument_uses(args[0], p_name, sql=True):
                        sink_line = call.start_point[0] + 1
                        evidence = "".join(lines[sink_line - 1:call.end_point[0] + 1]).strip()

                        fingerprint = hashlib.sha256(f"sast:sql:{rel_path}:{p_name}:{sink_line}".encode("utf-8")).hexdigest()[:16]
                        finding_id = f"fnd_sast_{fingerprint}"

                        loc_source = Location(
                            path=rel_path, startLine=p_line, endLine=p_line,
                            startColumn=p_col, endColumn=p_col + len(p_name)
                        )
                        loc_sink = self._sink_location(rel_path, lines, call)

                        finding = Finding(
                            id=finding_id,
                            fingerprint=fingerprint,
                            analyzer=self.name,
                            ruleId="KOTLIN-SQL-INJECTION",
                            ruleVersion="1.0.0",
                            severity=Severity.CRITICAL,
                            severityBasis="Пользовательский параметр конкатенируется или интерполируется напрямую в SQL-запрос",
                            confidence=Confidence.CONFIRMED,
                            confidenceBasis="Входной параметр непосредственно входит в текст SQL-аргумента вызова",
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
                            coverageWarnings=["Анализ ограничен прямой интерполяцией и конкатенацией в SQL-аргументе; межпроцедурный поток не проверен"]
                        )
                        findings.append(finding)
                        break

    def _check_path_traversal(self, rel_path: str, lines: List[str], fn_node: Any,
                              fn_text: str, user_params: List[Tuple[str, int, int]],
                              findings: List[Finding]) -> None:
        file_sinks = ["File(", "FileInputStream(", "FileOutputStream(", "Path.of(", "Paths.get(", "FileReader("]
        handled_params: Set[str] = set()

        for sink, call, args in self._sink_calls(fn_node, fn_text, file_sinks):
            if args:
                for p_name, p_line, p_col in user_params:
                    if p_name in handled_params:
                        continue
                    if any(self._argument_uses(argument, p_name) for argument in args):
                        handled_params.add(p_name)
                        sink_line = call.start_point[0] + 1
                        evidence = "".join(lines[sink_line - 1:call.end_point[0] + 1]).strip()

                        fingerprint = hashlib.sha256(f"sast:path:{rel_path}:{p_name}:{sink_line}".encode("utf-8")).hexdigest()[:16]
                        finding_id = f"fnd_sast_{fingerprint}"

                        loc_source = Location(
                            path=rel_path, startLine=p_line, endLine=p_line,
                            startColumn=p_col, endColumn=p_col + len(p_name)
                        )
                        loc_sink = self._sink_location(rel_path, lines, call)

                        finding = Finding(
                            id=finding_id,
                            fingerprint=fingerprint,
                            analyzer=self.name,
                            ruleId="KOTLIN-PATH-TRAVERSAL",
                            ruleVersion="1.0.0",
                            severity=Severity.HIGH,
                            severityBasis="Пользовательский параметр используется при формировании пути к файлу; безопасность границ директории требует проверки",
                            confidence=Confidence.POTENTIAL,
                            confidenceBasis="Параметр непосредственно передан в конструктор пути; корректность ограничений пути не доказана",
                            status=FindingStatus.POTENTIAL,
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
                                    description="Формирование пути из входного параметра; требуется проверка границ разрешённой директории",
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
                            coverageWarnings=["Границы директории и защитные проверки требуют ручного подтверждения"]
                        )
                        findings.append(finding)

    def _check_command_injection(self, rel_path: str, lines: List[str], fn_node: Any,
                                fn_text: str, user_params: List[Tuple[str, int, int]],
                                findings: List[Finding]) -> None:
        cmd_sinks = ["Runtime.getRuntime().exec", "ProcessBuilder("]
        for sink, call, args in self._sink_calls(fn_node, fn_text, cmd_sinks):
            if args:
                for p_name, p_line, p_col in user_params:
                    if any(self._argument_uses(argument, p_name) for argument in args):
                        sink_line = call.start_point[0] + 1
                        evidence = "".join(lines[sink_line - 1:call.end_point[0] + 1]).strip()

                        fingerprint = hashlib.sha256(f"sast:cmd:{rel_path}:{p_name}:{sink_line}".encode("utf-8")).hexdigest()[:16]
                        finding_id = f"fnd_sast_{fingerprint}"

                        loc_source = Location(path=rel_path, startLine=p_line, endLine=p_line, startColumn=p_col, endColumn=p_col + len(p_name))
                        loc_sink = self._sink_location(rel_path, lines, call)

                        finding = Finding(
                            id=finding_id,
                            fingerprint=fingerprint,
                            analyzer=self.name,
                            ruleId="KOTLIN-COMMAND-INJECTION",
                            ruleVersion="1.0.0",
                            severity=Severity.CRITICAL,
                            severityBasis="Внешний ввод пользователя передается в вызов системного процесса операционной системы",
                            confidence=Confidence.POTENTIAL,
                            confidenceBasis="Входной параметр непосредственно передан в аргумент процесса; эксплуатация зависит от запускаемой команды",
                            status=FindingStatus.POTENTIAL,
                            primaryLocation=loc_sink,
                            relatedLocations=[loc_source],
                            trace=[
                                TraceStep(step=1, description=f"Параметр '{p_name}' получен из внешнего источника", location=loc_source),
                                TraceStep(step=2, description="Передача ввода в аргументы запускаемого процесса", location=None),
                                TraceStep(step=3, description=f"Запуск процесса через {sink}", location=loc_sink)
                            ],
                            redactedEvidence=evidence,
                            advisoryIds=[],
                            coverageWarnings=["ProcessBuilder/exec не обязательно запускает оболочку; проверьте управление командой и её аргументами"]
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
        # Ownership may be enforced in a service outside this function.
        has_owner_check = "principal" in fn_text.lower() or "authentication" in fn_text.lower() or "currentuser" in fn_text.lower()

        if has_owner_check:
            return
        for sink, call, args in self._sink_calls(fn_node, fn_text, ["findById", "getById", "repository.get"]):
            for p_name, p_line, p_col in user_params:
                if "id" in p_name.lower() and any(self._argument_uses(argument, p_name) for argument in args):
                    sink_line = call.start_point[0] + 1
                    evidence = "".join(lines[sink_line - 1:call.end_point[0] + 1]).strip()

                    fingerprint = hashlib.sha256(f"sast:idor:{rel_path}:{p_name}:{sink_line}".encode("utf-8")).hexdigest()[:16]
                    finding_id = f"fnd_sast_{fingerprint}"

                    loc_source = Location(path=rel_path, startLine=p_line, endLine=p_line, startColumn=p_col, endColumn=p_col + len(p_name))
                    loc_sink = self._sink_location(rel_path, lines, call)

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
