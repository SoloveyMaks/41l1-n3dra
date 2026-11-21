"""Downloadable report exports built from saved runtime rows.

The export path reads only `runtime.runs`, `runtime.findings`, `runtime.reports`
and the stored AI_SCAN node output. It never re-runs scanners or calls an LLM.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from backend.app.ai.provider import mask_sensitive_text
from backend.app.codefacts.source import mask_text
from backend.app.reports.language import naturalize
from backend.app.storage.database import DatabaseRepository

EXPORT_SCHEMA_VERSION = "nedra-report-export/1"

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
            "description": mask_sensitive_text(naturalize(str(item.get("description") or ""))),
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
        "severityBasis": mask_sensitive_text(naturalize(str(finding.get("severityBasis") or ""))),
        "confidence": finding.get("confidence"),
        "confidenceBasis": mask_sensitive_text(naturalize(str(finding.get("confidenceBasis") or ""))),
        "status": finding.get("status"),
        "primaryLocation": _safe_location(finding.get("primaryLocation")),
        "relatedLocations": [
            _safe_location(item) for item in (finding.get("relatedLocations") or [])
        ],
        "trace": _safe_trace(finding.get("trace")),
        "redactedEvidence": mask_text(str(finding.get("redactedEvidence") or "")),
        "advisoryIds": list(finding.get("advisoryIds") or []),
        "coverageWarnings": [
            mask_sensitive_text(str(item)) for item in (finding.get("coverageWarnings") or [])
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
            "rationale": mask_sensitive_text(naturalize(str(item.get("rationale") or ""))),
            "remediation": mask_sensitive_text(naturalize(str(item.get("remediation") or ""))),
            "grounding": item.get("grounding"),
        })
    observations.sort(
        key=lambda item: (
            _SEVERITY_ORDER.get(str(item.get("severity")), 9),
            str(item.get("id") or ""),
        )
    )
    coverage = result.get("coverage") if isinstance(result.get("coverage"), dict) else None
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
            limitations.append(f"Ошибка провайдера: {mask_sensitive_text(str(error))}")
    return {
        "status": result.get("status") or "disabled",
        "verified": False,
        "countsAsFindings": False,
        "limitations": limitations,
        "observations": observations,
        "provenance": result.get("provenance"),
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


def load_export_bundle(db: DatabaseRepository, run_id: str) -> ExportBundle:
    run = db.get_run(run_id)
    if not run:
        raise ReportNotFound("Запуск не найден в текущем workspace")
    findings = [_safe_finding(item) for item in db.get_findings(run_id)]
    findings.sort(key=lambda item: (
        _SEVERITY_ORDER.get(str(item.get("severity")), 9),
        str(item.get("ruleId") or ""),
        str(item.get("id") or ""),
    ))
    report = db.get_report(run_id)
    official = _official(report)
    passport = (report or {}).get("passport")
    if not isinstance(passport, dict):
        passport = official.get("securityPassport") if isinstance(official.get("securityPassport"), dict) else {}
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
        "aiAnalysis": official.get("aiAnalysis"),
        "aiScan": ai_scan.get("provenance"),
        "aiScanCoverage": ai_scan.get("coverage"),
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
        "securityPassport": bundle.passport,
        "policyResult": bundle.policy,
        "technicalProvenance": bundle.technical,
        "limitations": [
            "Отчёт собран по сохранённым результатам; повторный анализ и обращения к модели не выполнялись.",
            "Итог проверки относится к подтверждённому покрытию, а не ко всему проекту.",
            *bundle.ai_scan.get("limitations", []),
        ],
    }
    return json.dumps(document, ensure_ascii=False, indent=2, default=str).encode("utf-8")


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
    passport = bundle.passport
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
            ["Проверено файлов", passport.get("files_scanned", "—")],
            ["Строк кода", passport.get("lines_of_code", "—")],
            ["Полнота проверки", "подтверждена" if passport.get("completeness_known") else "не установлена"],
        ],
        [3.0, 3.8],
        mods,
        9.5,
    )

    reasons = bundle.policy.get("reasons") if isinstance(bundle.policy.get("reasons"), list) else []
    if reasons:
        heading = doc.add_heading("Почему такой итог", level=1)
        heading.paragraph_format.keep_with_next = True
        for reason in reasons:
            doc.add_paragraph(mask_sensitive_text(naturalize(str(reason))), style="List Bullet")
    coverage_reasons = passport.get("coverage_reasons") if isinstance(passport.get("coverage_reasons"), list) else []
    if coverage_reasons:
        heading = doc.add_heading("Что осталось за пределами проверки", level=1)
        heading.paragraph_format.keep_with_next = True
        for reason in coverage_reasons:
            doc.add_paragraph(mask_sensitive_text(naturalize(str(reason))), style="List Bullet")

    doc.add_page_break()
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
            [0.35, 0.8, 1.4, 2.45, 1.8],
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
            if finding.get("advisoryIds"):
                doc.add_paragraph("Связанные записи безопасности: " + ", ".join(finding["advisoryIds"]))
            if finding.get("redactedEvidence"):
                evidence = doc.add_paragraph()
                evidence.paragraph_format.left_indent = mods["Inches"](0.25)
                run_evidence = evidence.add_run(finding["redactedEvidence"])
                run_evidence.font.name = "Courier New"
                run_evidence.font.size = mods["Pt"](8.5)
            trace = finding.get("trace") or []
            if trace:
                trace_heading = doc.add_paragraph("Цепочка доказательств:")
                trace_heading.paragraph_format.keep_with_next = True
                trace_paragraphs = []
                for step in trace:
                    trace_paragraph = doc.add_paragraph(
                        f"{step.get('step')}. {step.get('description')}",
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
    doc.add_paragraph(
        "Это гипотезы, а не подтверждённые находки. Они не меняют итог проверки и не "
        "входят в счётчики находок. Модель видела только выбранный фрагмент проекта с удалёнными секретами."
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
            doc.add_paragraph(mask_sensitive_text(naturalize(str(limitation))), style="List Bullet")

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