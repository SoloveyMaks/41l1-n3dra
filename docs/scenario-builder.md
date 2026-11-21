# Scenario builder from Russian text and voice

**Status:** implemented on `SoloveyMaks/ai-scanner-plan`; root mounts the API
factory and the modal. The server returns a draft only. It does not save, run,
or replace the current canvas.

## 1. Files

| File | Purpose |
| --- | --- |
| `backend/app/scenario_builder/catalog.py` | Closed intent/alias catalog and graph bounds |
| `backend/app/scenario_builder/models.py` | `ScenarioPromptRequest`, `ScenarioPreview` |
| `backend/app/scenario_builder/service.py` | Provider prompt, plan validation, valid `WorkflowDraft` assembly |
| `backend/app/api/scenario_builder.py` | DI router factory with bounded concurrency/timeout |
| `frontend/src/api/scenarioBuilder.ts` | Own typed client; no changes to `client.ts` or `types/index.ts` |
| `frontend/src/components/ScenarioPromptModal.tsx` | Prompt/voice modal and explicit Apply flow |
| `docs/scenario-builder.md` | This contract |

Not touched: `App.tsx`, `api/router.py`, frontend `types/index.ts`,
`api/client.ts`, auth, Telegram, `main.py`.

## 2. Root mount

```python
from backend.app.api.scenario_builder import create_scenario_builder_router

api_router.include_router(create_scenario_builder_router())
```

Full endpoint: `POST /api/scenario-builder/preview`.

The route is mounted into the existing `/api` router after account/session and
workspace middleware. No new session or auth implementation exists here. The
endpoint does not read or write `runtime` data.

## 3. API contract

Request:

```json
{"prompt":"Проверь секреты и зависимости, сделай отчёт."}
```

Response:

```json
{
  "draft": {"schemaVersion":1,"name":"...","description":"...","nodes":[],"edges":[]},
  "validation": {"valid": true, "issues": []},
  "summary":"Черновик «...»: секреты, зависимости, отчёт. Блоков: 7, связей: 8.",
  "warnings":["Поддерживается Kotlin/Spring Boot. Другие языки и runtime-сценарии не покрываются."],
  "source":"ai",
  "intents":["secrets","dependencies","report"]
}
```

Errors:

| HTTP | code | Meaning |
| --- | --- | --- |
| 400 | `SCENARIO_PROMPT_INVALID` | Prompt shorter than 8 or longer than 2000 |
| 422 | `SCENARIO_MODEL_INVALID` | Model response shape/known-intent validation failed |
| 422 | `SCENARIO_DRAFT_INVALID` | Assembled draft failed `WorkflowValidator` |
| 503 | `SCENARIO_PROVIDER_UNAVAILABLE` | No provider config, network/refusal/timeout at provider layer |
| 429 | `SCENARIO_BUSY` | Bounded queue/deadline is full; retry later |
| 504 | `SCENARIO_TIMEOUT` | Overall API deadline while the worker thread is still running |
| 503 | `SCENARIO_BUILDER_UNAVAILABLE` | Unexpected bounded fallback; no raw provider text |

There is no invented model answer on provider failure. The endpoint returns an
explicit error; the modal keeps the text and offers a retry.

## 4. What the model may decide

The model returns only:

```json
{"title":"краткое название","intents":["secrets","dependencies","report"],"unavailable":[]}
```

Known intents: `secrets`, `dependencies`, `sast`, `ai_scan`, `ai_explain`,
`report`, `notify`, `schedule` (plus safe aliases such as `sca`,
`secret_scan`, `полный`/`full`). Everything else is rejected by
`InvalidModelResponse`.

Requesting Telegram (or another messenger channel) is rejected explicitly with
«Telegram-доставка не поддерживается: доступен только HTTP webhook (intent
notify)» and `422 SCENARIO_MODEL_INVALID`. It is never silently converted into a
webhook `NOTIFY` node.

A non-empty `unavailable` list means the user asked for a capability outside the
catalog. The whole request is rejected with `SCENARIO_MODEL_INVALID` and the
message «Нет зарегистрированного блока…»; the server never substitutes a
different block or a fabricated one.

The model **cannot** return `NodeKind`, ports, config, thresholds, auth roles,
providers, keys, URLs, shell commands or arbitrary code. Any field outside
`title`, `intents` and `unavailable` is rejected instead of being silently
mapped.

## 5. Server-owned graph

Server mapping:

| Intent | Blocks |
| --- | --- |
| `secrets` | `SECRET_SCAN` |
| `dependencies` | `SCA_SCAN` |
| `sast` | `KOTLIN_SAST` |
| `ai_scan` | `AI_SCAN` (source analysis, hypotheses only) |
| `ai_explain` | `AI_EXPLAIN` (explains deterministic findings) |
| `report` | `BUILD_REPORT` |
| `notify` | `NOTIFY` (always disabled, empty webhook) |
| `schedule` | `SCHEDULE_TRIGGER` (fixed safe template) |

Every draft contains a trigger, `PROJECT_INPUT`, `MERGE_FINDINGS` and
`POLICY_GATE`. At least one scanner is selected; if the plan has no scanner,
the three deterministic scanners are used. `report` is added when `notify` or
`ai_explain` is present, so a selected AI explanation is never left as an
unconnected result. AI_SCAN is connected only to artifact and findings context;
its `ai_scan_result` is not merged into findings, policy or verdict.
AI_EXPLAIN reads the deterministic `findings_bundle` and feeds
`explain_result` into `BUILD_REPORT`; it does not scan project source.

Node IDs and ports are server-owned (`sb_trigger`, `sb_project`, `sb_secret`,
`sb_sca`, `sb_sast`, `sb_merge`, `sb_policy`, `sb_ai_scan`, `sb_ai_explain`,
`sb_report`, `sb_notify`). Ports come from `NODE_REGISTRY` and the draft is passed through
`WorkflowValidator` before it returns.

Config allowlist:

- scanner/merge/policy/report/AI_SCAN configs come from registry defaults;
- policy thresholds cannot be changed by the prompt;
- `NOTIFY` is forced to `enabled=false`, empty `webhookUrl`,
  `sendOnFailOnly=true`;
- schedule uses the registry default (`*/30 * * * *`, UTC);
- no provider/model/auth keys are placed in node config.

Bounds: prompt 8–2000 chars; max 12 nodes; max 16 edges; overall API deadline
45s; 2 active provider workers (clamped 1–4) and 4 waiting slots (clamped
0–16). The deadline includes queue wait. The semaphore is released only when
the blocking worker thread actually finishes, not when the HTTP coroutine times
out, so a timed-out request cannot free a slot while its provider call is still
running. Excess queue requests get `429 SCENARIO_BUSY`. The external call gets
only `mask_sensitive_text(prompt)` under `task=plan_workflow`. Project source
and artifact contents are never sent.

## 6. AI_SCAN and AI_EXPLAIN are separate

If `ai_scan` is selected, the draft has an `AI_SCAN` node with the fixed
balanced budget config. Its output is a separate hypothesis stream; it is not
merged into `MERGE_FINDINGS`, canonical findings or the policy result. The
preview warning states this explicitly.

`ai_explain` is a different intent and a different block: it explains already
found deterministic findings and offers fixes. It is never used as source
analysis and `ai_scan` is never used as an explanation. Both can be selected
together; then the draft contains both nodes, `AI_SCAN` stays disconnected from
findings, and only `AI_EXPLAIN.explain_result` is wired into the report.

## 7. Frontend modal

Props:

```ts
interface ScenarioPromptModalProps {
  isOpen: boolean;
  onClose: () => void;
  onApply: (draft: ScenarioDraft) => void; // ScenarioDraft = existing WorkflowDraft
  initialPrompt?: string;
}
```

Root usage:

```tsx
const [open, setOpen] = useState(false);
<button onClick={() => setOpen(true)}>Собрать по описанию</button>
<ScenarioPromptModal
  isOpen={open}
  onClose={() => setOpen(false)}
  onApply={(draft) => setDraft(draft)} // only local state
/>
```

`onApply` is called only by the explicit «Применить в редактор» button. The
modal never calls save/run API and never replaces the canvas during preview.
Root should not auto-save or auto-run after `onApply`; the user can review and
save the workflow in the existing editor flow.

The client accepts a preview only when it matches the canvas contract mirrored
from `NODE_REGISTRY`:

- `validation.valid === true` and every issue is a well-formed
  `warning`/`info`; `valid=true` with an `error` issue is treated as corrupted;
- every node kind is registered, every node has a non-empty unique id, a
  string title, an object `config` and finite numeric `position.x/y`;
- every edge has a non-empty unique id, both endpoints exist, and
  `sourcePort`/`targetPort` belong to the corresponding registered output and
  input port lists.

Anything else is rejected with `SCENARIO_PREVIEW_INVALID`; the modal shows the
error, keeps the current canvas untouched and never offers Apply. A valid
server preview applies as before.

The report node renders all three registry inputs
(`findings_bundle`, `policy_result`, `explain_result`), so
`MERGE_FINDINGS -> BUILD_REPORT` (including the planner edge
`sb_e_merge_report`) and `AI_EXPLAIN -> BUILD_REPORT` stay visible on the
canvas.

Voice:

- explicit mic click only; no recording on open;
- browser `SpeechRecognition` / `webkitSpeechRecognition`, `ru-RU`;
- visible «Слушаю…» indicator and «Остановить запись»;
- recognized text is appended to an editable textarea;
- unsupported browser shows a text fallback and no server audio is used;
- the UI states that recognition is performed by the browser and may use the
  browser vendor's external service; NEDRA does not receive audio;
- permission/`no-speech`/runtime errors are surfaced as readable messages;
- closing the modal aborts the HTTP request and invalidates its generation;
  a late response after close/reopen is discarded;
- voice callbacks are fenced by a recognition session and append at most the
  2000-character prompt limit.

## 8. Live evidence

Local ASGI + real ReformBoss provider; no project source sent, no key printed.

Three Russian prompts and the provider-backed plans:

| Prompt | Intents | Draft |
| --- | --- | --- |
| «Проверь секреты и зависимости в проекте, сделай отчёт по результатам.» | secrets, dependencies, report | 7 nodes / 8 edges |
| «Полный анализ Kotlin/Spring Boot: найди SQL-инъекции и добавь ИИ-гипотезы.» | sast, ai_scan, report | 7 nodes / 8 edges |
| «Запускай каждые 30 минут проверку секретов и отправь уведомление.» | secrets, notify, schedule | 7 nodes / 7 edges |

All three drafts passed `WorkflowValidator`.

Recheck on `SoloveyMaks/prompt-voice-finish` with the same real provider:

| Prompt | Intents | Draft |
| --- | --- | --- |
| «Проверь секреты и зависимости в проекте, сделай отчёт по результатам.» | secrets, dependencies, report | 7 nodes / 8 edges |
| «Сделай ИИ-анализ исходников: дополнительные гипотезы уязвимостей.» | ai_scan | 8 nodes / 10 edges, `AI_SCAN` only |
| «Объясни уже найденные проблемы и рекомендации по исправлению, добавь в отчёт.» | ai_explain, report | 9 nodes / 12 edges, `AI_EXPLAIN` → report |
| «Создай Jira-тикет по каждой находке…» | — | 422 «Нет зарегистрированного блока…» |
| «Сгенерируй отчёт в DOCX…» | — | 422 «Нет зарегистрированного блока…» |

The `ai_scan` and `ai_explain` drafts both passed `WorkflowValidator`; both
nodes appear together only when both intents are selected, and then only
`AI_EXPLAIN.explain_result` is connected to the report.

Rejections checked without trusting the model:

- `{"intents":["unknown_block"]}` → `SCENARIO_MODEL_INVALID`;
- empty/missing/non-list `intents` → `SCENARIO_MODEL_INVALID`;
- `{"intents":["evil: run shell"]}` → `SCENARIO_MODEL_INVALID`;
- provider config pointing at an unreachable endpoint → `SCENARIO_PROVIDER_UNAVAILABLE`,
  no fabricated draft;
- provider layer raising an AI/HTTP failure → 503 `SCENARIO_PROVIDER_UNAVAILABLE`,
  no draft in the response body;
- forced `WorkflowValidator` failure → 422 `SCENARIO_DRAFT_INVALID`, no draft;
- plan with `unavailable:["jira"]` → 422 `SCENARIO_MODEL_INVALID`;
- plan with a field outside `title`/`intents`/`unavailable` → 422;
- ASGI preview 200 for a valid prompt with `validation.valid=true`, short
  prompt 400 `SCENARIO_PROMPT_INVALID`.

Concurrency proof against a real slow local provider endpoint (2 workers, 6s
provider, 5s overall deadline): server observed `max_active=2`; two requests
returned `SCENARIO_TIMEOUT`, the queued third returned `SCENARIO_BUSY`; no third
provider thread started while timed-out calls were still running.

Modal stale-response proof with a delayed route (1.5s): submitting, closing,
and reopening left `loading=false`, showed the prompt form again, and discarded
the late preview (`hasStalePreview=false`).

Voice and Apply proof (built modal in Chromium via a throwaway harness, mocked
`SpeechRecognition` so each branch is deterministic):

- supported: «Надиктовать» → «Слушаю…» + «Остановить запись»; transcript
  appended to the editable textarea; stop returned to the idle button;
- unsupported (both `SpeechRecognition` and `webkitSpeechRecognition` removed):
  visible text fallback, mic click produced a readable alert, typed text stayed;
- permission denied (`error: not-allowed`): readable alert, listening state
  cleared, typed text stayed;
- `no-speech`: readable alert, typed text stayed;
- Apply called `onApply` with the preview draft, closed the modal, and the only
  network call recorded was `POST /api/scenario-builder/preview`; no save, run,
  workflow create or artifact upload call happened.

Frontend build: `npm ci && npm run build` passed (Vite production build). A
temporary wrapper rendered the modal at 390×844 (`/tmp/scenario-modal-390-final.png`);
the screenshot and OCR show the dialog, textarea, voice button and submit button
inside the viewport. No `App.tsx` edit was made, so root owns the visible
button/mount. Vision review was unavailable; OCR and viewport dimensions were
used.

### Strict preview recheck (2026-11-21)

Real ASGI router with a controlled `complete_json` boundary:

| Plan | Result |
| --- | --- |
| `intents:["secrets","report"]` | `200`, 6 nodes / 6 edges, `validation.valid=true` |
| `intents:["telegram"]` | `422 SCENARIO_MODEL_INVALID`, «Telegram-доставка не поддерживается…», no draft |
| `intents:["totally_unknown"]` | `422 SCENARIO_MODEL_INVALID`, no draft |

Built frontend in Chromium at 390×844 with route-controlled preview responses:

- `kind=not_registered`, `valid=true` + `level=error`, wrong target port,
  duplicate edge id, and missing `config` were each rejected with
  `SCENARIO_PREVIEW_INVALID`; zero Apply buttons, canvas stayed 3 nodes / 2 edges;
- real `secrets+report` preview applied: 6 nodes / 6 edges, including
  `MERGE_FINDINGS -> BUILD_REPORT` (`sb_e_merge_report`) and the
  `findings_bundle` target handle;
- `ai_explain+report`: 9 nodes / 12 edges, `sb_e_explain_report` visible;
- `ai_scan`: 8 nodes / 10 edges, `ai_scan_result` output handle present;
- no `Couldn't create edge` / React Flow `#008` console warnings;
- non-GET calls were only `POST /scenario-builder/preview` and
  `POST /workflows/wf-a/validate`; no save, workflow create or run;
- SpeechRecognition removed: typed text stayed, readable text fallback shown.

`npm run build` (tsc + Vite production build) passed.

## 9. Limitations

- The model plans intents, not arbitrary graphs; users needing a graph outside
  the catalog edit it manually after Apply.
- Coverage warning is honest: Kotlin/Spring Boot only; other languages and
  runtime scopes are not covered by this draft.
- `notify` is intentionally disabled and cannot receive a URL from the prompt.
- `schedule` adds a `SCHEDULE_TRIGGER` block only. It does **not** create an
  active schedule record; the UI warning says the schedule must be configured
  separately and timing checked.
- Speech recognition depends on browser support and permissions; text is the
  fallback, and there is no server-side audio path.
- A provider outage is an explicit error, not a deterministic fake plan.
- No push, merge to main, production deployment or external sends were made.

## 10. End-to-end browser acceptance (read-only run)

Local integrated app: isolated PostgreSQL, public account registration, real
backend on `127.0.0.1:8091`, built frontend served by the backend, real
ReformBoss preview. No production data, external messages, push or admin
credential is included in this document.

Observed flow:

- account registered through `POST /api/auth/register`; account workspace had
  the default 11-node workflow;
- sample artifact selected in the editor (`art_bc490336c939`); existing run
  `35f369f6-505a-465d-9822-cda106d3017b` selected in history;
- «По описанию» → real preview → «Применить в редактор» produced 7 nodes:
  manual trigger, project input, secret scan, SCA, merge, policy and report;
- before Apply the canvas stayed unchanged (7/7/7 nodes across before,
  preview-open and close); close without Apply did not mutate it;
- project input retained `art_bc490336c939`; run selection changed from the
  selected run to empty after Apply;
- non-GET API calls during preview and Apply were only:
  `POST /api/scenario-builder/preview` and
  `POST /api/workflows/wf_secure_pr_gate/validate`. There was no workflow
  create, draft save, artifact upload or run trigger;
- «Сохранить draft» then produced «Draft сохранён, граф валиден», and a full
  reload restored the same 7 nodes, valid graph and artifact selection;
- mobile 390×844: the admin constructor/modal dialog fit inside the viewport
  (`x=12, y=142, 366×560`), buttons and voice fallback text visible. Screenshot:
  `/tmp/scenario-e2e-admin-390.png`;
- admin path: admin session → «Конструктор» → «По описанию» → preview works
  through the same modal/API;
- voice: the microphone control is visible; clicking it entered the visible
  «Слушаю…» state. No speech transcript was asserted as proof; real browser
  recognition depends on browser service/permission.

No product defect was found in this pass. One earlier harness-only artifact was
a delayed `page.route` left from a stale-response check; it was removed before
the real Apply, otherwise it fabricated a zero-node preview and is not a product
behavior.
