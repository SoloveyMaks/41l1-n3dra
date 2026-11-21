"""Downloadable report exports built from saved runtime rows.

The export path reads only `runtime.runs`, `runtime.findings`, `runtime.reports`
and the stored AI_SCAN node output. It never re-runs scanners or calls an LLM.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import unquote_plus, urlsplit, urlunsplit

from backend.app.ai.provider import mask_sensitive_text
from backend.app.codefacts.source import mask_text
from backend.app.reports.language import completeness_label, naturalize
from backend.app.storage.database import DatabaseRepository

EXPORT_SCHEMA_VERSION = "nedra-report-export/1"

_SECRET_KEY_WORDS = {
    "key", "apikey", "accesskey", "secret", "clientsecret", "token", "password",
    "passwd", "pwd", "authorization", "credential", "credentials", "privatekey",
    "bearer",
}
_SECRET_QUERY_KEYS = {"key", "sig", "signature"}
_NON_SECRET_SUFFIX_WORDS = {
    "count", "counts", "id", "ids", "name", "names", "type", "types",
    "limit", "limits", "max", "min", "total", "length", "size", "usage",
    "expires", "expiry", "ttl", "at", "version", "versions",
}
_PATH_LIKE_KEY_WORDS = {
    "path", "paths", "url", "urls", "uri", "uris", "endpoint", "endpoints",
    "route", "routes", "query", "queries", "param", "params", "parameter",
    "parameters", "baseurl",
}
_URL_IN_TEXT_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s\"'<>]+")
_BARE_SECRET_PAIR_RE = re.compile(
    r"(?i)\b([A-Za-z0-9][A-Za-z0-9_.-]*)(\s*[=:]\s*)([^\s,;&\"']+)"
)
# A scheme-less reference inside an error string: an absolute path
# (`/token/<secret>`) or `host.tld/token/<secret>`. A bare word such as
# `key/value` is ordinary prose and never treated as a path.
_BARE_PATH_IN_TEXT_RE = re.compile(
    r"(?<![\w:/.@-])(?:"
    r"/[^\s\"'<>`()\[\],]+"
    r"|[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}/[^\s\"'<>`()\[\],]+"
    r")"
)
_PROVENANCE_MAX_DEPTH = 12
_COVERAGE_STATUS_LABELS = {
    "complete": "полное",
    "partial": "частичное",
    "unavailable": "недоступно",
    "unknown": "неизвестно",
}


def _name_words(name: str) -> List[str]:
    """Split CamelCase/snake_case/kebab-case into lowercase words."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(name))
    return [word for word in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if word]


def _is_secret_name(name: str, extra_words=()) -> bool:
    """A credential name, but `promptTokens`/`tokenCount`/`apiKeyId` stay safe."""
    words = _name_words(name)
    for index, word in enumerate(words):
        if word not in _SECRET_KEY_WORDS and word not in extra_words:
            continue
        rest = words[index + 1:]
        if rest and all(item in _NON_SECRET_SUFFIX_WORDS for item in rest):
            continue
        return True
    return False


def _is_secret_field(name: str) -> bool:
    return _is_secret_name(name)


def _is_secret_query_key(name: str) -> bool:
    return _is_secret_name(name, _SECRET_QUERY_KEYS)


def _redact_query(query: str) -> str:
    """Drop values of credential-looking params, keep every other byte as-is."""
    pairs: List[str] = []
    changed = False
    for raw in query.split("&"):
        key, separator, value = raw.partition("=")
        if separator and value and _is_secret_query_key(unquote_plus(key)):
            pairs.append(f"{key}=***")
            changed = True
        else:
            pairs.append(raw)
    return "&".join(pairs) if changed else query


def _redact_path(path: str) -> str:
    """Drop credential values in path segments while keeping the endpoint shape.

    Covers `/token/<secret>`, `/api/key/<secret>`, matrix params
    (`;api_key=<secret>`) and `key=value`/`key:value` inside a segment. Known
    token formats (`sk-…`, `AKIA…`, `ghp_…`) are masked even without a marker.
    """
    if not path:
        return path
    segments = path.split("/")
    redacted: List[str] = []
    previous_is_marker = False
    for raw in segments:
        if not raw:
            redacted.append(raw)
            continue
        pieces: List[str] = []
        for piece in raw.split(";"):
            key, separator, value = piece.partition("=")
            if not separator:
                key, separator, value = piece.partition(":")
            if separator and value and _is_secret_query_key(unquote_plus(key)):
                pieces.append(f"{key}{separator}***")
            else:
                pieces.append(mask_sensitive_text(piece))
        segment = ";".join(pieces)
        if previous_is_marker and segment != "***":
            segment = "***"
        redacted.append(segment)
        decoded = unquote_plus(raw)
        previous_is_marker = (
            not any(separator in decoded for separator in "=:;")
            and _is_secret_query_key(decoded)
        )
    return "/".join(redacted)


def _sanitize_url(url: str) -> str:
    """Drop URL userinfo, path and query credentials, keep safe provenance."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return mask_sensitive_text(url)
    netloc = parts.netloc
    if "@" in netloc:
        # Keep the raw authority after userinfo: it preserves IPv6 brackets and
        # odd ports that `parts.port` would reject on historical values.
        host = netloc.rsplit("@", 1)[1]
        netloc = f"***@{host}" if host else "***"
    return urlunsplit(
        (parts.scheme, netloc, _redact_path(parts.path),
         _redact_query(parts.query), parts.fragment)
    )


def _sanitize_reference(value: str) -> str:
    """Sanitize a URL, a bare path, or a query-like provenance value."""
    if "://" in value:
        return _sanitize_text(value)
    try:
        parts = urlsplit(value)
    except ValueError:
        return _redact_path(value)
    netloc = parts.netloc
    if "@" in netloc:
        host = netloc.rsplit("@", 1)[1]
        netloc = f"***@{host}" if host else "***"
    if netloc or parts.query:
        return urlunsplit(
            ("", netloc, _redact_path(parts.path), _redact_query(parts.query), parts.fragment)
        )
    if "/" not in value and "=" in value:
        return _redact_query(value)
    return _redact_path(value)


def _is_path_like_key(name: str) -> bool:
    return any(word in _PATH_LIKE_KEY_WORDS for word in _name_words(name))


def _mask_bare_pairs(text: str) -> str:
    def replace(match: re.Match) -> str:
        key = match.group(1)
        if not _is_secret_query_key(key):
            return match.group(0)
        value = match.group(3)
        # `mask_sensitive_text` already redacts the token after a scheme word.
        if "authorization" in _name_words(key) and value.lower() in ("bearer", "basic"):
            return match.group(0)
        return f"{key}{match.group(2)}***"

    return _BARE_SECRET_PAIR_RE.sub(replace, text)


def _sanitize_prose(value: str) -> str:
    """Prose without a URL scheme: bare secret pairs and credential paths."""
    text = _mask_bare_pairs(mask_sensitive_text(value))
    return _BARE_PATH_IN_TEXT_RE.sub(
        lambda match: _sanitize_reference(match.group(0)), text
    )


def _sanitize_text(value: str) -> str:
    """Mask secrets in prose without letting the text masker eat URL params."""
    chunks: List[str] = []
    cursor = 0
    for match in _URL_IN_TEXT_RE.finditer(value):
        chunks.append(_sanitize_prose(value[cursor:match.start()]))
        chunks.append(_sanitize_url(match.group(0)))
        cursor = match.end()
    chunks.append(_sanitize_prose(value[cursor:]))
    return "".join(chunks)


def _sanitize_provenance(value: Any, key: str = "", depth: int = 0) -> Any:
    """Recursively strip credentials from provenance while keeping safe fields.

    Export is the last boundary before a file leaves the process, so a
    historical row must not carry URL userinfo, path/query credentials or
    secret-named fields into JSONC/DOCX. Token counts and endpoint shape stay.
    """
    if depth > _PROVENANCE_MAX_DEPTH:
        return "[REDACTED]"
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for name, item in value.items():
            name = str(name)
            if _is_secret_field(name) and item not in (None, ""):
                cleaned[name] = "[REDACTED]"
            else:
                cleaned[name] = _sanitize_provenance(item, name, depth + 1)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_sanitize_provenance(item, key, depth + 1) for item in value]
    if isinstance(value, str):
        if _is_secret_field(key) and value:
            return "[REDACTED]"
        if _is_path_like_key(key) and value:
            return _sanitize_reference(value)
        return _sanitize_text(value)
    return value


def sanitize_text(value: str) -> str:
    """Public text boundary: Mask URL, path and query credentials in prose.

    Legacy HTTP handlers (``api/router.py``) serve stored report rows, so they
    must run the same sanitizer the export builders use instead of a weaker
    per-field mask. Safe metadata (token counts, endpoint shape) stays intact.
    """
    return _sanitize_text(str(value))


def sanitize_provenance(value: Any) -> Any:
    """Public recursive boundary for arbitrary stored provenance/document data.

    Keeps keys, counts and canonical values; replaces credential-named fields
    with ``[REDACTED]`` and sanitizes URL/path/query values. ``export`` uses the
    private implementation directly; this wrapper exists for the legacy report
    routes without exposing a second convention.
    """
    return _sanitize_provenance(value)


def _sanitized_export_copy(value: Any) -> Any:
    """A plain, sanitized copy of stored passport/policy for the JSONC boundary.

    Persisted canonical rows stay untouched. Pydantic models and dataclasses
    are converted to plain data first, then the shared sanitizer removes URL
    userinfo, path/query credentials and credential-named fields while keeping
    canonical counts/keys and safe metadata (`tokenCount`, `apiKeyId`).
    """
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    elif hasattr(value, "__dataclass_fields__"):
        value = {
            name: getattr(value, name)
            for name in value.__dataclass_fields__
        }
    return sanitize_provenance(value)


def _contradictory_coverage_warning(passport: Dict[str, Any]) -> Optional[str]:
    """A passport may claim confirmed completeness while naming another status."""
    if not passport.get("completeness_known"):
        return None
    status = str(passport.get("coverage_status") or "unknown")
    if status == "complete":
        return None
    label = _COVERAGE_STATUS_LABELS.get(status, status)
    return (
        "Сохранённый паспорт противоречив: полнота помечена как подтверждённая, "
        f"но статус покрытия — «{label}». Полнота считается не установленной."
    )


def _display_passport(passport: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Conservative display copy; canonical JSONC passport stays untouched."""
    result = dict(passport or {})
    warning = _contradictory_coverage_warning(result)
    if warning:
        result["completeness_known"] = False
        warnings = result.get("coverage_warnings")
        result["coverage_warnings"] = [
            *(warnings if isinstance(warnings, list) else []),
            warning,
        ]
    return result


def _passport_count(passport: Dict[str, Any], key: str) -> Optional[int]:
    """A saved count, or None when the passport did not store that measurement."""
    if key not in passport:
        return None
    value = passport.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _passport_rules_count(passport: Dict[str, Any]) -> Optional[int]:
    if "rules_executed" not in passport:
        return None
    rules = passport.get("rules_executed")
    return len(rules) if isinstance(rules, list) else None


def _count_display(value: Optional[int]) -> Any:
    return "—" if value is None else value


def _rewrite_unknown_coverage(text: str, passport: Dict[str, Any]) -> str:
    """Never present an absent saved measurement as a measured zero."""
    scanned = _passport_count(passport, "files_scanned")
    skipped = _passport_count(passport, "files_skipped")
    if scanned is None or skipped is None:
        text = re.sub(
            r"(?m)^Просканировано файлов: \d+; пропущено: \d+\.",
            "Просканировано файлов: "
            f"{'неизвестно' if scanned is None else scanned}; пропущено: "
            f"{'неизвестно' if skipped is None else skipped}.",
            text,
        )
    measurements = {
        "Проверено файлов": scanned,
        "Пропущено файлов": skipped,
        "Строк кода": _passport_count(passport, "lines_of_code"),
        "Запущено правил": _passport_rules_count(passport),
        "Ошибок парсера": _passport_count(passport, "parser_errors"),
        "Неразрешённых зависимостей": _passport_count(passport, "unresolved_dependencies"),
    }
    for label, value in measurements.items():
        if value is None:
            text = re.sub(
                rf"(?m)^\| {re.escape(label)} \| [^|]* \|$",
                f"| {label} | неизвестно |",
                text,
            )
    return text


_VERDICT_LABELS = {
    "pass": "нарушений не обнаружено",
    "fail": "требуется исправление",
    "inconclusive": "нужна дополнительная проверка",
    "pending": "итог ещё не определён",
}
_STATUS_LABELS = {
    "queued": "в очереди",
    "running": "выполняется",
    "succeeded": "завершено",
    "failed": "завершилось ошибкой",
    "interrupted": "прервано",
    "cancelled": "отменено",
}
_SEVERITY_LABELS = {
    "critical": "критический",
    "high": "высокий",
    "medium": "средний",
    "low": "низкий",
    "info": "информационный",
}
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
_CONFIDENCE_LABELS = {
    "confirmed": "подтверждено",
    "potential": "потенциально",
    "inconclusive": "не определено",
}
_AI_STATUS_LABELS = {
    "confirmed": "подтверждено",
    "potential": "не подтверждено",
    "inconclusive": "не определено",
}
_AI_SCAN_STATUS_LABELS = {
    "succeeded": "завершён",
    "partial": "частичный",
    "degraded": "ограниченный",
    "rejected": "отклонён валидатором",
    "failed": "завершился ошибкой",
    "disabled": "не запускался",
}


class ReportExportError(RuntimeError):
    pass


class ReportNotFound(ReportExportError):
    pass


class ReportExportUnavailable(ReportExportError):
    pass


@dataclass(frozen=True)
class ExportBundle:
    run: Dict[str, Any]
    report: Optional[Dict[str, Any]]
    findings: List[Dict[str, Any]]
    ai_scan: Dict[str, Any]
    passport: Dict[str, Any]
    policy: Dict[str, Any]
    official: Dict[str, Any]
    workflow_name: str
    counts: Dict[str, int]
    technical: Dict[str, Any]


def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _format_dt(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    else:
        return ""
    return parsed.strftime("%d.%m.%Y %H:%M UTC")


def _safe_location(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    return {
        "path": value.get("path"),
        "startLine": value.get("startLine"),
        "endLine": value.get("endLine"),
        "startColumn": value.get("startColumn"),
        "endColumn": value.get("endColumn"),
        "byteRange": value.get("byteRange"),
    }


def _safe_trace(value: Any) -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = []
    if not isinstance(value, list):
        return steps
    for item in value:
        if not isinstance(item, dict):
            continue
        steps.append({
            "step": item.get("step"),
            "description": _sanitize_text(naturalize(str(item.get("description") or ""))),
            "location": _safe_location(item.get("location")),
        })
    return steps


def _safe_finding(finding: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": finding.get("id"),
        "fingerprint": finding.get("fingerprint"),
        "analyzer": finding.get("analyzer"),
        "ruleId": finding.get("ruleId") or finding.get("rule_id"),
        "ruleVersion": finding.get("ruleVersion") or finding.get("rule_version"),
        "severity": finding.get("severity"),
        "severityBasis": _sanitize_text(naturalize(str(finding.get("severityBasis") or ""))),
        "confidence": finding.get("confidence"),
        "confidenceBasis": _sanitize_text(naturalize(str(finding.get("confidenceBasis") or ""))),
        "status": finding.get("status") or "inconclusive",
        "primaryLocation": _safe_location(finding.get("primaryLocation")),
        "relatedLocations": [
            _safe_location(item) for item in (finding.get("relatedLocations") or [])
        ],
        "trace": _safe_trace(finding.get("trace")),
        "redactedEvidence": mask_text(str(finding.get("redactedEvidence") or "")),
        "advisoryIds": list(finding.get("advisoryIds") or []),
        "coverageWarnings": [
            _sanitize_text(str(item)) for item in (finding.get("coverageWarnings") or [])
        ],
    }


def _safe_ai_scan(result: Any) -> Dict[str, Any]:
    if not isinstance(result, dict) or not result:
        return {
            "status": "disabled",
            "verified": False,
            "countsAsFindings": False,
            "limitations": ["ИИ-сканер не входил в сценарий или не сформировал результат."],
            "observations": [],
            "provenance": None,
            "coverage": None,
        }
    observations = []
    for item in result.get("observations") or []:
        if not isinstance(item, dict):
            continue
        observations.append({
            "id": item.get("id"),
            "origin": item.get("origin") or "ai_hypothesis",
            "ruleClass": item.get("ruleClass"),
            "severity": item.get("severity"),
            "confidence": item.get("confidence"),
            "status": item.get("status"),
            "primaryLocation": _safe_location(item.get("primaryLocation")),
            "redactedEvidence": mask_text(str(item.get("redactedEvidence") or "")),
            "rationale": _sanitize_text(naturalize(str(item.get("rationale") or ""))),
            "remediation": _sanitize_text(naturalize(str(item.get("remediation") or ""))),
            "grounding": _sanitize_provenance(item.get("grounding")),
        })
    observations.sort(
        key=lambda item: (
            _SEVERITY_ORDER.get(str(item.get("severity")), 9),
            str(item.get("id") or ""),
        )
    )
    coverage = _sanitize_provenance(result.get("coverage")) if isinstance(result.get("coverage"), dict) else None
    limitations = [
        "Гипотеза не подтверждена детерминированными сканерами и не меняет итог проверки.",
        "Модель видела только выбранный фрагмент проекта с удалёнными секретами, а не весь репозиторий.",
    ]
    if coverage:
        if coverage.get("omittedCount"):
            limitations.append(
                f"Часть файлов не отправлялась: {coverage['omittedCount']}."
            )
        if coverage.get("truncatedFiles"):
            limitations.append(
                f"Часть файлов отправлена не полностью: {len(coverage['truncatedFiles'])}."
            )
        for error in (coverage.get("providerErrors") or [])[:5]:
            limitations.append(f"Ошибка провайдера: {_sanitize_text(str(error))}")
    return {
        "status": result.get("status") or "disabled",
        "verified": False,
        "countsAsFindings": False,
        "limitations": limitations,
        "observations": observations,
        "provenance": _sanitize_provenance(result.get("provenance")),
        "coverage": coverage,
    }


def _counts(findings: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    result = {
        "total": len(findings),
        "critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0,
        "confirmed": 0, "potential": 0, "inconclusive": 0,
    }
    for item in findings:
        severity = str(item.get("severity") or "info")
        if severity in result:
            result[severity] += 1
        status = str(item.get("status") or "inconclusive")
        if status in result:
            result[status] += 1
    return result


def _official(report: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not report:
        return {}
    raw = report.get("official_json")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}


def _workflow_name(db: DatabaseRepository, workflow_id: Any) -> str:
    if not isinstance(workflow_id, str) or not workflow_id:
        return ""
    try:
        workflow = db.get_workflow(workflow_id)
    except Exception:
        return ""
    return str((workflow or {}).get("name") or "")


def _frozen_findings(official: Dict[str, Any]) -> Optional[List[Any]]:
    """The saved report's finding list, only when it really is one.

    A list — including an intentionally empty one — freezes the finding set.
    A missing, null, string or object value is malformed legacy data and must
    fall back to runtime rows, exactly as the bundle loader does.
    """
    source = official.get("findings")
    return source if isinstance(source, list) else None


def load_export_bundle(db: DatabaseRepository, run_id: str) -> ExportBundle:
    run = db.get_run(run_id)
    if not run:
        raise ReportNotFound("Запуск не найден в текущем workspace")
    report = db.get_report(run_id)
    official = _official(report)
    # A saved report freezes its finding set, including an intentionally empty list.
    source = _frozen_findings(official)
    if source is None:
        source = db.get_findings(run_id)
    findings = [_safe_finding(item) for item in source]
    findings.sort(key=lambda item: (
        _SEVERITY_ORDER.get(str(item.get("severity")), 9),
        str(item.get("ruleId") or ""),
        str(item.get("id") or ""),
    ))
    passport = official.get("securityPassport")
    if not isinstance(passport, dict):
        passport = (report or {}).get("passport") or {}
    policy = official.get("policyResult") if isinstance(official.get("policyResult"), dict) else {}

    ai_scan: Dict[str, Any] = {}
    for node_run in reversed(db.get_node_runs(run_id)):
        if node_run.get("kind") != "ai_scan":
            continue
        output = node_run.get("output_data_masked") or {}
        value = output.get("ai_scan_result") if isinstance(output, dict) else None
        if isinstance(value, dict):
            ai_scan = _safe_ai_scan(value)
            break
    if not ai_scan:
        ai_scan = _safe_ai_scan({})

    technical = {
        "exportSchemaVersion": EXPORT_SCHEMA_VERSION,
        "aiAnalysis": _sanitize_provenance(official.get("aiAnalysis")),
        "aiScan": _sanitize_provenance(ai_scan.get("provenance")),
        "aiScanCoverage": _sanitize_provenance(ai_scan.get("coverage")),
    }
    return ExportBundle(
        run=run,
        report=report,
        findings=findings,
        ai_scan=ai_scan,
        passport=passport,
        policy=policy,
        official=official,
        workflow_name=_workflow_name(db, run.get("workflow_id")),
        counts=_counts(findings),
        technical=technical,
    )


def build_jsonc(bundle: ExportBundle) -> bytes:
    """Strict-JSON report (JSONC-compatible; no comments, no duplicate source)."""
    run = bundle.run
    limitations = [
        "Отчёт собран по сохранённым результатам; повторный анализ и обращения к модели не выполнялись.",
        "Итог проверки относится к подтверждённому покрытию, а не ко всему проекту.",
        *bundle.ai_scan.get("limitations", []),
    ]
    security_passport = _sanitized_export_copy(bundle.passport)
    policy_result = _sanitized_export_copy(bundle.policy)
    coverage_warning = (
        _contradictory_coverage_warning(security_passport)
        if isinstance(security_passport, dict)
        else None
    )
    if coverage_warning:
        limitations.append(coverage_warning)
    document = {
        "schemaVersion": EXPORT_SCHEMA_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "run": {
            "id": run.get("id"),
            "workflowId": run.get("workflow_id"),
            "workflowName": bundle.workflow_name,
            "revisionId": run.get("revision_id"),
            "artifactId": run.get("artifact_id"),
            "triggerType": run.get("trigger_type"),
            "executionStatus": run.get("execution_status"),
            "executionStatusLabel": _STATUS_LABELS.get(str(run.get("execution_status")), str(run.get("execution_status") or "")),
            "securityVerdict": run.get("security_verdict"),
            "securityVerdictLabel": _VERDICT_LABELS.get(str(run.get("security_verdict")), str(run.get("security_verdict") or "")),
            "startedAt": _iso(run.get("started_at")),
            "finishedAt": _iso(run.get("finished_at")),
            "createdAt": _iso(run.get("created_at")),
        },
        "summary": bundle.counts,
        "reportAvailable": bundle.report is not None,
        "deterministic": {
            "count": len(bundle.findings),
            "findings": bundle.findings,
        },
        "aiHypotheses": bundle.ai_scan,
        "securityPassport": security_passport,
        "policyResult": policy_result,
        "technicalProvenance": bundle.technical,
        "limitations": limitations,
    }
    return json.dumps(document, ensure_ascii=False, indent=2, default=str).encode("utf-8")


def build_markdown(bundle: ExportBundle) -> str:
    """Readable Markdown from the same saved bundle as JSONC and DOCX."""
    from backend.app.contracts.schemas import ExplainResult, Finding, PolicyResult, SecurityPassport
    from backend.app.reports.builder import ReportBuilder

    verdict = bundle.run.get("security_verdict") or "pending"
    display = _display_passport(bundle.passport)
    passport = SecurityPassport.model_validate({**display, "verdict": verdict})
    policy = PolicyResult.model_validate({**bundle.policy, "verdict": verdict})
    analysis = bundle.official.get("aiAnalysis") or {}
    items = [
        _sanitize_provenance(item["ai_enrichment"])
        for item in (_frozen_findings(bundle.official) or [])
        if isinstance(item, dict) and isinstance(item.get("ai_enrichment"), dict)
    ]
    explanation = ExplainResult(
        status=analysis.get("status") or "disabled",
        model=_sanitize_text(str(analysis.get("model") or "")),
        summary=_sanitize_text(str(analysis.get("summary") or "")),
        items=items,
    ) if analysis.get("status") not in (None, "disabled") else None
    text = ReportBuilder().build_readable_markdown(
        str(bundle.run.get("id") or ""), bundle.workflow_name, passport, policy,
        [Finding.model_validate(item) for item in bundle.findings], explanation,
        execution_status=str(bundle.run.get("execution_status") or "queued"),
        ai_scan=bundle.ai_scan,
    )
    text = _rewrite_unknown_coverage(text, bundle.passport)
    if bundle.report is None:
        text += "\n\nСохранённый отчёт не сформирован; показаны только сохранённые результаты запуска.\n"
    # The builder already masks known fields; this is the last boundary, so the
    # whole document gets the same URL/path/query sanitizer as JSONC and DOCX.
    return _sanitize_text(text) + "\n"


def _docx_modules():
    try:
        from docx import Document
        from docx.enum.section import WD_SECTION
        from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.shared import Inches, Pt, RGBColor
    except ImportError as exc:
        raise ReportExportUnavailable("python-docx не установлен") from exc
    return {
        "Document": Document,
        "WD_SECTION": WD_SECTION,
        "WD_ALIGN_VERTICAL": WD_ALIGN_VERTICAL,
        "WD_TABLE_ALIGNMENT": WD_TABLE_ALIGNMENT,
        "WD_ALIGN_PARAGRAPH": WD_ALIGN_PARAGRAPH,
        "OxmlElement": OxmlElement,
        "qn": qn,
        "Inches": Inches,
        "Pt": Pt,
        "RGBColor": RGBColor,
    }


def _shade(cell: Any, fill: str, mods: Dict[str, Any]) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.first_child_found_in("w:shd")
    if shd is None:
        shd = mods["OxmlElement"]("w:shd")
        tc_pr.append(shd)
    shd.set(mods["qn"]("w:fill"), fill)


def _cell_borders(cell: Any, mods: Dict[str, Any], color: str = "D9D9D9") -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = tc_pr.first_child_found_in("w:tcBorders")
    if borders is None:
        borders = mods["OxmlElement"]("w:tcBorders")
        tc_pr.append(borders)
    for edge in ("top", "left", "bottom", "right"):
        tag = f"w:{edge}"
        element = borders.find(mods["qn"](tag))
        if element is None:
            element = mods["OxmlElement"](tag)
            borders.append(element)
        element.set(mods["qn"]("w:val"), "single")
        element.set(mods["qn"]("w:sz"), "4")
        element.set(mods["qn"]("w:space"), "0")
        element.set(mods["qn"]("w:color"), color)


def _clear_paragraph_borders(paragraph_or_style: Any, mods: Dict[str, Any]) -> None:
    element = getattr(paragraph_or_style, "element", None)
    if element is None:
        element = getattr(paragraph_or_style, "_p", None)
    if element is None:
        return
    p_pr = element.find(mods["qn"]("w:pPr"))
    if p_pr is None:
        return
    for border in list(p_pr.findall(mods["qn"]("w:pBdr"))):
        p_pr.remove(border)


def _set_table_widths(table: Any, widths: Sequence[float], mods: Dict[str, Any]) -> None:
    table.autofit = False
    table.alignment = mods["WD_TABLE_ALIGNMENT"].CENTER
    tbl = table._tbl
    tbl_pr = tbl.tblPr
    total_twips = int(round(sum(widths) * 1440))
    tbl_w = tbl_pr.find(mods["qn"]("w:tblW"))
    if tbl_w is None:
        tbl_w = mods["OxmlElement"]("w:tblW")
        tbl_pr.append(tbl_w)
    tbl_w.set(mods["qn"]("w:type"), "dxa")
    tbl_w.set(mods["qn"]("w:w"), str(total_twips))
    layout = tbl_pr.find(mods["qn"]("w:tblLayout"))
    if layout is None:
        layout = mods["OxmlElement"]("w:tblLayout")
        tbl_pr.append(layout)
    layout.set(mods["qn"]("w:type"), "fixed")
    grid = tbl.tblGrid
    for index, width in enumerate(widths):
        twips = str(int(round(width * 1440)))
        if index < len(grid.gridCol_lst):
            grid.gridCol_lst[index].set(mods["qn"]("w:w"), twips)
        table.columns[index].width = mods["Inches"](width)
        for cell in table.columns[index].cells:
            cell.width = mods["Inches"](width)


def _table(doc: Any, headers: Sequence[str], rows: Sequence[Sequence[Any]],
           widths: Sequence[float], mods: Dict[str, Any], font_size: float = 9.5) -> Any:
    table = doc.add_table(rows=1, cols=len(headers))
    _set_table_widths(table, widths, mods)
    header = table.rows[0]
    header_props = header._tr.get_or_add_trPr()
    repeat = mods["OxmlElement"]("w:tblHeader")
    header_props.append(repeat)
    for index, text in enumerate(headers):
        cell = header.cells[index]
        cell.width = mods["Inches"](widths[index])
        _shade(cell, "404040", mods)
        _cell_borders(cell, mods)
        cell.vertical_alignment = mods["WD_ALIGN_VERTICAL"].CENTER
        paragraph = cell.paragraphs[0]
        paragraph.alignment = mods["WD_ALIGN_PARAGRAPH"].LEFT
        run = paragraph.add_run(str(text))
        run.bold = True
        run.font.color.rgb = mods["RGBColor"](0xFF, 0xFF, 0xFF)
        run.font.size = mods["Pt"](font_size)
    for row_index, values in enumerate(rows):
        row = table.add_row()
        for index, value in enumerate(values):
            cell = row.cells[index]
            cell.width = mods["Inches"](widths[index])
            cell.vertical_alignment = mods["WD_ALIGN_VERTICAL"].CENTER
            _cell_borders(cell, mods)
            if row_index % 2 == 1:
                _shade(cell, "F2F2F2", mods)
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.space_after = mods["Pt"](0)
            run = paragraph.add_run(str(value if value is not None else ""))
            run.font.size = mods["Pt"](font_size)
    doc.add_paragraph().paragraph_format.space_after = mods["Pt"](2)
    return table


def _severity_label(value: Any) -> str:
    return _SEVERITY_LABELS.get(str(value), str(value or ""))


def _location_text(location: Any) -> str:
    if not isinstance(location, dict):
        return "не указано"
    path = location.get("path") or "?"
    start = location.get("startLine")
    end = location.get("endLine")
    if end and end != start:
        return f"{path}, строки {start}–{end}"
    return f"{path}, строка {start}"


def build_docx(bundle: ExportBundle) -> bytes:
    mods = _docx_modules()
    Document = mods["Document"]
    doc = Document()
    section = doc.sections[0]
    section.page_width = mods["Inches"](8.5)
    section.page_height = mods["Inches"](11)
    section.top_margin = mods["Inches"](0.75)
    section.bottom_margin = mods["Inches"](0.75)
    section.left_margin = mods["Inches"](0.85)
    section.right_margin = mods["Inches"](0.85)

    styles = doc.styles
    normal = styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = mods["Pt"](10.5)
    normal.paragraph_format.space_after = mods["Pt"](6)
    normal.paragraph_format.line_spacing = 1.15
    for name, size in (("Title", 24), ("Heading 1", 16), ("Heading 2", 12.5)):
        style = styles[name]
        style.font.name = "Calibri"
        style.font.size = mods["Pt"](size)
        style.font.bold = True
        style.font.color.rgb = mods["RGBColor"](0, 0, 0)
        style.paragraph_format.space_before = mods["Pt"](10)
        style.paragraph_format.space_after = mods["Pt"](4)
        _clear_paragraph_borders(style, mods)
    styles["Title"].font.size = mods["Pt"](24)
    styles["Title"].paragraph_format.space_after = mods["Pt"](10)
    _clear_paragraph_borders(styles["Title"], mods)

    footer = section.footer.paragraphs[0]
    footer.text = "NEDRA · Отчёт о проверке безопасности кода"
    footer.alignment = mods["WD_ALIGN_PARAGRAPH"].RIGHT
    for run in footer.runs:
        run.font.size = mods["Pt"](8)
        run.font.color.rgb = mods["RGBColor"](0x59, 0x59, 0x59)

    run = bundle.run
    verdict = _VERDICT_LABELS.get(
        str(run.get("security_verdict")),
        str(run.get("security_verdict") or "не определён"),
    )
    status = _STATUS_LABELS.get(
        str(run.get("execution_status")),
        str(run.get("execution_status") or "неизвестно"),
    )
    scenario = bundle.workflow_name or run.get("workflow_id") or "без названия"
    title = doc.add_paragraph("Отчёт о проверке безопасности кода", style="Title")
    _clear_paragraph_borders(title, mods)
    intro = doc.add_paragraph()
    intro.add_run(
        f"Проверен сценарий «{scenario}». Итог: {verdict}. Ниже — что найдено "
        "в проверенном объёме, какие гипотезы требуют проверки и что осталось "
        "за пределами проверки."
    )

    if bundle.report is None:
        doc.add_paragraph(
            "Сохранённый отчёт для этого запуска не сформирован. В документе показаны "
            "сохранённые находки, статус запуска и технические сведения."
        )

    _table(
        doc,
        ["Параметр", "Значение"],
        [
            ["Сценарий", bundle.workflow_name or run.get("workflow_id") or "не указан"],
            ["Номер запуска", run.get("id") or ""],
            ["Дата", _format_dt(run.get("finished_at") or run.get("created_at"))],
            ["Статус", status],
            ["Итог", verdict],
        ],
        [1.7, 5.1],
        mods,
        10,
    )

    heading = doc.add_heading("Основные показатели", level=1)
    heading.paragraph_format.keep_with_next = True
    passport = _display_passport(bundle.passport)
    _table(
        doc,
        ["Показатель", "Значение"],
        [
            ["Всего находок", bundle.counts["total"]],
            ["Критические", bundle.counts["critical"]],
            ["Высокие", bundle.counts["high"]],
            ["Средние", bundle.counts["medium"]],
            ["Низкие", bundle.counts["low"]],
            ["Информационные", bundle.counts["info"]],
            ["Подтверждено", bundle.counts["confirmed"]],
            ["Потенциально", bundle.counts["potential"]],
            ["Не определено", bundle.counts["inconclusive"]],
            ["Проверено файлов", _count_display(_passport_count(passport, "files_scanned"))],
            ["Пропущено файлов", _count_display(_passport_count(passport, "files_skipped"))],
            ["Запущено правил", _count_display(_passport_rules_count(passport))],
            ["Строк кода", _count_display(_passport_count(passport, "lines_of_code"))],
            [
                "Полнота проверки",
                completeness_label(
                    bool(passport.get("completeness_known")),
                    str(passport.get("coverage_status") or "unknown"),
                ),
            ],
        ],
        [3.0, 3.8],
        mods,
        9.5,
    )

    reasons = bundle.policy.get("reasons") if isinstance(bundle.policy.get("reasons"), list) else []
    doc.add_heading("Что проверено", level=1)
    scanned = _passport_count(passport, "files_scanned")
    skipped = _passport_count(passport, "files_skipped")
    doc.add_paragraph(
        "Просканировано файлов: "
        f"{'неизвестно' if scanned is None else scanned}; пропущено: "
        f"{'неизвестно' if skipped is None else skipped}. "
        "Результат относится только к указанным правилам и проверенным областям."
    )
    rules = passport.get("rules_executed") or []
    doc.add_paragraph(
        "Запущенные правила: " + ", ".join(str(rule) for rule in rules) + "."
        if rules else "Список выполненных правил не сохранён; полнота набора проверок неизвестна."
    )
    if reasons:
        heading = doc.add_heading("Почему такой итог", level=1)
        heading.paragraph_format.keep_with_next = True
        for reason in reasons:
            doc.add_paragraph(_sanitize_text(naturalize(str(reason))), style="List Bullet")
    coverage_reasons = passport.get("coverage_reasons") if isinstance(passport.get("coverage_reasons"), list) else []
    if coverage_reasons:
        heading = doc.add_heading("Что осталось за пределами проверки", level=1)
        heading.paragraph_format.keep_with_next = True
        for reason in coverage_reasons:
            doc.add_paragraph(_sanitize_text(naturalize(str(reason))), style="List Bullet")
    coverage_warnings = passport.get("coverage_warnings") or []
    if coverage_warnings:
        heading = doc.add_heading("Предупреждения по покрытию", level=2)
        heading.paragraph_format.keep_with_next = True
        for warning in coverage_warnings:
            doc.add_paragraph(_sanitize_text(naturalize(str(warning))), style="List Bullet")

    heading = doc.add_heading("Находки", level=1)
    heading.paragraph_format.keep_with_next = True
    if not bundle.findings:
        doc.add_paragraph(
            "Находок не обнаружено. Это не доказывает отсутствие уязвимостей за пределами "
            "проверенного покрытия."
        )
    else:
        rows = []
        for index, finding in enumerate(bundle.findings, 1):
            location = finding.get("primaryLocation") or {}
            rows.append([
                index,
                _severity_label(finding.get("severity")),
                finding.get("ruleId") or "",
                (
                    f"{location.get('path') or ''}, "
                    f"{location.get('startLine', '')}–{location.get('endLine', '')}"
                ).strip(" –"),
                finding.get("id") or "",
            ])
        _table(
            doc,
            ["№", "Уровень", "Правило", "Где", "ID"],
            rows,
            [0.35, 0.95, 1.35, 2.35, 1.8],
            mods,
            8.5,
        )

        for index, finding in enumerate(bundle.findings, 1):
            finding_heading = doc.add_heading(
                f"{index}. {_severity_label(finding.get('severity')).capitalize()}: {finding.get('ruleId')}",
                level=2,
            )
            finding_heading.paragraph_format.keep_with_next = True
            finding_id_paragraph = doc.add_paragraph(f"ID: {finding.get('id')}")
            finding_id_paragraph.paragraph_format.keep_with_next = True
            finding_rule_paragraph = doc.add_paragraph(
                f"Правило: {finding.get('ruleId')} ({finding.get('analyzer')})"
            )
            finding_rule_paragraph.paragraph_format.keep_with_next = True
            finding_location_paragraph = doc.add_paragraph(
                f"Где: {_location_text(finding.get('primaryLocation'))}"
            )
            finding_location_paragraph.paragraph_format.keep_with_next = True
            doc.add_paragraph(f"Почему это важно: {finding.get('severityBasis') or 'без пояснения'}")
            confidence = _CONFIDENCE_LABELS.get(str(finding.get("confidence")), str(finding.get("confidence") or ""))
            doc.add_paragraph(f"Достоверность: {confidence}. {finding.get('confidenceBasis') or ''}".strip())
            doc.add_paragraph("Статус доказательства: " + _CONFIDENCE_LABELS.get(
                str(finding.get("status")), str(finding.get("status") or "не определено")
            ))
            if finding.get("advisoryIds"):
                doc.add_paragraph("Связанные записи безопасности: " + ", ".join(finding["advisoryIds"]))
            if finding.get("redactedEvidence"):
                evidence = doc.add_paragraph()
                evidence.paragraph_format.left_indent = mods["Inches"](0.25)
                run_evidence = evidence.add_run(finding["redactedEvidence"])
                run_evidence.font.name = "Courier New"
                run_evidence.font.size = mods["Pt"](8.5)
            doc.add_paragraph("Ограничения:")
            finding_warnings = finding.get("coverageWarnings") or []
            for warning in finding_warnings:
                doc.add_paragraph(_sanitize_text(naturalize(str(warning))), style="List Bullet")
            if not finding_warnings:
                doc.add_paragraph(
                    "Отдельные ограничения этой находки не указаны; общие ограничения покрытия сохраняются."
                )
            trace = finding.get("trace") or []
            if trace:
                trace_heading = doc.add_paragraph("Цепочка доказательств:")
                trace_heading.paragraph_format.keep_with_next = True
                trace_paragraphs = []
                for step in trace:
                    location = f" ({_location_text(step.get('location'))})" if step.get("location") else ""
                    trace_paragraph = doc.add_paragraph(
                        f"{step.get('step')}. {step.get('description')}{location}",
                        style="List Bullet",
                    )
                    trace_paragraph.paragraph_format.keep_together = True
                    trace_paragraphs.append(trace_paragraph)
                # A short chain is one visual unit; a long chain keeps only the
                # heading/first bullet together and may flow across pages.
                if len(trace_paragraphs) <= 4:
                    trace_heading.paragraph_format.keep_together = True
                    for trace_paragraph in trace_paragraphs[:-1]:
                        trace_paragraph.paragraph_format.keep_with_next = True

    heading = doc.add_heading("Гипотезы ИИ-сканера", level=1)
    heading.paragraph_format.keep_with_next = True
    ai_scan = bundle.ai_scan
    scan_status = str(ai_scan.get("status") or "disabled")
    doc.add_paragraph(
        "Это гипотезы, а не подтверждённые находки. Они не меняют итог проверки и не "
        "входят в счётчики находок. Модель видела только выбранный фрагмент проекта с удалёнными секретами. "
        f"Статус ИИ-сканера: {_AI_SCAN_STATUS_LABELS.get(scan_status, scan_status)}."
    )
    observations = ai_scan.get("observations") or []
    if not observations:
        doc.add_paragraph("ИИ-сканер не сформировал гипотез для этого запуска.")
    else:
        rows = []
        for index, item in enumerate(observations, 1):
            location = item.get("primaryLocation") or {}
            rows.append([
                index,
                item.get("ruleClass") or "",
                _severity_label(item.get("severity")),
                (
                    f"{location.get('path') or ''}, "
                    f"{location.get('startLine', '')}–{location.get('endLine', '')}"
                ).strip(" –"),
                item.get("id") or "",
            ])
        _table(
            doc,
            ["№", "Класс", "Уровень", "Где", "ID"],
            rows,
            [0.35, 1.0, 0.75, 2.7, 2.0],
            mods,
            8.5,
        )
        for index, item in enumerate(observations, 1):
            ai_heading = doc.add_heading(
                f"{index}. {item.get('ruleClass')} ({_severity_label(item.get('severity'))})",
                level=2,
            )
            ai_heading.paragraph_format.keep_with_next = True
            ai_status = _AI_STATUS_LABELS.get(str(item.get("status")), str(item.get("status") or ""))
            ai_id_paragraph = doc.add_paragraph(
                f"ID: {item.get('id')}; статус: {ai_status}; проверено: нет."
            )
            ai_id_paragraph.paragraph_format.keep_with_next = True
            ai_location_paragraph = doc.add_paragraph(
                f"Где: {_location_text(item.get('primaryLocation'))}"
            )
            ai_location_paragraph.paragraph_format.keep_with_next = True
            doc.add_paragraph(f"Почему: {item.get('rationale') or 'без пояснения'}")
            doc.add_paragraph(f"Что проверить: {item.get('remediation') or 'без рекомендации'}")
            if item.get("redactedEvidence"):
                evidence = doc.add_paragraph()
                evidence.paragraph_format.left_indent = mods["Inches"](0.25)
                run_evidence = evidence.add_run(item["redactedEvidence"])
                run_evidence.font.name = "Courier New"
                run_evidence.font.size = mods["Pt"](8.5)
    limitations = ai_scan.get("limitations") or []
    if limitations:
        doc.add_paragraph("Ограничения:")
        for limitation in limitations:
            doc.add_paragraph(_sanitize_text(naturalize(str(limitation))), style="List Bullet")

    heading = doc.add_heading("Приложение А. Технические сведения", level=1)
    heading.paragraph_format.keep_with_next = True
    ai_analysis = bundle.technical.get("aiAnalysis") or {}
    ai_provenance = bundle.technical.get("aiScan") or {}
    ai_coverage = bundle.technical.get("aiScanCoverage") or {}
    _table(
        doc,
        ["Параметр", "Значение"],
        [
            ["Схема экспорта", EXPORT_SCHEMA_VERSION],
            ["ИИ-пояснения", ai_analysis.get("status") or "disabled"],
            ["Модель ИИ-пояснений", ai_analysis.get("model") or "—"],
            ["Модель ИИ-скана", ai_provenance.get("model") or "—"],
            ["Провайдер", ai_provenance.get("provider") or "—"],
            ["Версия промпта", ai_provenance.get("promptVersion") or "—"],
            ["Версия валидатора", ai_provenance.get("validatorVersion") or "—"],
            ["Версия конфигурации", ai_provenance.get("configVersion") or "—"],
            ["Идентификатор запроса", ai_provenance.get("requestId") or "—"],
            ["Адрес провайдера", ai_provenance.get("endpoint") or "—"],
            ["Статус ИИ-скана", ai_scan.get("status") or "disabled"],
            ["Покрытие ИИ-скана", ai_coverage.get("status") or "—"],
        ],
        [2.4, 4.4],
        mods,
        8.5,
    )

    output = io.BytesIO()
    doc.save(output)
    return output.getvalue()
