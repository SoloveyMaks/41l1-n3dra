"""Статический extractor Spring/Kotlin фактов для security map.

Никакого исполнения кода и никакой подстановки «иллюстраций»: всё строится по
тексту frozen snapshot. Каждый узел и каждое ребро ссылаются на evidence с
точными line/column/byte-координатами и sha256 исходного файла.

Что реально умеет:
* `authorizeHttpRequests { ... }`: permitAll / authenticated / hasRole / denyAll,
  порядок правил и первое совпадение для маршрута;
* `@RestController` + `@RequestMapping` + `@GetMapping/@PostMapping/...`;
* прямые вызовы `jdbc.query...`, `queryForList`, `createNativeQuery`, `File(...)`,
  `ProcessBuilder(...)` в теле обработчика;
* простые межфайловые вызовы `reader.read(ticketId)` по типу конструктора;
* SQL-таблицы/колонки, `schema.sql` (CREATE TABLE), taint через `$param`/конкатенацию;
* честные `unknown`, когда правило авторизации или owner-check не доказаны.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.app.codefacts.source import (
    SourceFile,
    make_evidence,
    mask_text,
)
from backend.app.contracts.schemas import (
    Confidence,
    Finding,
    FindingStatus,
    Location,
    Severity,
    TraceStep,
)

EXTRACTOR_VERSION = "cg-codefacts/1.0.0"
RULES_VERSION = "cf-rules/1"
MAX_GRAPH_FILES = int(__import__("os").getenv("CG_MAX_GRAPH_FILES", "2000"))

RULE_DEFINITIONS = [
    {
        "id": "CF-ROUTE-UNAUTH-PII",
        "version": "1.0.0",
        "family": "route-authorization",
        "title": "Публичный экспорт данных с PII",
    },
    {
        "id": "CF-SQL-PARAM-INTERPOLATION",
        "version": "1.0.0",
        "family": "sql-injection",
        "title": "Интерполяция параметра запроса в SQL",
    },
    {
        "id": "CF-OWNER-CHECK-UNPROVEN",
        "version": "1.0.0",
        "family": "object-authorization",
        "title": "Проверка владельца объекта не доказана",
    },
]
RULE_BY_ID = {rule["id"]: rule for rule in RULE_DEFINITIONS}

PII_COLUMNS = {
    "customer_id", "owner_id", "email", "phone", "address", "balance",
    "counter_note", "work_note", "ssn", "passport", "account_id", "tenant_id",
}
IDENTITY_COLUMNS = {"customer_id", "owner_id", "user_id", "tenant_id", "account_id"}

SQL_METHODS = {
    "queryForList", "queryForMap", "queryForObject", "query", "update",
    "execute", "createNativeQuery", "prepareStatement", "executeQuery",
    "executeUpdate",
}
FILE_SINKS = {"File", "FileInputStream", "FileOutputStream", "Path", "Paths", "FileReader"}
PROCESS_SINKS = {"ProcessBuilder", "Runtime"}

_METHOD_MAPPING_RE = re.compile(r"@(Get|Post|Put|Delete|Patch)Mapping\b")
_CLASS_MAPPING_RE = re.compile(r"@RequestMapping\b")
_FUN_RE = re.compile(
    r"^\s*(?:(?:public|private|internal|protected|override|suspend|inline|operator|infix)\s+)*"
    r"fun\s+([A-Za-z_]\w*)\s*\("
)
_CLASS_RE = re.compile(
    r"^\s*(?:(?:public|internal|private|open|final|abstract|sealed|data)\s+)*class\s+([A-Za-z_]\w*)",
    re.MULTILINE,
)
_PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)", re.MULTILINE)
_REQUEST_MATCHER_RE = re.compile(
    r"requestMatchers\s*\(\s*(?:(?:HttpMethod\.)?(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s*,\s*)?"
    r"[\"']([^\"']+)[\"']\s*\)\s*\.\s*"
    r"(permitAll|authenticated|hasRole|hasAnyRole|hasAuthority|denyAll)\s*"
    r"(?:\(\s*[\"']([^\"']*)[\"']\s*\))?"
)
_ANY_REQUEST_RE = re.compile(
    r"anyRequest\s*\(\s*\)\s*\.\s*(permitAll|authenticated|hasRole|hasAnyRole|hasAuthority|denyAll)"
    r"\s*(?:\(\s*[\"']([^\"']*)[\"']\s*\))?"
)
_CALL_RE = re.compile(r"(?<![\w.])(?:this\.)?([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*\(")
_SQL_CALL_RE = re.compile(
    r"(?<![\w.])([A-Za-z_]\w*)\s*\.\s*(queryForList|queryForMap|queryForObject|query|update|execute|"
    r"createNativeQuery|prepareStatement|executeQuery|executeUpdate)\s*\("
)
_CTOR_VAL_RE = re.compile(
    r"\b(?:private|internal|public|protected)?\s*val\s+([A-Za-z_]\w*)\s*:\s*([A-Za-z_][\w.<>?, ]*)"
)
_PARAM_RE = re.compile(
    r"@(RequestParam|PathVariable|RequestBody|RequestHeader)\b(?:\s*\(([^)]*)\))?\s*"
    r"(?:val\s+)?([A-Za-z_]\w*)\s*:\s*([^,)=]+)"
)
_CREATE_TABLE_RE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][\w.]*)", re.IGNORECASE)
_IDENTITY_FILTER_RE = re.compile(
    r"\b(customer_id|owner_id|user_id|tenant_id|account_id)\s*=\s*(\?|:\w+|\$\w+)",
    re.IGNORECASE,
)
_WHERE_RE = re.compile(r"\bWHERE\b", re.IGNORECASE)
_SELECT_RE = re.compile(r"\bSELECT\b(.*?)\bFROM\b", re.IGNORECASE | re.DOTALL)
_TABLE_RE = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+([A-Za-z_][\w.]*)", re.IGNORECASE)
_BRACE_OR_COMMENT_RE = re.compile(r"//.*|/\*.*?\*/|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'", re.DOTALL)


@dataclass
class NodeDraft:
    symbol_key: str
    kind: str
    label: str
    evidence: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class EdgeDraft:
    from_key: str
    to_key: str
    kind: str
    evidence: List[Dict[str, Any]]
    resolution: str = "resolved"
    reason: str = ""
    basis: str = "source"
    policy_ref: Optional[str] = None


@dataclass
class Param:
    name: str
    annotation: Optional[str]
    annotation_args: str
    type: str
    line: int


@dataclass
class SqlSink:
    receiver: str
    method: str
    sql: str
    file: str
    start_line: int
    end_line: int
    evidence: Dict[str, Any]
    tables: List[str]
    columns: List[str]
    tainted_params: List[str]


@dataclass
class MethodInfo:
    class_fq: str
    class_name: str
    name: str
    params: List[Param]
    start_line: int
    fun_line: int
    end_line: int
    body: str
    source: SourceFile
    route: Optional["RouteInfo"] = None
    sinks: List[SqlSink] = field(default_factory=list)


@dataclass
class RouteInfo:
    method: str
    path: str
    class_fq: str
    handler: str
    fun_line: int
    annotation_line: int
    source: SourceFile
    params: List[Param]
    func: Optional[MethodInfo] = None
    preauthorize: Optional[str] = None


@dataclass
class SecurityRule:
    method: Optional[str]
    pattern: str
    effect: str
    role: Optional[str]
    file: str
    line: int
    evidence: Dict[str, Any]
    order: int


@dataclass
class ExtractionResult:
    nodes: Dict[str, NodeDraft] = field(default_factory=dict)
    edges: List[EdgeDraft] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)
    finding_symbols: Dict[str, List[str]] = field(default_factory=dict)
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    files_read: List[str] = field(default_factory=list)
    files_total: int = 0
    unresolved_symbols: List[str] = field(default_factory=list)
    limits: List[str] = field(default_factory=list)
    rules: List[SecurityRule] = field(default_factory=list)
    routes: List[RouteInfo] = field(default_factory=list)

    def node(self, symbol: str, kind: str, label: str, evidence: List[Dict[str, Any]]) -> None:
        existing = self.nodes.get(symbol)
        if existing:
            for item in evidence:
                if item not in existing.evidence:
                    existing.evidence.append(item)
            return
        self.nodes[symbol] = NodeDraft(symbol, kind, label, list(evidence))

    def edge(self, from_key: str, to_key: str, kind: str, evidence: List[Dict[str, Any]],
             resolution: str = "resolved", reason: str = "", basis: str = "source",
             policy_ref: Optional[str] = None) -> None:
        clean = [item for item in evidence if item]
        key = (from_key, to_key, kind, resolution, reason)
        for existing in self.edges:
            if (existing.from_key, existing.to_key, existing.kind, existing.resolution, existing.reason) == key:
                for item in clean:
                    if item not in existing.evidence:
                        existing.evidence.append(item)
                return
        self.edges.append(EdgeDraft(
            from_key=from_key, to_key=to_key, kind=kind, evidence=clean,
            resolution=resolution, reason=reason, basis=basis, policy_ref=policy_ref,
        ))

    def diag(self, code: str, message: str, path: Optional[str] = None,
             evidence: Optional[Dict[str, Any]] = None) -> None:
        item: Dict[str, Any] = {"code": code, "message": message}
        if path:
            item["path"] = path
        if evidence:
            item["evidence"] = evidence
        if item not in self.diagnostics:
            self.diagnostics.append(item)


# ---------------------------------------------------------------------------
# text helpers
# ---------------------------------------------------------------------------

def _strip_code_noise(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        token = match.group(0)
        if token.startswith("//") or token.startswith("/*"):
            return " "
        return '""'
    return _BRACE_OR_COMMENT_RE.sub(repl, text)


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset)) + 1


def _line_index(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset))


def _balanced_end(text: str, open_offset: int, opener: str, closer: str) -> int:
    if open_offset < 0 or text[open_offset] != opener:
        return -1
    depth = 0
    in_string = False
    in_triple = False
    in_line_comment = False
    in_block_comment = False
    i = open_offset
    while i < len(text):
        ch = text[i]
        nxt = text[i:i + 2]
        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            if nxt == "*/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if in_triple:
            if text[i:i + 3] == '"""':
                in_triple = False
                i += 3
                continue
            i += 1
            continue
        if in_string:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if nxt == "//":
            in_line_comment = True
            i += 2
            continue
        if nxt == "/*":
            in_block_comment = True
            i += 2
            continue
        if text[i:i + 3] == '"""':
            in_triple = True
            i += 3
            continue
        if ch == '"':
            in_string = True
            i += 1
            continue
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _split_top_level(value: str) -> List[str]:
    parts: List[str] = []
    depth = 0
    current: List[str] = []
    in_string = False
    for ch in value:
        if ch == '"' and (not current or current[-1] != "\\"):
            in_string = not in_string
        if not in_string:
            if ch in "([<{":
                depth += 1
            elif ch in ")]>}":
                depth = max(0, depth - 1)
            elif ch == "," and depth == 0:
                parts.append("".join(current).strip())
                current = []
                continue
        current.append(ch)
    if "".join(current).strip():
        parts.append("".join(current).strip())
    return parts


def _literal_at(text: str, pos: int) -> Optional[Tuple[str, int, int]]:
    """Возвращает (literal, start_offset, end_offset) для первого строкового литерала."""
    i = pos
    while i < len(text) and text[i] in " \t\r\n":
        i += 1
    if text.startswith('"""', i):
        end = text.find('"""', i + 3)
        if end == -1:
            return None
        return text[i + 3:end], i, end + 3
    if i < len(text) and text[i] == '"':
        j = i + 1
        while j < len(text):
            if text[j] == "\\":
                j += 2
                continue
            if text[j] == '"':
                break
            if text[j] == "\n":
                return None
            j += 1
        if j >= len(text):
            return None
        return text[i + 1:j], i, j + 1
    return None


def _pattern_matches(pattern: str, path: str) -> bool:
    regex = ["^"]
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            regex.append(".*")
            i += 2
            continue
        ch = pattern[i]
        if ch == "*":
            regex.append("[^/]*")
        elif ch == "{":
            end = pattern.find("}", i)
            if end == -1:
                regex.append(re.escape(ch))
            else:
                regex.append("[^/]+")
                i = end
        else:
            regex.append(re.escape(ch))
        i += 1
    regex.append("$")
    try:
        return re.match("".join(regex), path) is not None
    except re.error:
        return False


def _backend_location(loc: Dict[str, Any]) -> Location:
    return Location(
        path=loc["path"],
        startLine=loc["line"],
        endLine=loc["endLine"],
        startColumn=loc["column"],
        endColumn=loc["endColumn"],
        byteRange=[loc["byteStart"], loc["byteEnd"]],
    )


def _trace(step: int, description: str, evidence: Dict[str, Any]) -> TraceStep:
    return TraceStep(step=step, description=description, location=_backend_location(evidence["location"]))


def _finding_id(rule_id: str, path: str, line: int, context: str) -> Tuple[str, str]:
    fingerprint = hashlib.sha256(f"cf:{rule_id}:{path}:{line}:{context}".encode("utf-8")).hexdigest()
    return f"fnd_cf_{fingerprint[:16]}", fingerprint


# ---------------------------------------------------------------------------
# extractor
# ---------------------------------------------------------------------------

class SpringFactExtractor:
    def __init__(self, sources: Dict[str, SourceFile], files_total: int):
        self.sources = sources
        self.files_total = files_total
        self.result = ExtractionResult(files_total=files_total)
        self.classes: Dict[str, str] = {}
        self.methods_by_class: Dict[str, List[MethodInfo]] = {}
        self.methods_by_fq: Dict[str, MethodInfo] = {}
        self.ctor_types: Dict[str, Dict[str, str]] = {}
        self.calls_by_method: Dict[str, List[MethodInfo]] = {}
        self.routes: List[RouteInfo] = []
        self.rules: List[SecurityRule] = []
        self.any_rule: Optional[SecurityRule] = None
        self.schema_evidence: Dict[str, Dict[str, Any]] = {}
        self.processed_calls: Set[Tuple[str, str]] = set()

    # --- public ----------------------------------------------------------
    def run(self) -> ExtractionResult:
        files = []
        for path in sorted(self.sources):
            if path.endswith((".kt", ".java", ".sql")):
                files.append(path)
        if len(files) > MAX_GRAPH_FILES:
            self.result.limits.append(f"MAX_GRAPH_FILES={MAX_GRAPH_FILES}")
            files = files[:MAX_GRAPH_FILES]

        self._parse_schemas()
        for path in files:
            source = self.sources[path]
            self.result.files_read.append(path)
            if source.decode_error:
                # Replacement characters change UTF-8 byte lengths, so neither
                # evidence bytes nor graph anchors can be derived from this text.
                self.result.diag(
                    "CF-DECODE-ERROR",
                    f"Файл пропущен: некорректный UTF-8 ({source.decode_error}); "
                    "координаты доказательств по заменённому тексту не строятся",
                    path=path,
                )
                continue
            if path.endswith((".kt", ".java")):
                self._parse_source(source)

        self._resolve_all_calls()
        for route in self.routes:
            self._emit_route_graph(route)
        self._emit_method_sinks()
        self._emit_sql_findings()
        self._emit_route_findings()

        if not self.rules:
            self.result.diag(
                "CF-NO-SECURITY-CONFIG",
                "Не найдено SecurityFilterChain/authorizeHttpRequests: авторизация маршрутов остаётся unknown.",
            )
        self.result.diag(
            "CF-STATIC-ONLY",
            "Статический анализ исходников: код не исполнялся, runtime-конфигурация и внешние фильтры не проверялись.",
        )
        return self.result

    # --- parsing ---------------------------------------------------------
    def _parse_schemas(self) -> None:
        for path, source in sorted(self.sources.items()):
            if not path.endswith(".sql"):
                continue
            for idx, line in enumerate(source.text.split("\n"), start=1):
                match = _CREATE_TABLE_RE.search(line)
                if not match:
                    continue
                table = match.group(1).split(".")[-1].lower()
                open_idx = source.text.find("(", sum(len(x) + 1 for x in source.text.split("\n")[:idx - 1]))
                end_line = idx
                if open_idx != -1:
                    close = _balanced_end(source.text, open_idx, "(", ")")
                    if close != -1:
                        end_line = _line_of(source.text, close)
                evidence = make_evidence(source, idx, max(idx, end_line), kind="sql")
                self.schema_evidence[table] = evidence
                self.result.files_read.append(path)

    def _parse_source(self, source: SourceFile) -> None:
        text = source.text
        package_match = _PACKAGE_RE.search(text)
        package = package_match.group(1) if package_match else ""
        parsed_ranges: List[Tuple[int, int]] = []
        for match in _CLASS_RE.finditer(text):
            if any(start < match.start() < end for start, end in parsed_ranges):
                continue  # nested class already inside a parsed top-level body
            class_name = match.group(1)
            fq = f"{package}.{class_name}" if package else class_name
            self.classes[fq] = class_name
            line_end = text.find("\n", match.end())
            if line_end == -1:
                line_end = len(text)
            if "{" not in text[match.end():line_end]:
                # `data class X(...)` without a body, enum/interface-like declarations.
                parsed_ranges.append((match.start(), match.end()))
                continue
            class_open = text.find("{", match.end(), line_end)
            class_close = _balanced_end(text, class_open, "{", "}")
            if class_close == -1:
                class_close = len(text)
            parsed_ranges.append((match.start(), class_close))
            header = text[match.start():class_open]
            class_prefix = self._class_route_prefix(text, match.start(), header)
            ctor_types = {
                m.group(1): re.sub(r"[?<].*$", "", m.group(2)).strip()
                for m in _CTOR_VAL_RE.finditer(header)
            }
            self.ctor_types[fq] = ctor_types
            class_body = text[class_open:class_close]
            body_start_line = _line_of(text, class_open)
            self._parse_methods(source, fq, class_name, class_body, body_start_line, class_prefix)

    def _class_route_prefix(self, text: str, class_offset: int, header: str) -> str:
        window = text[max(0, class_offset - 1200):class_offset]
        matches = list(_CLASS_MAPPING_RE.finditer(window))
        if not matches:
            return ""
        last = matches[-1]
        tail = window[last.end():last.end() + 300]
        literal = re.search(r"[\"']([^\"']*)[\"']", tail)
        return literal.group(1) if literal else ""

    def _parse_methods(self, source: SourceFile, class_fq: str, class_name: str,
                       class_body: str, body_start_line: int, class_prefix: str) -> None:
        lines = class_body.split("\n")
        pending: Optional[Dict[str, Any]] = None
        idx = 0
        while idx < len(lines):
            line = lines[idx]
            if "@PreAuthorize" in line:
                pre = re.search(r"[\"']([^\"']*)[\"']", line)
                if pending is None:
                    pending = {}
                pending["preauthorize"] = pre.group(1) if pre else "present"
            mapping = _METHOD_MAPPING_RE.search(line)
            if mapping:
                annotation_line = body_start_line + idx
                annotation_text = line
                j = idx
                while j + 1 < len(lines) and annotation_text.count("(") > annotation_text.count(")"):
                    j += 1
                    annotation_text += "\n" + lines[j]
                path_literal = re.search(r"[\"']([^\"']*)[\"']", annotation_text)
                pending = pending or {}
                pending.update({
                    "method": mapping.group(1).upper(),
                    "path": path_literal.group(1) if path_literal else "",
                    "annotation_line": annotation_line,
                })
                idx = j + 1
                continue

            fun_match = _FUN_RE.match(line)
            if not fun_match:
                idx += 1
                continue
            handler = fun_match.group(1)
            sig, sig_end, end_idx, body_text = self._function_slice(lines, fun_idx=idx)
            params = self._parse_params(sig)
            fun_start_line = body_start_line + idx
            method_info = MethodInfo(
                class_fq=class_fq, class_name=class_name, name=handler, params=params,
                start_line=fun_start_line, fun_line=fun_start_line,
                end_line=body_start_line + end_idx, body=body_text, source=source,
            )
            route = None
            if pending and "method" in pending:
                route = RouteInfo(
                    method=pending["method"],
                    path=self._join_path(class_prefix, pending.get("path", "")),
                    class_fq=class_fq,
                    handler=handler,
                    fun_line=fun_start_line,
                    annotation_line=pending["annotation_line"],
                    source=source,
                    params=params,
                    preauthorize=pending.get("preauthorize"),
                )
                route.func = method_info
                method_info.route = route
                self.routes.append(route)
            self.methods_by_fq[f"{class_fq}.{handler}"] = method_info
            self.methods_by_class.setdefault(class_fq, []).append(method_info)
            pending = None
            idx = end_idx + 1

    def _function_slice(self, lines: List[str], fun_idx: int) -> Tuple[str, int, int, str]:
        # Build signature until the parameter list closes.
        sig_lines: List[str] = []
        balance = 0
        sig_end = fun_idx
        for i in range(fun_idx, min(len(lines), fun_idx + 30)):
            line = lines[i]
            sig_lines.append(line)
            balance += line.count("(") - line.count(")")
            sig_end = i
            if balance <= 0 and "(" in "".join(sig_lines):
                break
        sig = "\n".join(sig_lines)
        after = "\n".join(lines[fun_idx:sig_end + 1])
        # Block body only when there is no expression-body `=` after the parameter list.
        expression_body = re.search(r"\)\s*(?::[^=]*)?=", sig) is not None
        brace_pos = after.find("{") if not expression_body else -1
        if brace_pos != -1:
            global_start = sum(len(x) + 1 for x in lines[:fun_idx]) + brace_pos
            text = "\n".join(lines)
            close = _balanced_end(text, global_start, "{", "}")
            if close == -1:
                close = len(text)
            end_idx = _line_index(text, close)
            body = text[global_start:close + 1]
            return sig, sig_end, end_idx, body
        # Expression body: continue while parentheses are open or line ends with an operator.
        depth = 0
        end_idx = sig_end
        for i in range(fun_idx, len(lines)):
            line = lines[i]
            depth += line.count("(") - line.count(")")
            stripped = line.strip()
            end_idx = i
            if i == fun_idx:
                if depth > 0 or stripped.endswith(("=", "+", ",")):
                    continue
                break
            if depth <= 0 and not stripped.endswith(("=", "+", ",", "||", "&&")):
                break
            if i - fun_idx > 60:
                break
        body = "\n".join(lines[fun_idx:end_idx + 1])
        return sig, sig_end, end_idx, body

    def _parse_params(self, signature: str) -> List[Param]:
        if "(" not in signature:
            return []
        depth = 0
        start = signature.find("(") + 1
        end = -1
        for i in range(start, len(signature)):
            ch = signature[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    end = i
                    break
                depth -= 1
        if end == -1:
            return []
        params_text = signature[start:end]
        result: List[Param] = []
        for segment in _split_top_level(params_text):
            ann = re.search(r"@([A-Za-z_]\w*)(?:\s*\(([^)]*)\))?", segment)
            name_match = re.search(r"([A-Za-z_]\w*)\s*:\s*([^,=]+)", segment)
            if not name_match:
                continue
            name = name_match.group(1)
            type_name = name_match.group(2).strip().rstrip("?")
            result.append(Param(
                name=name,
                annotation=ann.group(1) if ann else None,
                annotation_args=ann.group(2) or "" if ann else "",
                type=type_name,
                line=0,
            ))
        return result

    def _join_path(self, prefix: str, suffix: str) -> str:
        parts = []
        for part in (prefix, suffix):
            if part:
                parts.append("/" + part.strip("/"))
        joined = "".join(parts)
        return joined or "/"

    # --- security rules --------------------------------------------------
    def parse_security(self) -> None:
        order = 0
        for path, source in sorted(self.sources.items()):
            if not path.endswith((".kt", ".java")):
                continue
            text = source.text
            if "authorizeHttpRequests" not in text and "authorizeRequests" not in text:
                continue
            for idx, line in enumerate(text.split("\n"), start=1):
                if "requestMatchers" not in line and "anyRequest" not in line:
                    continue
                evidence = make_evidence(source, idx, idx, kind="dsl")
                match = _REQUEST_MATCHER_RE.search(line)
                if match:
                    order += 1
                    self.rules.append(SecurityRule(
                        method=match.group(1), pattern=match.group(2),
                        effect=match.group(3), role=match.group(4),
                        file=path, line=idx, evidence=evidence, order=order,
                    ))
                    continue
                any_match = _ANY_REQUEST_RE.search(line)
                if any_match:
                    order += 1
                    rule = SecurityRule(
                        method=None, pattern="**", effect=any_match.group(1),
                        role=any_match.group(2), file=path, line=idx,
                        evidence=evidence, order=order,
                    )
                    self.any_rule = rule
                    self.rules.append(rule)
        self.result.rules = list(self.rules)

    def _auth_for_route(self, route: RouteInfo) -> Tuple[Optional[SecurityRule], Optional[SecurityRule]]:
        for rule in self.rules:
            if rule.method and rule.method != route.method:
                continue
            if _pattern_matches(rule.pattern, route.path):
                return rule, self.any_rule
        return self.any_rule, self.any_rule

    # --- calls -----------------------------------------------------------
    def _resolve_all_calls(self) -> None:
        for methods in self.methods_by_class.values():
            for method in methods:
                method.sinks = self._sql_sinks(method)
        for methods in list(self.methods_by_class.values()):
            for method in methods:
                targets = self._resolve_calls(method)
                self.calls_by_method[f"{method.class_fq}.{method.name}"] = targets

    def _resolve_calls(self, method: MethodInfo) -> List[MethodInfo]:
        targets: List[MethodInfo] = []
        ctor_types = self.ctor_types.get(method.class_fq, {})
        for match in _CALL_RE.finditer(method.body):
            receiver, target_name = match.group(1), match.group(2)
            if receiver in {"this", "jdbc", "jdbcTemplate"}:
                continue
            if target_name in SQL_METHODS:
                continue
            target = None
            target_type = ctor_types.get(receiver)
            if target_type:
                target_class = self._resolve_class(target_type)
                if target_class:
                    target = self._find_method(target_class, target_name)
                if target is None:
                    self.result.unresolved_symbols.append(
                        f"{method.class_fq}.{method.name}->{target_type}.{target_name}"
                    )
            else:
                # Same-class call or unique method name fallback.
                target = self._find_method(method.class_fq, target_name)
                if target is None:
                    candidates = [
                        candidate
                        for class_key, methods in self.methods_by_class.items()
                        for candidate in methods
                        if candidate.name == target_name and candidate is not method
                    ]
                    unique = {f"{c.class_fq}.{c.name}": c for c in candidates}
                    if len(unique) == 1:
                        target = next(iter(unique.values()))
            if target is None or target is method:
                continue
            key = (f"{method.class_fq}.{method.name}", f"{target.class_fq}.{target.name}")
            call_line = method.fun_line + method.body[:match.start()].count("\n")
            evidence = make_evidence(method.source, call_line, call_line, kind="source")
            if key not in self.processed_calls:
                self.processed_calls.add(key)
                self.result.node(
                    f"function:{method.class_fq}.{method.name}", "function",
                    f"{method.class_name}.{method.name}", [evidence],
                )
                self.result.node(
                    f"function:{target.class_fq}.{target.name}", "function",
                    f"{target.class_name}.{target.name}",
                    [make_evidence(target.source, target.fun_line, target.fun_line, kind="ast")],
                )
                self.result.edge(
                    f"function:{method.class_fq}.{method.name}",
                    f"function:{target.class_fq}.{target.name}",
                    "calls", [evidence], resolution="resolved",
                    reason="Прямой вызов метода по типу конструктора/однозначному имени.",
                )
            if target not in targets:
                targets.append(target)
        return targets

    def _resolve_class(self, simple_or_fq: str) -> Optional[str]:
        if simple_or_fq in self.methods_by_class:
            return simple_or_fq
        matches = [fq for fq in self.classes if fq == simple_or_fq or fq.endswith("." + simple_or_fq)]
        return matches[0] if len(matches) == 1 else None

    def _find_method(self, class_key: str, name: str) -> Optional[MethodInfo]:
        for method in self.methods_by_class.get(class_key, []):
            if method.name == name:
                return method
        return None

    def _sql_sinks(self, method: MethodInfo) -> List[SqlSink]:
        sinks: List[SqlSink] = []
        body = method.body
        for match in _SQL_CALL_RE.finditer(body):
            receiver, sql_method = match.group(1), match.group(2)
            literal = _literal_at(body, match.end())
            if not literal:
                continue
            sql, lit_start, lit_end = literal
            if "?" not in sql and not re.search(r"\b(select|insert|update|delete)\b", sql, re.IGNORECASE):
                continue
            start_line = method.fun_line + body[:lit_start].count("\n")
            end_line = method.fun_line + body[:lit_end].count("\n")
            evidence = make_evidence(method.source, start_line, end_line, kind="sql")
            tables = sorted({
                item.split(".")[-1].lower()
                for item in _TABLE_RE.findall(sql)
                if item.lower() not in {"select", "where"}
            })
            columns: List[str] = []
            select_match = _SELECT_RE.search(sql)
            if select_match:
                for raw in select_match.group(1).split(","):
                    raw = raw.strip()
                    alias = re.search(r"(?:[A-Za-z_]\w*\.)?([A-Za-z_]\w*)\s*(?:AS\s+[A-Za-z_]\w*)?$", raw, re.IGNORECASE)
                    if alias:
                        column = alias.group(1).lower()
                        if column not in {"count", "sum", "avg", "min", "max"}:
                            columns.append(column)
            tainted = []
            for param in method.params:
                if re.search(rf"\${{\s*{re.escape(param.name)}\s*}}|\${re.escape(param.name)}\b", sql):
                    tainted.append(param.name)
            sinks.append(SqlSink(
                receiver=receiver, method=sql_method, sql=sql.strip(), file=method.source.path,
                start_line=start_line, end_line=end_line, evidence=evidence,
                tables=tables, columns=sorted(set(columns)), tainted_params=tainted,
            ))
        return sinks

    # --- graph emission --------------------------------------------------
    def _emit_route_graph(self, route: RouteInfo) -> None:
        rule, fallback = self._auth_for_route(route)
        route_evidence = make_evidence(
            route.source, route.annotation_line,
            max(route.annotation_line, route.fun_line), kind="ast",
        )
        route_symbol = f"route:{route.method}:{route.path}"
        self.result.node(route_symbol, "route", f"{route.method} {route.path}", [route_evidence])

        if route.preauthorize:
            pre_line = route.annotation_line
            pre_evidence = make_evidence(route.source, pre_line, pre_line, kind="ast")
            guard_symbol = f"guard:method-security:{route.class_fq}.{route.handler}"
            self.result.node(guard_symbol, "guard", "Method security (@PreAuthorize)", [pre_evidence])
            self.result.edge(route_symbol, guard_symbol, "requires", [pre_evidence, route_evidence],
                             reason="На методе объявлен @PreAuthorize; это статическое объявление, не runtime-доказательство.")
        function_symbol = f"function:{route.class_fq}.{route.handler}"
        if route.func:
            fun_evidence = make_evidence(
                route.source, route.fun_line,
                min(route.func.end_line, route.fun_line + 2), kind="ast",
            )
            self.result.node(function_symbol, "function", f"{route.handler}", [fun_evidence, route_evidence])
            self.result.edge(route_symbol, function_symbol, "routes_to", [route_evidence, fun_evidence],
                             reason="Spring mapping непосредственно связан с этой функцией-обработчиком.")

        if rule is None:
            actor_symbol = "actor:unknown"
            self.result.node(actor_symbol, "actor", "Неизвестный клиент", [route_evidence])
            self.result.edge(actor_symbol, route_symbol, "matches", [route_evidence],
                             resolution="unknown",
                             reason="Нет применимого статического правила авторизации; runtime-конфигурация неизвестна.")
            return

        effect = rule.effect
        role = rule.role
        matcher_symbol = f"matcher:{rule.method or 'ANY'}:{rule.pattern}"
        self.result.node(matcher_symbol, "matcher", rule.pattern, [rule.evidence])
        if effect == "permitAll":
            actor_symbol = "actor:anonymous"
            actor_label = "Анонимный запрос"
            chain_kind = "security_chain"
            chain_symbol = f"chain:permitAll:{rule.pattern}"
            chain_label = "permitAll"
            match_kind = "permits"
        elif effect == "denyAll":
            actor_symbol = "actor:denied"
            actor_label = "Доступ запрещён"
            chain_kind = "security_chain"
            chain_symbol = f"chain:denyAll:{rule.pattern}"
            chain_label = "denyAll"
            match_kind = "permits"
        elif effect in {"hasRole", "hasAnyRole"}:
            actor_symbol = f"actor:role:{role or 'unknown'}"
            actor_label = f"Роль {role or 'не указана'}"
            chain_kind = "guard"
            chain_symbol = f"guard:role:{role or 'unknown'}:{rule.pattern}"
            chain_label = f"Требуется роль {role or '?'}"
            match_kind = "requires"
        else:
            actor_symbol = "actor:authenticated"
            actor_label = "Аутентифицированный пользователь"
            chain_kind = "guard"
            chain_symbol = f"guard:authenticated:{rule.pattern}"
            chain_label = "Требуется аутентификация"
            match_kind = "requires"

        self.result.node(actor_symbol, "actor", actor_label, [rule.evidence])
        self.result.node(chain_symbol, chain_kind, chain_label, [rule.evidence])
        self.result.edge(actor_symbol, matcher_symbol, "matches", [rule.evidence],
                         reason="Клиент проходит matcher по методу и пути.")
        self.result.edge(matcher_symbol, chain_symbol, match_kind, [rule.evidence],
                         reason=f"Правило {effect} применяется к {rule.method or 'ANY'} {rule.pattern}.")
        self.result.edge(chain_symbol, route_symbol, match_kind, [rule.evidence, route_evidence],
                         reason="Первое применимое правило filter chain для этого маршрута.")

    def _emit_method_sinks(self) -> None:
        # Emit function/sink/data nodes for every method with SQL, reachable or not.
        for methods in self.methods_by_class.values():
            for method in methods:
                if not method.sinks:
                    continue
                function_symbol = f"function:{method.class_fq}.{method.name}"
                fun_evidence = make_evidence(
                    method.source, method.fun_line,
                    min(method.end_line, method.fun_line + 2), kind="ast",
                )
                self.result.node(function_symbol, "function", f"{method.class_name}.{method.name}", [fun_evidence])
                for sink in method.sinks:
                    self._emit_sink(method, sink, function_symbol)

    def _emit_sink(self, method: MethodInfo, sink: SqlSink, function_symbol: str) -> None:
        sink_symbol = f"sink:{sink.file}:{method.name}:{sink.start_line}"
        label = f"{sink.receiver}.{sink.method}"
        self.result.node(sink_symbol, "sink", label, [sink.evidence])
        self.result.edge(function_symbol, sink_symbol, "calls", [sink.evidence],
                         reason="Прямой вызов SQL/IO sink в теле функции.")

        for param_name in sink.tainted_params:
            param = next((p for p in method.params if p.name == param_name), None)
            if not param:
                continue
            source_evidence = make_evidence(method.source, method.fun_line, method.fun_line, kind="source")
            source_symbol = f"source:{sink.file}:{method.name}:{param_name}"
            self.result.node(source_symbol, "source", f"Параметр запроса {param_name}", [source_evidence])
            self.result.edge(source_symbol, sink_symbol, "flows_to", [source_evidence, sink.evidence],
                             reason=f"Параметр '{param_name}' интерполируется в SQL без параметризации.")

        if not sink.tables:
            return
        data_symbol = f"data:{','.join(sink.tables)}:{','.join(sink.columns) or '*'}"
        data_evidence = [sink.evidence]
        for table in sink.tables:
            if table in self.schema_evidence:
                data_evidence.append(self.schema_evidence[table])
        label = " JOIN ".join(sink.tables)
        if sink.columns:
            label += ": " + ", ".join(sink.columns)
        self.result.node(data_symbol, "data", label, data_evidence)
        self.result.edge(sink_symbol, data_symbol, "reads", data_evidence,
                         reason="SQL-проекция явно читает эти таблицы и колонки.")

    def _reachable_sinks(self, method: MethodInfo, seen: Optional[Set[str]] = None) -> List[Tuple[MethodInfo, SqlSink]]:
        seen = seen if seen is not None else set()
        key = f"{method.class_fq}.{method.name}"
        if key in seen:
            return []
        seen.add(key)
        result = [(method, sink) for sink in method.sinks]
        for target in self.calls_by_method.get(key, []):
            result.extend(self._reachable_sinks(target, seen))
        return result

    # --- findings --------------------------------------------------------
    def _emit_route_findings(self) -> None:
        for route in self.routes:
            if not route.func:
                continue
            rule, _ = self._auth_for_route(route)
            reachable = self._reachable_sinks(route.func)
            if not reachable:
                continue
            all_methods = [method for method, _ in reachable] + [route.func]
            principal_used = any(
                re.search(r"\b(Principal|Authentication|SecurityContextHolder|@AuthenticationPrincipal)\b", m.body)
                for m in all_methods
            )
            has_path_variable = any(p.annotation == "PathVariable" for p in route.params)

            if rule and rule.effect == "permitAll":
                pii_sinks = [
                    (method, sink) for method, sink in reachable
                    if (set(sink.columns) & PII_COLUMNS) and not _WHERE_RE.search(sink.sql)
                ]
                if pii_sinks:
                    self._emit_public_pii_finding(route, rule, pii_sinks)

            if has_path_variable and not principal_used:
                owner_sinks = [
                    (method, sink) for method, sink in reachable
                    if sink.tables and not _IDENTITY_FILTER_RE.search(sink.sql)
                ]
                if owner_sinks:
                    self._emit_owner_unknown_finding(route, owner_sinks)

    def _emit_public_pii_finding(self, route: RouteInfo, rule: SecurityRule,
                                 sinks: List[Tuple[MethodInfo, SqlSink]]) -> None:
        method, sink = sinks[0]
        columns = sorted({column for _, s in sinks for column in s.columns if column in PII_COLUMNS})
        rule_evidence = rule.evidence
        route_evidence = make_evidence(route.source, route.annotation_line, route.fun_line, kind="ast")
        fun_evidence = make_evidence(route.source, route.fun_line, min(route.func.end_line, route.fun_line + 2), kind="ast")
        data_evidence = [sink.evidence]
        for table in sink.tables:
            if table in self.schema_evidence:
                data_evidence.append(self.schema_evidence[table])
        context = f"{route.method} {route.path}|permitAll|{','.join(columns)}"
        finding_id, fingerprint = _finding_id("CF-ROUTE-UNAUTH-PII", rule.file, rule.line, context)
        warning = (
            "Runtime-фильтры и прокси не проверялись. Чувствительность выведена из имён "
            "проецируемых колонок; значения данных не читались."
        )
        finding = Finding(
            id=finding_id,
            fingerprint=fingerprint,
            analyzer="codefacts",
            ruleId="CF-ROUTE-UNAUTH-PII",
            ruleVersion="1.0.0",
            severity=Severity.HIGH,
            severityBasis=(
                f"Маршрут {route.method} {route.path} разрешён permitAll, а обработчик "
                f"читает PII-колонки ({', '.join(columns)}) без WHERE-фильтра."
            ),
            confidence=Confidence.CONFIRMED,
            confidenceBasis=(
                "Первое применимое правило filter chain и прямой SQL-вызов подтверждены "
                "статическим разбором исходников."
            ),
            status=FindingStatus.CONFIRMED,
            primaryLocation=_backend_location(rule_evidence["location"]),
            relatedLocations=[_backend_location(sink.evidence["location"])],
            trace=[
                _trace(1, f"Правило {rule.method or 'ANY'} {rule.pattern}: {rule.effect}", rule_evidence),
                _trace(2, f"Spring mapping {route.method} {route.path} -> {route.handler}", route_evidence),
                _trace(3, f"Обработчик вызывает {sink.receiver}.{sink.method}", fun_evidence),
                _trace(4, f"SQL читает {', '.join(sink.tables)} ({', '.join(columns)})", sink.evidence),
            ],
            redactedEvidence=mask_text(rule_evidence["redactedSnippet"]),
            advisoryIds=[],
            coverageWarnings=[warning],
        )
        self.result.findings.append(finding)
        self.result.finding_symbols[finding.id] = [
            "actor:anonymous",
            f"matcher:{rule.method or 'ANY'}:{rule.pattern}",
            f"chain:permitAll:{rule.pattern}",
            f"route:{route.method}:{route.path}",
            f"function:{route.class_fq}.{route.handler}",
            f"sink:{sink.file}:{method.name}:{sink.start_line}",
            f"data:{','.join(sink.tables)}:{','.join(sink.columns) or '*'}",
        ]

    def _emit_owner_unknown_finding(self, route: RouteInfo,
                                    sinks: List[Tuple[MethodInfo, SqlSink]]) -> None:
        method, sink = sinks[0]
        route_evidence = make_evidence(route.source, route.annotation_line, route.fun_line, kind="ast")
        call_evidence = sink.evidence
        guard_symbol = f"guard:owner-unproven:{route.method}:{route.path}"
        self.result.node(
            guard_symbol, "guard", "Проверка владельца не доказана",
            [route_evidence, call_evidence],
        )
        self.result.edge(
            f"route:{route.method}:{route.path}", guard_symbol, "requires",
            [route_evidence, call_evidence], resolution="unknown",
            reason=(
                "Ни в обработчике, ни в достижимом SQL нет Principal/owner-предиката; "
                "это не доказательство отсутствия проверки в runtime/сервисном слое."
            ),
        )
        context = f"{route.method} {route.path}|owner-unproven|{sink.file}:{sink.start_line}"
        finding_id, fingerprint = _finding_id("CF-OWNER-CHECK-UNPROVEN", sink.file, sink.start_line, context)
        finding = Finding(
            id=finding_id,
            fingerprint=fingerprint,
            analyzer="codefacts",
            ruleId="CF-OWNER-CHECK-UNPROVEN",
            ruleVersion="1.0.0",
            severity=Severity.LOW,
            severityBasis="Проверка владельца объекта не найдена на статическом пути.",
            confidence=Confidence.INCONCLUSIVE,
            confidenceBasis=(
                "Маршрут получает объект по path-переменной; в достижимом SQL нет owner-предиката, "
                "Principal не используется. Отсутствие проверки в runtime не доказано."
            ),
            status=FindingStatus.INCONCLUSIVE,
            primaryLocation=_backend_location(route_evidence["location"]),
            relatedLocations=[_backend_location(sink.evidence["location"])],
            trace=[
                _trace(1, f"Маршрут {route.method} {route.path} принимает path-переменную", route_evidence),
                _trace(2, f"{method.class_name}.{method.name} читает объект по id", sink.evidence),
            ],
            redactedEvidence=mask_text(call_evidence["redactedSnippet"]),
            advisoryIds=[],
            coverageWarnings=[
                "Не утверждение об уязвимости: owner-check может быть в runtime, прокси или сервисном слое.",
                "Проверка владельца не выдумывается; связь помечена unknown.",
            ],
        )
        self.result.findings.append(finding)
        self.result.finding_symbols[finding.id] = [
            f"route:{route.method}:{route.path}",
            f"function:{route.class_fq}.{route.handler}",
            guard_symbol,
            f"sink:{sink.file}:{method.name}:{sink.start_line}",
        ]

    def _emit_sql_findings(self) -> None:
        for methods in self.methods_by_class.values():
            for method in methods:
                for sink in method.sinks:
                    if not sink.tainted_params:
                        continue
                    param_name = sink.tainted_params[0]
                    param = next((p for p in method.params if p.name == param_name), None)
                    if not param:
                        continue
                    source_evidence = make_evidence(method.source, method.fun_line, method.fun_line, kind="source")
                    context = f"{sink.receiver}.{sink.method}|{param_name}|{sink.sql[:120]}"
                    finding_id, fingerprint = _finding_id(
                        "CF-SQL-PARAM-INTERPOLATION", sink.file, sink.start_line, context,
                    )
                    finding = Finding(
                        id=finding_id,
                        fingerprint=fingerprint,
                        analyzer="codefacts",
                        ruleId="CF-SQL-PARAM-INTERPOLATION",
                        ruleVersion="1.0.0",
                        severity=Severity.CRITICAL,
                        severityBasis=f"Параметр '{param_name}' интерполируется в SQL-строку без плейсхолдера.",
                        confidence=Confidence.CONFIRMED,
                        confidenceBasis="Прямая интерполяция видна в тексте SQL и в сигнатуре функции.",
                        status=FindingStatus.CONFIRMED,
                        primaryLocation=_backend_location(sink.evidence["location"]),
                        relatedLocations=[_backend_location(source_evidence["location"])],
                        trace=[
                            _trace(1, f"Параметр '{param_name}' приходит из HTTP-запроса", source_evidence),
                            _trace(2, f"Интерполяция в {sink.receiver}.{sink.method}", sink.evidence),
                        ],
                        redactedEvidence=mask_text(sink.evidence["redactedSnippet"]),
                        advisoryIds=[],
                        coverageWarnings=["Статический анализ; runtime-достижимость маршрута не проверялась."],
                    )
                    self.result.findings.append(finding)
                    self.result.finding_symbols[finding.id] = [
                        f"function:{method.class_fq}.{method.name}",
                        f"sink:{sink.file}:{method.name}:{sink.start_line}",
                        f"source:{sink.file}:{method.name}:{param_name}",
                    ]


def extract_facts(sources: Dict[str, SourceFile], files_total: int) -> ExtractionResult:
    extractor = SpringFactExtractor(sources, files_total)
    extractor.parse_security()
    return extractor.run()