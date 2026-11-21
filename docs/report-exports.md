# Downloadable report exports: Markdown, DOCX and JSONC

**Status:** implemented; root mounts the router at `backend/app/api/router.py`.
The export path is read-only over saved runtime rows: it never reruns a scanner,
never calls an LLM and never creates a second finding set.

## 1. Files

| File | Purpose |
| --- | --- |
| `backend/app/reports/exports.py` | Load one saved run, build readable Markdown, strict JSONC and DOCX |
| `backend/app/reports/language.py` | Russian display normalization and one coverage label for all formats |
| `backend/app/reports/builder.py` | Readable Markdown cleanup; official machine JSON unchanged |
| `backend/app/api/report_exports.py` | DI router factory: three download endpoints |
| `frontend/src/components/ReportView.tsx` | DOCX/JSONC buttons and Markdown links in the report screen |
| `deploy/requirements.txt` | `python-docx==1.2.0`, `lxml==6.1.3` |
| `docs/report-exports.md` | This contract |

Not touched: `api/router.py`, `main.py`, session/auth, database, scheduler,
contracts, `App.tsx` or Telegram.

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
| GET | `/api/runs/{run_id}/report/export.md` |

The router does not implement authentication. Existing same-origin/session and
`workspace_scope` middleware apply. `run_id` is the only addressing input; there
is no workspace, findings or report payload from the caller. `DatabaseRepository`
is scoped by the request ContextVar, so a run in another workspace is
indistinguishable from a missing run: `404 RUN_NOT_FOUND`.

`python-docx` is imported lazily. If the image lacks it, the DOCX endpoint
returns `503 REPORT_EXPORT_UNAVAILABLE`; JSONC remains available.

## 3. Data source and parity

`load_export_bundle` reads exactly:

- `runtime.runs` — status, verdict, identity and timestamps;
- `runtime.reports` — stored passport, policy and official JSON;
- `runtime.findings` — saved deterministic findings, used when the official
  JSON has no frozen `findings` list;
- `runtime.node_runs` — the latest stored `ai_scan` result.

When a saved report exists, its official `findings` and `securityPassport` win:
a report freezes its finding set, including an intentionally empty list. A
legacy report without those keys, or with a `findings` value that is not a
list (`null`, a string, an object), falls back to `runtime.findings` and the
stored passport. Markdown takes AI enrichment only from that same validated
list, so a malformed field cannot crash one format while the others render;
AI enrichment never edits, adds or removes a deterministic finding:
`ai_enrichment` stays in its official slot and the Markdown AI section reads
`aiHypotheses`/`ai_scan` separately.

No scanner, importer, AI provider or LLM is called during export. Findings are
sorted by severity descending: critical, high, medium, low, info; ties by rule
id and finding id. JSONC, Markdown and DOCX are three renderings of that one
bundle: the same IDs in the same order, the same severity/status values, and
counts computed from the same list. Coverage wording comes from the same
`completeness_known`/`coverage_status` pair through one helper, so all three
say «подтверждена», «не установлена», «недоступна» or «неизвестна» alike.

A legacy passport can carry `completeness_known=true` next to `partial`,
`unknown` or `unavailable`. That contradiction never renders as «подтверждена»:
both readable formats downgrade the label to «не установлена»/«неизвестна»/
«недоступна» and add an explicit warning that the completeness is not
established. JSONC keeps the canonical passport unchanged and repeats the same
warning in `limitations`.

Before JSONC/DOCX rendering, technical provenance is sanitized recursively at
the export boundary: URL userinfo becomes `***@host`, credential values in path
segments (`/token/<secret>`, `/api/key/<secret>`, `;api_key=<secret>`,
`key=<secret>`/`key:<secret>`) and in query parameters (`token`, `api_key`,
`sig`, `X-Amz-Signature`, …) become `***`, and secret-named fields (`apiKey`,
`Authorization`, `client_secret`, …) become `[REDACTED]`. Known token formats
(`sk-…`, `AKIA…`, `ghp_…`) are masked even without a marker. Hosts, safe paths,
models, versions, request ids, non-secret query parameters and legitimate token
counts (`promptTokens`, `tokenCount`, `max_tokens`, …) are preserved. This is a
display/export copy: the stored official JSON is not rewritten, and no export
path logs the raw values.

Free-form error strings are treated as references too. A provider error such as
`request /token/<secret>?token=<secret>` has no path-like field name, so
`_sanitize_prose` also scans scheme-less references inside the string: an
absolute path (`/token/<secret>`, `/api/key/<secret>`, matrix params) and a
bare `host.tld/token/<secret>`. They go through the same `_sanitize_reference`
path/query redaction, while ordinary prose (`key/value`, `token/refresh`,
`/tmp/build/log.txt`, `и/или`) stays unchanged. Because the same `_sanitize_text`
now runs on `providerErrors` limitations, finding basis/warnings/trace text, AI
rationale/remediation/grounding, the official `aiAnalysis.summary` and
`ai_enrichment` used by readable Markdown, and the export `limitations`, no
format can print a credential path that another format hides.

### Inherited stored-report endpoints

The same redaction is *not* applied by the inherited
`GET /api/runs/{id}/report/official.json` and `GET /api/runs/{id}/report/readable.md`
handlers in `backend/app/api/router.py`. They still serve the stored
`official_json` and `readable_markdown` through `_remask_official_json` /
`_remask_report_record`, which mask only selected text fields (evidence, AI
summary/items, readable body) with `mask_sensitive_text`. `official.json` still
prints raw provider provenance (URL userinfo, query credentials, signature) and
a scheme-less `/token/<secret>` it carries in `aiAnalysis.summary`; `readable.md`
keeps a scheme-less `/token/<secret>` from the stored body. Both were measured
with fake sentinels against this branch. That router is outside this export task
and is not edited here; the export router must not be mistaken for a fix of those
two endpoints. Closing it is a router-side change: apply `_sanitize_provenance`
to `aiAnalysis`/`securityPassport`/`policyResult` and `_sanitize_text` to
`readable_markdown` inside `_remask_report_record`, or mount the export builders
instead of serving stored text.

When a saved passport has no measurement key (`files_scanned`, `lines_of_code`,
`parser_errors`, …), readable exports say «неизвестно» and the DOCX summary
uses «—». An explicitly saved `0` stays a measured zero; absence is never
presented as zero.

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
endpoint/request id lives only in `technicalProvenance` and the DOCX appendix;
both are emitted after recursive credential sanitization. `securityPassport`
and `policyResult` stay canonical machine data, while `limitations` carries the
contradictory-coverage warning when a saved passport claims completeness next
to a non-complete status.

`reportAvailable=false` is a valid export for a queued/failed run with no saved
report; deterministic findings may be empty and are still exported honestly.

## 5. DOCX contract

- US Letter portrait, 0.75–0.85" margins, Calibri 10.5 pt body, black title and
  headings, no title borders (`w:pBdr` explicitly removed from the Title style
  and title paragraph).
- Russian headings and natural prose; no FAIL/PASS badges, emoji, mixed
  English/Russian headings, provider endpoint or request id in the main text.
- Sections: title and short scope; metadata; summary counts and coverage;
  `Что проверено` with scanned/skipped files and executed rules; reasons for the
  verdict; `Что осталось за пределами проверки` and coverage warnings; findings
  table sorted severity descending; detailed findings with ID, rule, location,
  risk basis, confidence, evidence status, evidence, trace and per-finding
  restrictions; separate AI hypotheses section; Appendix A technical provenance.
- Findings table columns: №, уровень, правило, где (path + lines), ID. The
  appendix may contain provider/model/request metadata; the main text does not.
  Every table has an explicit `w:tblGrid`, fixed layout and total width 9792
  twips (6.8"). Findings widths: 0.35/0.95/1.35/2.35/1.8 in; the № column is not
  allowed to grow to 20%. `информационный` may wrap inside its cell but is never
  clipped.
- Every finding and AI hypothesis includes its ID and location. Tables use light
  gray borders, dark header fill with white text, alternating pale body rows,
  deliberate column widths and repeated header rows.
- Finding and AI detail headings set `keep_with_next` through ID, rule and
  location, so a heading is not stranded at the bottom of a page; long bodies
  may still continue on the next page.
- A short trace (up to four bullets) keeps its heading and bullets together;
  longer traces keep only the heading/first bullet together. Neither the AI
  section nor `Находки` starts with a forced page break, so a short coverage
  warning block does not leave a page holding one bullet.
- Coverage wording is shared with Markdown: «подтверждена», «не установлена»,
  «недоступна», «неизвестна»; DOCX never prints `100%` for a `partial` run.
  A contradictory `completeness_known=true` is downgraded and the warning is
  listed under `Предупреждения по покрытию`.
- Missing passport measurements print «—» in the summary and «неизвестно» in
  `Что проверено`; a saved `0` is kept. An absent measurement is never rendered
  as a measured zero.
- Appendix A is built from the sanitized technical copy: userinfo and secret
  query values are masked, secret-named fields are `[REDACTED]`, and a
  scheme-less `/token/<secret>` inside a provider error string is masked too,
  while safe provider/model/request metadata remains visible.
- Display headings use «ИИ», while machine JSON fields remain `aiHypotheses`,
  `aiScan`, `aiAnalysis` and `provenance`.
- AI hypotheses are labeled unverified and excluded from counts/verdict; the
  section states the scan status in Russian.
- The document opens with `python-docx`, LibreOffice converts it to PDF and
  `pdftotext -layout`/page PNGs are available for layout review. Word was not
  installed in this environment.

## 6. Readable Markdown

`ReportBuilder.build_readable_markdown` produces both the stored run report and
`GET /report/export.md`. Both paths pass the saved `ai_scan` result, so the
«Гипотезы ИИ-сканера (не подтверждены)» section with status/coverage is built
once instead of appended by the export. It emits natural Russian:

- title `Отчёт о проверке безопасности кода`, no icons or FAIL/PASS badges;
- headings `Основные показатели`, `Что проверено`,
  `Что осталось за пределами проверки`, `Почему такой итог`,
  `Гипотезы ИИ-сканера (не подтверждены)`, `Находки`,
  `Пояснения к находкам`;
- one summary block per run: severities, evidence statuses, files scanned and
  skipped, executed rules, parser errors and the single coverage label;
- Russian severity/confidence/status labels; IDs and locations unchanged;
- every finding carries rule, location, importance, confidence, evidence
  status, masked evidence, trace locations and restrictions;
- `naturalize` protects URLs, CVE/GHSA/KOTLIN/SECRET/SCA/CF IDs, `fnd_*`/
  `aiobs_*` IDs and backticked code from prose replacements;
- no provider endpoint, request id, batch/retrieval jargon in the main text;
- intro is scope + verdict (`Проверен сценарий «…». Итог: …`) rather than a
  technical note about repeated scans/LLM calls;
- model explanations remain a separate short section;
- evidence fences widen when the snippet itself contains backticks, so a code
  sample cannot break the document;
- no technical provenance (provider endpoint, request id) is emitted; the
  machine copy lives only in JSONC and the DOCX appendix;
- a saved passport without measurement keys renders «неизвестно» for those
  rows instead of the model default `0`; a contradictory
  `completeness_known=true` adds the same warning and never says «подтверждена»;
- `reportAvailable=false` adds an explicit line that no saved report exists.

The official machine JSON (`build_official_json`) is unchanged and keeps
technical provenance for machine consumers.

## 7. Live evidence

Local temp PostgreSQL, real runtime rows, ASGI app, existing session middleware.
No production, push or main was touched. `python-docx` was loaded from a local
test target; deployment pins it in `deploy/requirements.txt`.

### 15 deterministic findings

- DB `report_live_5ec7d41d`; run `e09bfa17-db6b-438f-a284-f8af09c03369`;
  workflow `report-live`; `executionStatus=succeeded`,
  `securityVerdict=fail`.
- JSONC 200: 15 findings — 8 critical, 6 high, 1 medium; IDs match the stored
  Markdown and DOCX table exactly.
- DOCX 200: 44,050 bytes, opens with python-docx; 176 paragraphs, 4 tables.
- Empty run 200: `reportAvailable=false`, 0 findings; DOCX 39,365 bytes and
  opens.
- Missing run 404; foreign-workspace run 404 `RUN_NOT_FOUND`.
- Redaction: no raw sample secrets in JSONC, Markdown or DOCX.
- Source URL parity: `https://github.com/advisories/...` and
  `https://osv.dev/vulnerability/...` remain byte-identical in JSONC/Markdown/DOCX;
  prose normalization protects them from `advisories`/`scopes` replacements.
- Markdown checks: no FAIL/PASS icons, no `endpoint:`/`request:`, all 15 IDs
  present.
- JSONC validated by `python -m json.tool`.
- DOCX rendered with the documents skill `render_docx.py` (LibreOffice) to an
  8-page US Letter PDF and PNGs. `pdftotext -layout` shows the findings table and details without
  clipping; a vision channel was unavailable for pixel-level review.

### AI hypothesis run

- DB `tg_ai_3efd618a`, run `765ca2be-a119-4c60-84bc-8f956517c3de`:
  deterministic findings `0`, one `SSRF` AI hypothesis, `verified=false`,
  `countsAsFindings=false`; DOCX contains the separate AI section and technical
  appendix; 3 pages.
- Account ASGI test: registration created an account workspace; export of an
  account-owned empty run returned 200, while the same run requested from a demo
  session returned 404. This confirms scoped access through existing middleware.

### 2026-11-21 integration run (Markdown endpoint, frozen bundle)

- Branch `SoloveyMaks/reports-mentor-finish` from dev `d23a68d`; temp
  PostgreSQL 17 container on `127.0.0.1:55439`, `python-docx 1.2.0`.
- Run `run-live-main`: 4 deterministic findings (1 critical, 1 high, 1 medium,
  1 info; 2 confirmed, 1 potential, 1 inconclusive), one `aiobs_*` SSRF
  hypothesis, coverage `partial` with three skipped scopes.
- Same IDs in the same order in JSONC, Markdown and DOCX; severity wording in
  Markdown/DOCX matches the JSONC severity value; coverage label «не установлена»
  in both prose formats and `coverage_status=partial` in JSONC.
- HTTP: `export.jsonc`, `export.docx`, `export.md` all 200 with the expected
  media type and `nedra-<run>.{jsonc,docx,md}` attachment name; missing run 404
  `RUN_NOT_FOUND`; in-process and HTTP JSONC payloads equal apart from
  `generatedAt`.
- Legacy report without official `findings`/`securityPassport`: export falls
  back to `runtime.findings` and still matches across formats.
- Queued run without a report: `reportAvailable=false`, 0 findings, explicit
  «Сохранённый отчёт не сформирован» line, coverage «неизвестна», no `100%`.
- Redaction: the AWS documentation sample key is masked in all three formats.
- DOCX rendered by LibreOffice to a 4-page US Letter PDF; `pdftotext -layout`
  shows the summary, `Что проверено`, coverage reasons, findings table and
  details, AI section and appendix without clipping or a one-bullet page.
- `ReportView.tsx` typechecks (no errors in that file; the two `ui/security-map`
  errors seen with the shared `node_modules` reproduce on the base worktree) and
  bundles with `rolldown`; its DOCX/JSONC buttons and both Markdown links point
  at the three endpoints above.

### 2026-11-21 inherited-risk fix (provenance, coverage, no-report counts)

- Disposable PostgreSQL 16.16 container, `network=none`, Unix socket; real
  `runtime.*` rows through `DatabaseRepository`, no production/stage services.
- Direct bundle checks: 58/58. Parity cases (`canonical`, empty canonical,
  `findings=null`, string findings, no report) produced identical IDs in the
  same order in JSONC, Markdown and DOCX; empty canonical stayed 0 and malformed
  findings fell back to runtime rows.
- Contradictory legacy passport: `completeness_known=true` with `partial`,
  `unknown` and `unavailable` rendered «не установлена», «неизвестна» and
  «недоступна»; the contradiction warning appeared in Markdown, DOCX and JSONC
  `limitations`, never «подтверждена». `complete` with `true` still rendered
  «подтверждена».
- No-report run: `reportAvailable=false`; Markdown and DOCX said
  «Просканировано файлов: неизвестно; пропущено: неизвестно.», DOCX summary used
  «—»; an explicit saved `0` was not rewritten.
- Fake sentinel credentials (`FAKE_*`): none appeared in JSONC, Markdown or DOCX.
  URL userinfo became `***@provider.invalid`; path values became `/token/***`
  and `/api/key/***`, matrix params `;api_key=***`; query values became `***`
  for `api_key` and `X-Amz-Signature`; secret fields became `[REDACTED]`.
  `model=safe-model`, `temperature=0`, `max_tokens=1000`, `requestId=req-safe-1`,
  `promptTokens`/`completionTokens`/`totalTokens`/`tokenCount` and provider/
  version fields survived. A malformed port next to userinfo was still redacted
  without an exception.
- In-process ASGI over the same rows: 61/61 — `md`/`docx`/`jsonc` returned 200
  with `attachment` and `no-store` for parity, empty, null, sanitize,
  contradictory and no-report runs; JSONC parsed; foreign Referer still 403.

## 8. Limitations

- Word binary is not available here; LibreOffice and python-docx opening were
  verified. Visual pixel review was limited by the unavailable vision channel;
  layout was checked through rendered PDF text and page images.
- The DOCX table wraps long finding IDs across two lines where necessary; no
  clipping was observed.
- Provenance sanitation is heuristic: it masks URL userinfo, credential-named
  path/query values and secret-named fields. An opaque secret stored under an
  arbitrary benign key without a marker or known token format is not detectable
  from the value alone.
- The export is intentionally a snapshot of saved rows. A run without a saved
  report exports `reportAvailable=false` rather than fabricating a report.
- Markdown is generated on request from the same bundle; the stored
  `readable_markdown` row keeps the run-time wording until the next run.
- No push, merge to main, or production deployment was performed.