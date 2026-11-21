# AI vulnerability scanner: implementation

**Status:** implemented and live-checked on branch `SoloveyMaks/ai-runtime-fix`
(baseline `d23a68d`). The system default workflow now includes `AI_SCAN` as an
isolated side branch; existing user workflows are not rewritten silently (see
section 10). This document describes only the `AI_SCAN` block; it does not
change canonical SECRET/SCA/SAST findings, policy, verdict or comparison.

## 1. What was added

| File | Change |
| --- | --- |
| `backend/app/contracts/schemas.py` | `PortType.AI_SCAN_RESULT`, `NodeKind.AI_SCAN`, `AiScanResult`, `AiObservation`, `AiObservationGrounding`, `AiScanCoverage`, `AiScanProvenance` |
| `backend/app/contracts/node_registry.py` | `ai_scan` node: `artifact` (required), `findings_bundle` (optional), `ai_scan_result` output, budget-only defaults |
| `backend/app/engine/validator.py` | requires `artifact`; validates AI_SCAN config; rejects provider/model/key fields; keeps `ai_scan` out of deterministic scanner kinds |
| `backend/app/engine/executor.py` | executes `AI_SCAN`, emits `ai_scan_result` SSE, stores the node output, treats AI node failure as non-fatal |
| `backend/app/ai/scan_selection.py` | frozen-snapshot selection, redaction, budgets, stable `fileRef` map |
| `backend/app/ai/scan_prompt.py` | versioned prompt and closed rule-class set |
| `backend/app/ai/scan_validator.py` | strict quote/line/ref grounding, server byte bounds, CVE/severity bans, dedupe |
| `backend/app/ai/scan_service.py` | one bounded ReformBoss request, validation, provenance, degraded paths |
| `backend/app/demo/default_workflow.py` | default draft includes `node_ai_scan` (artifact + findings for dedupe) and one advisory edge to the report's dedicated AI input; no edge into policy/canonical findings; frozen pre-AI_SCAN baseline for safe upgrades |
| `backend/app/demo/default_workflow_upgrade.py` | explicit revision-preserving upgrade/CLI for existing production defaults; user-modified drafts are reported, not overwritten |
| `docs/ai-scanner-implementation.md` | this document |

Not touched: frontend, `router.py`, `session.py`, `main.py`, `storage/*`,
`codefacts/identity.py`, `reports/*`, DB migrations, canonical bundle schema.

## 2. Node and port contract

```text
kind:    ai_scan
inputs:  artifact         ARTIFACT          required
         findings_bundle  FINDINGS_BUNDLE   optional (dedupe context)
outputs: ai_scan_result   AI_SCAN_RESULT
```

Default config:

```json
{"profile":"balanced","maxFiles":6,"maxBytes":24576,"perFileBytes":4096,"maxObservations":12}
```

Allowed config keys: `profile` (`fast|balanced|deep`), `maxFiles`, `maxBytes`,
`perFileBytes`, `maxObservations`. The validator rejects `model`, `apiKey`,
`api_key`, `token`, `secret`, `password`, `baseUrl`, `endpoint`, `prompt`.
Provider, model, base URL and credentials come only from the server process
environment; nothing secret is read from or written to block JSON.

`findings_bundle` is optional. When connected, deterministic findings are used
only by the server-side validator for duplicate suppression; they are not sent
to the model. This keeps the model focused on additional issues and avoids
output blow-up.

## 3. Input selection and privacy

`AI_SCAN` runs in the system default workflow. Its only outgoing edge goes to
`BUILD_REPORT.ai_scan_result`, the report's dedicated advisory input: the
persisted Markdown and official JSON show status/coverage/hypotheses, while
canonical findings, policy counts and the verdict never consume the result.
Adding it to the default did not rewrite existing user workflows; see section
10 for the explicit upgrade and surgical insertion.

1. The executor resolves the same frozen artifact as deterministic scanners;
   `load_snapshot_sources` verifies every snapshot path and SHA-256 before
   reading. Integrity checks were not relaxed.
2. Selection is limited to text source/config files from the snapshot:
   `.kt`, `.kts`, `.java`, `.yml`, `.yaml`, `.properties`, `.xml`, `.toml`.
   `.env`, `.pem`, `.key`, `.crt`, `.cer`, `.p12`, `.jks` and test/generated
   paths are skipped. Files above the per-file budget are not sent.
3. Ranking is deterministic: deterministic finding paths first (severity order),
   then runtime config and risky source names, then other source.
4. Excerpts are redacted with the project’s existing maskers:
   `mask_sensitive_text` globally (PEM, bearer, assignments) and `mask_text`
   per bounded chunk (URI credentials, long tokens, env defaults, control
   chars). The global 600-char `mask_text` truncation is not changed; chunking
   preserves redaction without cutting the excerpt at 600 chars.
5. Each excerpt gets an opaque `fileRef` (`sf_1`, …). The model never receives a
   database path, byte offset, finding ID or advisory ID.

Coverage records `filesTotal`, selected/sent counts, byte counts, truncated
files, omitted files (bounded sample), failures and `providerErrors`. It is
stored in `AiScanResult.coverage`; it is never merged into
`FindingsBundle.coverage` or the Security Passport. An excerpt capped at 220
lines, or centered on a deterministic finding without covering the rest of the
file, is listed in `truncatedFiles` and makes coverage `partial`; `truncated`
on the selected file is true as well.

**Data egress:** the block being present is the authorization to send the
redacted excerpts to ReformBoss. The default workflow now contains it, so a
default run makes one bounded external call. The excerpts are bounded
(default ≤24 KiB selected) and redacted, but redaction is best-effort, not a
proof that every credential is gone. A workspace that must not call an external
provider can remove the block or run without `REFORMBOSS_API_KEY`; the block
then reports `degraded` and the deterministic result is unchanged.

## 4. Model output and grounding

The model receives a JSON payload with `files[]` (`fileRef`, project-relative
`path`, `startLine`, `endLine`, `excerpt`) and must return:

```json
{"observations":[{
  "fileRef":"sf_1",
  "ruleClass":"SSRF",
  "severity":"high",
  "startLine":10,
  "endLine":11,
  "quote":"exact code from those lines",
  "rationale":"short reason",
  "remediation":"short fix"
}]}
```

Closed `ruleClass` set: `SQL_INJECTION`, `PATH_TRAVERSAL`,
`COMMAND_INJECTION`, `IDOR`, `SSRF`, `CRYPTO`, `TLS`, `CORS`,
`OPEN_ENDPOINT`. New classes require a prompt/validator version bump and a
dedupe mapping.

Validator rules (`backend/app/ai/scan_validator.py`):

- `fileRef` must be in the current selection; unknown refs → `AI_REF_UNKNOWN`.
- `startLine`/`endLine` must be integers inside the sent excerpt, ordered.
- `quote` is stripped, must be exact source text, unique in the declared line
  window, and ≤400 chars. No fuzzy matching. CRLF in the frozen file and in the
  model output is normalized to LF for the comparison; `byteRange` is still
  recomputed against the raw bytes.
- Columns and `byteRange` are recomputed from the frozen source by the server;
  model offsets are never used. For a single-line quote the exclusive end column
  is `startColumn + len(quote)`, not `len(quote) + 1`, so a quote that starts
  mid-line slices exactly the quoted bytes. Multiline quotes still end on the
  last line's column.
- Any `CVE-...` in quote/rationale/remediation → `AI_CVE_FORBIDDEN`.
- `severity` must be `low|medium|high`; `critical` → `AI_SEVERITY_FORBIDDEN`.
- `confidence`/`status` are forced to `potential`; `origin` is
  `ai_hypothesis`.
- Deterministic duplicate: same path, overlapping line ranges and a mapped
  deterministic rule (`SQL`, path, command, IDOR, open endpoint) is suppressed,
  not reported as additional.
- Duplicate AI fingerprints in one response are suppressed.
- Model IDs/paths/advisories are ignored; IDs are server-generated:
  `aiobs_<uuid5(workspace, run, ai-fp/v1)>`.
- Rationale/remediation are masked and capped at 1200 chars.

AI observations never become canonical findings: they are not inserted into
`runtime.findings`, are not in `FindingsBundle`, have no `fp/v1` fingerprint and
do not affect policy counts or the verdict.

## 5. Provider behavior and degradation

`AiScanService` uses the existing ReformBoss OpenAI-compatible client
(`backend/app/ai/provider.py`), default model `deepseek-v4.1-flash-1017`.
One request per node execution, `temperature=0`, timeout from
`AI_TIMEOUT_SECONDS`, at most 4096 output tokens, no retries.

| Situation | Result |
| --- | --- |
| No `REFORMBOSS_API_KEY` | `status=degraded`, `AI_PROVIDER_NOT_CONFIGURED`, no observations |
| Timeout/HTTP/unreachable | `status=degraded`, masked `providerErrors`, request id/latency if present |
| `finish_reason=length` | `status=degraded`, `AI_PROVIDER_ERROR`, no partial findings claimed |
| Malformed provider JSON / empty content | `status=degraded`, masked `AI_PROVIDER_ERROR`; no observations claimed |
| Valid JSON, invalid shape (`observations` not a list) | `status=rejected`, validation errors |
| Some observations rejected | `status=partial`, accepted grounded observations only |
| Valid response, no issues | `status=succeeded` (or `partial` if selection was truncated/omitted) |
| Frozen source hash mismatch | `status=degraded`, `AI_SOURCE_INTEGRITY`; no provider call |
| No selectable source | `status=degraded`, `AI_NO_SELECTED_SOURCE` |

`AiScanResult.status` and `coverage.status` are honest about limits:
`complete` means complete for the selected subset, never full-project coverage.

`provenance.configVersion` hashes every output-changing request setting:
prompt/validator/selection versions, provider/model, the full non-secret request
route (credentials, query and fragment stripped), effective `maxTokens`,
`temperature` and the `AI_JSON_MODE` flag.

The executor catches all AI_SCAN exceptions into a degraded result, and
`analysis_failed` excludes `AI_SCAN` in addition to `NOTIFY`. A provider outage
therefore cannot turn a successful deterministic run into a failed run.

## 6. API / DTO contract for frontend

Frontend is owned by another worker; this is the contract to wire.

**Node kind:** `ai_scan`; **output port/type:** `ai_scan_result` /
`ai_scan_result`.

**Run node output:**

```http
GET /api/runs/{run_id}/nodes
```

Find the node with `kind == "ai_scan"` and read
`output_data_masked.ai_scan_result`. Shape:

```json
{
  "status": "succeeded|partial|degraded|rejected",
  "isHypothesis": true,
  "countsAsFindings": false,
  "observations": [{
    "id": "aiobs_<uuid>",
    "origin": "ai_hypothesis",
    "ruleClass": "SSRF",
    "severity": "high",
    "confidence": "potential",
    "confidenceBasis": "AI hypothesis; not confirmed by deterministic scanners",
    "status": "potential",
    "primaryLocation": {
      "path": "src/main/kotlin/.../ProxyController.kt",
      "startLine": 10, "endLine": 11,
      "startColumn": 9, "endColumn": 49,
      "byteRange": [219, 332]
    },
    "redactedEvidence": "masked quote",
    "rationale": "short masked reason",
    "remediation": "short masked fix",
    "grounding": {
      "fileRef": "sf_1",
      "sourceHash": "<sha256>",
      "quoteSha256": "<sha256>",
      "startByte": 219, "endByte": 332,
      "modelStartLine": 10, "modelEndLine": 11
    }
  }],
  "suppressedDuplicates": 0,
  "validationErrors": [],
  "coverage": {
    "status": "complete|partial|degraded|rejected",
    "filesTotal": 3, "filesSelected": 3, "filesSent": 3,
    "bytesSelected": 1800, "bytesSent": 1800,
    "maxFiles": 6, "maxBytes": 24576, "maxObservations": 12,
    "truncatedFiles": [], "omittedCount": 0, "omitted": [],
    "failedCount": 0, "failed": [],
    "limitsReached": [], "providerErrors": []
  },
  "provenance": {
    "provider": "reformboss",
    "model": "deepseek-v4.1-flash-1017",
    "endpoint": "https://api.reformboss.com",
    "promptVersion": "ai-scan-prompt/1",
    "validatorVersion": "ai-scan-validator/1",
    "selectionVersion": "ai-scan-selection/1",
    "configVersion": "<16 hex>",
    "requestId": "...",
    "latencyMs": 2943.3,
    "usage": {"prompt": 826, "completion": 360, "total": 1186},
    "finishReason": "stop",
    "responseSha256": "<sha256>",
    "fallbackReason": null
  }
}
```

**SSE event:** `ai_scan_result` with
`{status, observations, suppressedDuplicates, coverageStatus, countsAsFindings}`.

**Important UI rules:**

- Render AI observations in a separate “AI hypotheses” section; never merge
  them into deterministic findings, passport counts, verdict, policy reasons or
  notify summary.
- Label `status=potential`, `origin=ai_hypothesis`, and show
  `countsAsFindings=false`.
- Do not call `/runs/{run_id}/evidence/{id}` for `aiobs_*`; that route reads
  canonical evidence. Render `redactedEvidence` from the observation.
- `GET /runs/{run_id}/findings` contains only deterministic rows; live runs
  below confirm no `aiobs_*` rows there.
- If `provenance.usage` values are null, the provider did not return usage;
  do not invent token counts.

## 7. Live evidence (2026-11-21)

All runs used local ephemeral PostgreSQL databases and `CG_DATA_DIR` temp
directories. Credentials stayed in the process environment and were never
printed. No production service, push or main branch was touched.

### 7.1 Real workflow, real provider, additional SSRF

- DB `ai_scan_ssrf_d3138047`, run `c3008aa2-9af3-4b03-96ad-f07c2a63a57a`,
  revision `rev_wf_ai_ssrf_d3138047_1`.
- Artifact: synthetic Kotlin `ProxyController` with `URL(userInput).openConnection()`.
- `execution_status=succeeded`, AI node `succeeded`, AI scan `succeeded`.
- One observation: `ruleClass=SSRF`, severity `high`, lines `10-11`, bytes
  `219-332`, `origin=ai_hypothesis`, `status=potential`.
- Deterministic findings: `0`; no `aiobs_*` in `/runs/{id}/findings`.
- Coverage `complete`; provenance model `deepseek-v4.1-flash-1017`,
  `usage={prompt:826, completion:360, total:1186}`, `finish_reason=stop`.

### 7.2 Real workflow, no provider key

- DB `ai_scan_live_ceb6e776`, run `2c7bf85f-f754-4348-bac9-f74bdb1d25ef`.
- `execution_status=succeeded`, verdict `fail`, deterministic findings `15`
  across `sast/sca/secret`.
- AI scan `degraded`, `validationErrors=["AI_PROVIDER_NOT_CONFIGURED"]`,
  observations `0`, no `aiobs_*` in deterministic findings.

### 7.3 Real workflow, real provider, deterministic dedupe

- DB `ai_scan_live_8b929395`, run `e0dc9f7f-a3e9-48cc-a768-d4f4f20137e1`.
- `execution_status=succeeded`, verdict `fail`, deterministic findings `15`.
- AI scan `partial`, observations `0`, `suppressedDuplicates=2` (two model
  observations matched deterministic rules and were not claimed as new),
  coverage `partial` due one omitted file.

### 7.4 Service-level real provider

- Sample project, no deterministic context: AI scan `partial`, two grounded
  observations (`SQL_INJECTION`, `PATH_TRAVERSAL`), no validation errors,
  `finish_reason=stop`, request id `9824c8b2faf24d03a17587abe69562b2`.
- Synthetic SSRF project: one grounded `SSRF`, `status=succeeded`,
  `finish_reason=stop`.

### 7.5 Rejections and failures

- Fabricated evidence fixture: `fileRef=../etc/passwd`, `quote=NOT IN FILE`,
  line `99` outside excerpt. Result: zero accepted observations,
  `AI_REF_UNKNOWN`, `AI_QUOTE_NOT_FOUND`, `AI_LINE_OUTSIDE_EXCERPT`.
- Unreachable provider (`AI_BASE_URL=http://127.0.0.1:9`): `status=degraded`,
  `providerErrors=["provider unreachable: ..."]`, zero observations.
- Truncated provider output during an earlier prompt version:
  `finish_reason=length` → `degraded`, execution still `succeeded`, 15
  deterministic findings intact. The final prompt/limit produced `stop` in the
  runs above.

### 7.6 Default workflow with AI_SCAN (2026-11-21)

- DB `ai_scan_default_live1`, workspace
  `22222222-3333-4444-8555-666666666666`, sample `sample_bank_app.zip`.
- Run `c4084441-914f-44f5-891a-6aeb013beae0`: `execution_status=succeeded`,
  verdict `fail`, deterministic findings `15` (8 critical, 6 high, 1 medium).
- AI node `succeeded`; AI scan `partial`: one grounded `SQL_INJECTION`
  hypothesis at `src/main/kotlin/com/bank/controller/UserController.kt:13`,
  `origin=ai_hypothesis`, `status=potential`, `countsAsFindings=false`;
  `suppressedDuplicates=1`; coverage `partial` because one of 8 files was
  omitted by the 6-file budget; `bytesSent=3016`.
- Provenance: `deepseek-v4.1-flash-1017`,
  `usage={prompt:1685, completion:1766, total:3451}`, `finish_reason=stop`,
  request id `8d25d5cf27ea476fb39295d032083c39`.
- Canonical separation: zero `aiobs_*` rows in `runtime.findings`; the 15 rows
  and policy counts are unchanged by the AI block.
- Same DB, unreachable provider (`AI_BASE_URL=http://127.0.0.1:9`), run
  `2b324b07-31a4-4146-b26e-29d5deb8d0f6`: execution `succeeded`, verdict
  `fail`, 15 findings intact, AI scan `degraded` with a masked
  `AI_PROVIDER_ERROR`, zero observations.
- Final code after CRLF/configVersion fixes, DB `ai_scan_default_final1`
  (workspace `33333333-4444-4555-8666-777777777777`): upgrade CLI moved
  `wf_secure_pr_gate` from `rev_..._1` to `rev_..._2`, the stored revision
  validated, and run `46869bd8-dc91-4ee0-a974-3b2f9e0173e5` returned
  `execution_status=succeeded`, verdict `fail`, 15 findings, AI scan `partial`
  with 0 new observations and 2 deterministic duplicates suppressed
  (`configVersion=5f2ec7ad11aac999`, `finish_reason=stop`). The JSONC export
  kept `deterministic.count=15` and `aiHypotheses.countsAsFindings=false` with
  no overlapping IDs.
- No-key service check (`HOME` without the agent env, no
  `REFORMBOSS_API_KEY`): `status=degraded`,
  `validationErrors=["AI_PROVIDER_NOT_CONFIGURED"]`, provenance provider
  `none`.
- CRLF grounding regression check: a two-line LF quote and the same CRLF quote
  both ground to the raw byte range `[15, 43]`; before the fix the LF quote was
  rejected with `AI_QUOTE_NOT_FOUND`.

### 7.7 Race, dry-run and report verification (2026-11-21)

- Re-ran the independent PG probe (`/tmp/ai-exact-c84-review.py`) on a
  disposable database. Nominal upgrade: dry-run twice `would_upgrade` with one
  revision, apply `upgraded` to two revisions, apply again `already_current`.
- Race with a fresh user edit after the upgrade read: `user_modified`,
  `freshEditPreserved=true`, binding `art_new_user_selection`, 2 revisions; the
  upgrade did not write. Two concurrent upgrades returned
  `['already_current', 'upgraded']` with exactly 2 revisions.
- Dry-run against a fresh workspace created 0 workspace rows (`0 -> 0`) and
  exited 0. Dry-run on a database with no runtime schema returned exit 2 and
  left `data_contract`/`runtime` schemas absent, proving no `init_db` side
  effect. Missing workspace and non-UUID workspace are explicit errors.
- Report path: no-key and unreachable runs kept 15 deterministic findings and
  zero `aiobs_*` canonical rows; stored `official_json` now contains `aiScan`,
  stored Markdown contains `ИИ-сканер`, and JSONC keeps
  `aiHypotheses.countsAsFindings=false`.
- Local HTTP fixture: one grounded SSRF hypothesis, `officialHasAI=true`,
  export observations 1; fabricated quote gave `rejected`,
  `AI_QUOTE_NOT_FOUND`, 0 observations. No false clean.
- Surgical insertion on a prod-like modified draft (custom scanner configs,
  positions, description, `art_fbe70934d1e3`): dry-run `would_add_ai_scan` with
  `+node_ai_scan` and 3 edges, apply `added_ai_scan` to a new revision,
  all 11 existing nodes/13 edges/positions/config/artifact preserved; repeat
  `already_present`. Factory `--apply` on a user-modified draft returned exit 1
  `user_modified`; `--add-ai-scan --apply` returned exit 0 and applied.
- The surgical revision executed with no provider: `succeeded`/`fail`, 15
  findings, stored `aiScan.status=degraded`, Markdown section present, zero
  `aiobs_*` canonical rows.
- Single-line grounding fixture `val prefix = URL(userUrl).readText()` now
  grounds to columns `14..37`, bytes `[13,36]`, slicing exactly
  `URL(userUrl).readText()`; CRLF multiline still slices the full quote.
- Coverage window fixture: a 300-line file selects lines 1–220,
  `truncated=true`, `truncatedFiles=['big.kt']`, coverage `partial`.
- Config identity: `/v1` vs `/v2` routes and `AI_JSON_MODE` off/on produce
  different `configVersion` values; userinfo/query/fragment are not hashed.

## 8. Limitations

- The model sees only a bounded selected subset. Zero observations does not
  mean the project is clean; `coverage.status=complete` is complete for the
  selected subset only.
- AI detection is not guaranteed. The SSRF run proves the mechanism, not
  comprehensive class coverage.
- AI observations are not canonical evidence, not comparable across runs, and
  not eligible for “fixed/introduced” claims. Model/prompt/config changes can
  change observations; `configVersion` records that.
- The stored Markdown and official JSON now include a separate advisory
  `aiScan` block; JSONC/DOCX exports read the same node run. No frontend panel
  or new router endpoint was added; the frontend consumes the existing node-run
  output/SSE contract above.
- No DB migration: the result lives in `runtime.node_runs.output_data_masked`
  and the SSE event. A typed observation table is a later decision.

## 9. Workspace/auth contract

Verified against the auth-owner contract:

- `AiScanService.__init__` caches only provider configuration; it does not read,
  cache or freeze a workspace at init.
- `WorkflowExecutor` resolves `workspace_id()` per `AI_SCAN` execution, not at
  construction or module import, and passes it explicitly into
  `AiScanService.scan`. `asyncio.to_thread` receives the already-resolved value.
- `CodeFactsStore`, artifact lookup, node-run persistence and SSE all use the
  current `DatabaseRepository`/workspace context; no second DB repository,
  auth copy or context copy was added.
- No `os.environ` mutation in the AI_SCAN request path, no module-level
  workspace and no global identity cache. The upgrade CLI may set
  `RUNTIME_WORKSPACE_ID` from its explicit `--workspace-id` before opening the
  repository. A future ContextVar implementation in `identity.py` can replace
  the resolver without changing AI_SCAN logic; scheduler work remains owned by
  root and must pass its explicit workspace as usual.
- AI node artifacts and outputs stay in the current workspace-scoped
  `runtime.node_runs`; no cross-workspace read/write path exists.

**Final status: implemented and live-checked on this branch; not pushed, not
merged into main, not deployed.**

## 10. Default workflow and safe production upgrade

`get_default_workflow_draft()` returns 12 nodes. The deterministic chain is
unchanged and `node_ai_scan` is connected to `node_project.artifact` and
`node_merge.findings_bundle` (dedupe context). Its only outgoing edge goes to
`node_report.ai_scan_result`; the report shows the advisory result, but nothing
feeds policy, canonical findings or the verdict.

The API seeds `wf_secure_pr_gate` only when a workspace has no workflows. It
does not rewrite an existing workflow, so production needs an explicit step.
The CLI requires an explicit workspace and is read-only on dry-run:

```bash
# dry run: no init_db, no DDL, no workspace row; says missing |
# would_upgrade | already_current | user_modified
DATABASE_URL=... RUNTIME_WORKSPACE_ID=<workspace-uuid> \
  python -m backend.app.demo.default_workflow_upgrade --workflow-id wf_secure_pr_gate

# factory replacement only when the active draft still matches the old
# factory default (a user-selected artifactId is preserved)
DATABASE_URL=... RUNTIME_WORKSPACE_ID=<workspace-uuid> \
  python -m backend.app.demo.default_workflow_upgrade --workflow-id wf_secure_pr_gate --apply

# real modified draft: insert only AI_SCAN, preserve every existing node,
# config, position, edge and the selected artifactId
DATABASE_URL=... RUNTIME_WORKSPACE_ID=<workspace-uuid> \
  python -m backend.app.demo.default_workflow_upgrade --workflow-id wf_secure_pr_gate --add-ai-scan --apply
```

`upgrade_system_default_workflow` detects the frozen pre-AI_SCAN draft and
returns `user_modified` without writing when the draft differs. The only
factory override is the explicit `--force-user-edits`. A requested `--apply`
that does not apply returns a non-zero exit: `user_modified`, `not_applicable`,
`invalid_*` and `stale_upgrade` are failures for deployment automation.
`already_current` / `already_present` are successful noops.

`add_ai_scan_surgically` is the reviewed path for a real modified draft. It
adds one `AI_SCAN` node and up to three edges (artifact, optional findings,
optional report input), validates the result, and previews a diff such as
`added nodes: node_ai_scan`, `added edges: ...` with counts of preserved
nodes/edges and the preserved `artifactId`. It refuses with `not_applicable`
when Project Input is missing or ambiguous; it never replaces the draft.

Both paths read the active revision once and call
`DatabaseRepository.save_workflow_if_latest`, which compares the expected
`latest_revision_id` and inserts the next revision under one row lock. On a
conflict it returns the winner's revision and draft from that transaction; the
caller re-evaluates without a second racing read. A fresh user edit or a second
upgrade therefore cannot be overwritten, and concurrent upgrades produce one
new revision plus one `already_current`. The CLI never prints `DATABASE_URL` or
provider keys.