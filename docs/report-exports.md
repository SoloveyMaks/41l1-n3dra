# Downloadable report exports: DOCX and JSONC

**Status:** implemented on branch `SoloveyMaks/ai-scanner-plan`; root mounts the
router. The export path is read-only over saved runtime rows: it never reruns a
scanner, never calls an LLM and never creates a second finding set.

## 1. Files

| File | Purpose |
| --- | --- |
| `backend/app/reports/exports.py` | Load one saved run, build strict JSONC and DOCX |
| `backend/app/reports/language.py` | Russian display normalization for scanner prose |
| `backend/app/reports/builder.py` | Readable Markdown cleanup; official machine JSON unchanged |
| `backend/app/api/report_exports.py` | DI router factory: two download endpoints |
| `deploy/requirements.txt` | `python-docx==1.2.0`, `lxml==6.1.3` |
| `docs/report-exports.md` | This contract |

Not touched: `api/router.py`, `main.py`, frontend, session/auth, database,
scheduler, contracts or Telegram.

## 2. Root integration contract

```python
from backend.app.api.report_exports import create_report_exports_router

api_router.include_router(create_report_exports_router(db_repo))
```

The factory adds:

| Method | Path |
| --- | --- |
| GET | `/api/runs/{run_id}/report/export.jsonc` |
| GET | `/api/runs/{run_id}/report/export.docx` |

The router does not implement authentication. Existing same-origin/session and
`workspace_scope` middleware apply. `run_id` is the only addressing input; there
is no workspace, findings or report payload from the caller. `DatabaseRepository`
is scoped by the request ContextVar, so a run in another workspace is
indistinguishable from a missing run: `404 RUN_NOT_FOUND`.

`python-docx` is imported lazily. If the image lacks it, the DOCX endpoint
returns `503 REPORT_EXPORT_UNAVAILABLE`; JSONC remains available.

## 3. Data source and parity

`load_export_bundle` reads exactly:

- `runtime.runs` — status, verdict, counts and identity;
- `runtime.findings` — saved deterministic findings;
- `runtime.reports` — stored passport and official JSON;
- `runtime.node_runs` — the latest stored `ai_scan` result.

No scanner, importer, AI provider or LLM is called during export. Findings are
sorted by severity descending: critical, high, medium, low, info; ties by rule
id and finding id. JSONC and DOCX use the same sorted list, same IDs, same
locations and same computed counts. The stored Markdown report is produced once
at run completion from the same findings; its IDs match the export.

## 4. JSONC contract

The file is strict JSON with a `.jsonc` extension (JSON is a JSONC subset; no
comments that break strict parsers).

```json
{
  "schemaVersion": "nedra-report-export/1",
  "generatedAt": "...",
  "run": {
    "id": "...", "workflowId": "...", "workflowName": "...",
    "executionStatus": "succeeded", "executionStatusLabel": "завершено",
    "securityVerdict": "fail", "securityVerdictLabel": "требуется исправление",
    "startedAt": "...", "finishedAt": "..."
  },
  "summary": {"total": 15, "critical": 8, "high": 6, "medium": 1, "low": 0, "info": 0,
               "confirmed": 14, "potential": 1, "inconclusive": 0},
  "reportAvailable": true,
  "deterministic": {
    "count": 15,
    "findings": [{
      "id": "fnd_...", "fingerprint": "...", "analyzer": "sast",
      "ruleId": "...", "severity": "critical", "confidence": "confirmed",
      "primaryLocation": {"path": "...", "startLine": 13, "endLine": 13,
                          "startColumn": 1, "endColumn": 66, "byteRange": null},
      "redactedEvidence": "...", "advisoryIds": [], "trace": []
    }]
  },
  "aiHypotheses": {
    "status": "succeeded|partial|degraded|rejected|disabled",
    "verified": false,
    "countsAsFindings": false,
    "observations": [{
      "id": "aiobs_...", "origin": "ai_hypothesis", "ruleClass": "SSRF",
      "severity": "high", "status": "potential",
      "primaryLocation": {...}, "redactedEvidence": "...",
      "rationale": "...", "remediation": "...", "grounding": {...}
    }],
    "provenance": {...},
    "coverage": {...},
    "limitations": ["..."]
  },
  "securityPassport": {...},
  "policyResult": {...},
  "technicalProvenance": {
    "exportSchemaVersion": "nedra-report-export/1",
    "aiAnalysis": {...},
    "aiScan": {...},
    "aiScanCoverage": {...}
  },
  "limitations": ["..."]
}
```

`aiHypotheses` is always separate from `deterministic`; it is never counted in
`summary` and never changes `run.securityVerdict`. Technical provider
endpoint/request id lives only in `technicalProvenance` and the DOCX appendix.

`reportAvailable=false` is a valid export for a queued/failed run with no saved
report; deterministic findings may be empty and are still exported honestly.

## 5. DOCX contract

- US Letter portrait, 0.75–0.85" margins, Calibri 10.5 pt body, black title and
  headings, no title borders.
- Russian headings and natural prose; no FAIL/PASS badges, emoji, mixed
  English/Russian headings, provider endpoint or request id in the main text.
- Sections: title and short scope; metadata; summary counts and coverage;
  reasons for the verdict; findings table sorted severity descending; detailed
  findings with ID, rule, location, risk basis, confidence, evidence and trace;
  separate AI hypotheses section; Appendix A technical provenance.
- Findings table columns: №, уровень, правило, где (path + lines), ID. The
  appendix may contain provider/model/request metadata; the main text does not.
- Every finding and AI hypothesis includes its ID and location. Tables use light
  gray borders, dark header fill with white text, alternating pale body rows,
  deliberate column widths and repeated header rows.
- AI hypotheses are labeled unverified and excluded from counts/verdict.
- The document opens with `python-docx`, LibreOffice converts it to PDF and
  `pdftotext -layout`/page PNGs are available for layout review. Word was not
  installed in this environment.

## 6. Readable Markdown cleanup

`ReportBuilder.build_readable_markdown` now emits natural Russian:

- title `Отчёт о проверке безопасности кода`, no icons or FAIL/PASS badges;
- headings `Основные показатели`, `Почему такой итог`, `Находки`,
  `Пояснения к находкам`, `Что осталось за пределами проверки`;
- Russian severity/confidence/status labels; IDs and locations unchanged;
- no provider endpoint, request id, batch/retrieval jargon in the main text;
- model explanations remain a separate short section.

The official machine JSON (`build_official_json`) is unchanged and keeps
technical provenance for machine consumers.

## 7. Live evidence

Local temp PostgreSQL, real runtime rows, ASGI app, existing session middleware.
No production, push or main was touched. `python-docx` was loaded from a local
test target; deployment pins it in `deploy/requirements.txt`.

### 15 deterministic findings

- DB `report_live_18065497`; run `b8653da2-4565-498b-a4f7-a6d65488a753`;
  workflow `report-live`; `executionStatus=succeeded`,
  `securityVerdict=fail`.
- JSONC 200: 15 findings — 8 critical, 6 high, 1 medium; IDs match the stored
  Markdown and DOCX table exactly.
- DOCX 200: 44,004 bytes, opens with python-docx; 176 paragraphs, 4 tables.
- Empty run 200: `reportAvailable=false`, 0 findings; DOCX 39,351 bytes and
  opens.
- Missing run 404; foreign-workspace run 404 `RUN_NOT_FOUND`.
- Redaction: no raw sample secrets in JSONC, Markdown or DOCX.
- Markdown checks: no FAIL/PASS icons, no `endpoint:`/`request:`, all 15 IDs
  present.
- JSONC validated by `python -m json.tool`.
- DOCX rendered with the documents skill `render_docx.py` (LibreOffice) to a
  9-page US Letter PDF and PNGs. `pdftotext -layout` shows the findings table and details without
  clipping; a vision channel was unavailable for pixel-level review.

### AI hypothesis run

- DB `tg_ai_3efd618a`, run `765ca2be-a119-4c60-84bc-8f956517c3de`:
  deterministic findings `0`, one `SSRF` AI hypothesis, `verified=false`,
  `countsAsFindings=false`; DOCX contains the separate AI section and technical
  appendix; 3 pages.
- Account ASGI test: registration created an account workspace; export of an
  account-owned empty run returned 200, while the same run requested from a demo
  session returned 404. This confirms scoped access through existing middleware.

## 8. Limitations

- Word binary is not available here; LibreOffice and python-docx opening were
  verified. Visual pixel review was limited by the unavailable vision channel;
  layout was checked through rendered PDF text and page images.
- The DOCX table wraps long finding IDs across two lines where necessary; no
  clipping was observed.
- The export is intentionally a snapshot of saved rows. A run without a saved
  report exports `reportAvailable=false` rather than fabricating a report.
- No frontend download buttons were added; root mounts the factory and the UI
  owner can link the two URLs.
- No push, merge to main, or production deployment was performed.