# Telegram bot: buttons, Mini App, binding and repository runs

**Status:** runtime, menus and regression checks are implemented on this
isolated branch; deployment is performed by the coordinator. Direct Telegram
Bot API is blocked in this environment; an existing HTTP proxy on
`127.0.0.1:14600` was confirmed to reach `getMe` (`@chilli_nedra_bot`, bot id
`9239629524`). No token value was printed, copied or committed.

## 1. Files

| File | Purpose |
| --- | --- |
| `db/migrations/010_telegram.up.sql` | `telegram` schema: bindings, one-time codes, settings, jobs, outbox, cursor, update dedupe |
| `db/migrations/011_telegram_rebind.up.sql` | binding epochs, owner-scoped outbox/jobs |
| `db/migrations/012_telegram_job_recovery.up.sql` | job leases and crash recovery |
| `db/migrations/016_telegram_binding_generation.up.sql` | binding generation sequence fencing |
| `db/migrations/017_telegram_delivery_filters.up.sql` | delivery filters: `min_severity`, quiet hours + timezone, project/branch scope |
| `backend/app/storage/telegram.py` | PostgreSQL repository; no transport, no token |
| `backend/app/telegram/delivery_policy.py` | pure delivery decisions: severity threshold, project/branch match, quiet-hours deferral |
| `backend/app/telegram/bot_api.py` | httpx Bot API client; token from env/0600 file; `TELEGRAM_PROXY_URL` or UNIX-socket proxy; redacted errors; messages, callbacks, commands, menu button |
| `backend/app/telegram/menus.py` | public URL policy, signed 64-byte callback data, inline keyboards, Mini App menu payload |
| `backend/app/telegram/proxy_bridge.py` | loopback TCP→UNIX-socket bridge for `TELEGRAM_PROXY_SOCKET`; no external listener |
| `backend/app/telegram/runtime.py` | polling, commands/buttons, repo jobs, workspace scope usage, outbox worker |
| `backend/app/telegram/report.py` | redacted JSONC-compatible report + JSONC file writer |
| `backend/app/telegram/router.py` | DI router factory for binding/settings endpoints |
| `backend/app/telegram/workspace.py` | delegates to auth-owner `workspace_scope`; no copied auth/context |
| `backend/tests/test_telegram_buttons.py` | disposable-DB/fake-transport regression checks for buttons, callbacks, URLs and epoch guards |
| `backend/tests/test_telegram_delivery.py` | fake Bot API/outbox checks: completion delivery, severity threshold, quiet-hours deferral, idempotency, JSONC findings |

Not touched: frontend, `main.py`, `session.py`, auth storage, `database.py`,
reports, AI engine/contracts, `backend/app/notifications/**`. `api/router.py`,
`engine/executor.py`, `engine/scheduler.py` only carry the completion-hook
wiring described below.

## 2. Root integration contract

```python
from backend.app.artifacts.repository import import_repository
from backend.app.telegram import create_telegram_router, create_telegram_runtime

telegram_runtime = create_telegram_runtime(db_repo, executor, import_repository)
# Completion delivery: every terminal run (manual, scheduled, repository,
# bot job) enqueues one durable outbox row per bound chat.
executor.add_run_completion_hook(telegram_runtime.on_run_completed)
api_router.include_router(
    create_telegram_router(
        db_repo,
        telegram_runtime,
        principal_resolver=telegram_principal,  # account session only
    )
)
```

Lifespan/startup:

```python
# start() is bounded to 20s by getMe; create_task keeps app startup non-blocking.
telegram_task = asyncio.create_task(telegram_runtime.start())
# shutdown:
await telegram_runtime.stop()
```

`telegram_principal(request)` must return only a verified account principal:

```python
{"userId": "<uuid>", "workspaceId": "<uuid>"}
```

Do not return a demo session (no `userId`) or a raw cookie payload. A wrong or
missing principal yields `401/503`; the router never guesses a workspace.

Migration order: auth branch has `008_profile_history` and `009_accounts`;
`010_telegram` applies after them. It does not change the runtime schema version
and has no FK to `account.users` (auth validation is the HTTP principal; binding
stores the resulting `user_id`/`workspace_id`). `workspace_id` references
`data_contract.workspaces`.

Multi-workspace background work relies on auth-owner `workspace_scope`.
`telegram_workspace_scope` delegates to it when present. On this pre-auth
checkout it permits only the process/default workspace and refuses a foreign
workspace instead of silently reading it.

## 3. Token and network

Resolution order:

1. `TELEGRAM_BOT_TOKEN` in the process environment.
2. `TELEGRAM_BOT_TOKEN_FILE`, default `~/.config/nedra/telegram-bot.token`.
3. `TELEGRAM_BOT_TOKEN=...` in 0600 `~/.config/nedra/runtime.env`.

Bot API egress uses **only** `TELEGRAM_PROXY_URL` or `TELEGRAM_PROXY_SOCKET`
(exactly one; both set is a config error). `TELEGRAM_PROXY_URL` is a bare
`http(s)://host:port`, no credentials/path/query. `TELEGRAM_PROXY_SOCKET` is an
absolute path to a UNIX socket served by the scoped host relay; the app starts a
loopback TCP→UNIX bridge inside its own network namespace and points httpx at
it. The httpx client sets `trust_env=False`, so global `HTTP(S)_PROXY` and
LLM/client proxy settings do not leak into Telegram traffic. Direct access is
attempted when neither variable is set.

Token files must be mode 0600; broader permissions fail closed. The token is
never put in argv, git, logs, exceptions, API responses or the DB. `repr` is
`TelegramBotAPI(<redacted>)`; HTTP/URL errors are redacted before logging.
`TELEGRAM_API_BASE` must be HTTPS; `TELEGRAM_BOT_KEY` is only a logical cursor
key. httpx INFO records are rendered before filtering, so a token inside an
`httpx.URL` argument is redacted as well, not only plain string arguments.

External check in this environment: direct `getMe` failed, while the host proxy
`127.0.0.1:14600` reached Telegram. That proxy listens on host loopback only and
the app container cannot reach it; production uses a UNIX-socket relay scoped to
that one upstream (see `docs/telegram-deployment.md`). Live container check with
the socket route: `getMe` returned `chilli_nedra_bot`, `proxyMode="socket"`;
`getWebhookInfo` showed no webhook. The runtime itself was not started, so no
`getUpdates` consumed pending messages. No proxy/network/tunnel was changed; the
proxy was already running. Outbound `sendMessage`/`sendDocument` to a real chat
was not exercised (no authorized live chat was available); the outbox retry path
was verified against an unreachable local endpoint.

### Mini App and cabinet URLs

| Variable | Default | Rule |
| --- | --- | --- |
| `TELEGRAM_MINI_APP_URL` | `https://nedra.soloveyka.bond/miniapp` | HTTPS only; host required; no credentials, query string or fragment |
| `TELEGRAM_WEB_CABINET_URL` | `https://nedra.soloveyka.bond/` | Same policy; used in unbound binding instructions |

Both are read by `load_menu_config()` in `backend/app/telegram/menus.py`. An
invalid `TELEGRAM_MINI_APP_URL` disables only the `web_app` button and chat menu
button and is reported through `status().menuError`; polling, bindings, jobs and
the outbox keep working. An invalid cabinet override falls back to the known
public base so an unbound user still gets a safe link. The runtime publishes
`setMyCommands` and `setChatMenuButton` once after a successful `getMe`, in a
background task; a failed publish is logged and never stops polling.

Manual publication without putting the token in argv or output (reads the
token from the existing 0600 env/file source inside the process):

```bash
TELEGRAM_BOT_TOKEN_FILE="$HOME/.config/nedra/telegram-bot.token" \
TELEGRAM_MINI_APP_URL="https://nedra.soloveyka.bond/miniapp" \
/tmp/nedra-venv/bin/python - <<'PY'
import asyncio
from backend.app.telegram.bot_api import TelegramBotAPI
from backend.app.telegram.menus import BOT_COMMANDS, load_menu_config, menu_button

async def publish() -> None:
    api = TelegramBotAPI.from_runtime()
    try:
        await api.set_my_commands(BOT_COMMANDS)
        button = menu_button(load_menu_config())
        if button is not None:
            await api.set_chat_menu_button(button)
    finally:
        await api.close()
    print("public bot config published")

asyncio.run(publish())
PY
```

This changes public bot config only (command list and menu button); it does not
send messages to users.

## 4. HTTP endpoints

Factory prefix `/telegram`; when included into `api_router` they become
`/api/telegram/...`.

| Method | Path | Contract |
| --- | --- | --- |
| POST | `/telegram/binding` | Issue one-time code (TTL 600s); plaintext returned once, DB stores sha256 only; `deepLink` only if bot username is known |
| GET | `/telegram/me` | `{bound, chatId?, telegramUsername?, settings?, botUsername, externalStatus}` for the current account only |
| POST | `/telegram/unbind` | Remove current account's binding |
| GET | `/telegram/scenarios` | Workflows in the current workspace only (`id`, `name`, no drafts) |
| PUT | `/telegram/settings` | Body `{deliveryMode?, scenario?, minSeverity?, quietHours?, projectFilter?, branchFilter?}`; scenario must exist in the current workspace; `quietHours` is `{start, end, timezone}` (wall clock, `HH:MM`) or `{clear: true}`; `minSeverity` ∈ `info|low|medium|high|critical`; `""` clears a project/branch filter. Response is the full settings object, also returned by `GET /telegram/me` |

Example binding response:

```json
{"code":"<one-time>","expiresAt":"...","expiresInSec":600,"deepLink":null,
 "instructions":"Откройте Telegram и отправьте /start <код> боту."}
```

## 5. Bot commands and buttons

Private chat only. The primary flow is **`/start` then a plain code**:

1. `/start` in an unbound chat replies briefly: open the cabinet, create a code,
   and send that code as the next ordinary message. The reply has
   `Открыть NEDRA` (`web_app` → `/miniapp`) and `Открыть кабинет` URL buttons.
   It does not ask the user to type `/start <код>`.
2. The user sends the code as bare text. It is consumed once through
   `TelegramRepository.consume_binding_code`; a wrong/expired code gets an
   explicit “код не подошёл” reply, never silence or generic help.
3. After a successful bind the bot sends the main menu and then the settings
   view with delivery/scenario buttons. No scenario id has to be typed.

The rest of the surface:

- `/start` (bound) — short status (delivery mode, scenario) and the main menu:
  `Открыть NEDRA` (`web_app` → `/miniapp`), `Проверки`, `Настройки`, `Помощь`.
- `/help` — short command reference plus menu; legacy text syntax is not
  advertised.
- `/settings` — buttons: `Кратко` / `JSON` / `Оба` (selected mode is marked
  `✓`), scenario buttons by real workflow name, bounded pages of
  `SCENARIO_PAGE_SIZE=6`, `‹ Назад` / `Ещё ›`, `В меню`. These are the same
  human labels and the same `telegram.settings` rows used by the Mini App.
- `/status` (and the `Проверки` button) — binding state, delivery mode, selected
  scenario, Bot API status, last jobs and last five runs from the bound
  workspace, with `Обновить`, `Настройки`, `В меню` and the Mini App entry.
- A private-chat HTTPS GitHub/GitLab URL — create an idempotent repo job. The
  bot replies with the job id, then imports and runs in the background.

Delivery filters (same `telegram.settings` rows as the cabinet PUT):

- `/settings severity <info|low|medium|high|critical>` — the run's highest
  finding severity must reach the threshold.
- `/settings quiet <HH:MM-HH:MM> <IANA-timezone>` (e.g.
  `quiet 22:00-07:00 Europe/Moscow`; `quiet off` disables) — sends inside the
  window are deferred to its end, never dropped.
- `/settings project <substring|off>` — match the repository URL/artifact name.
- `/settings branch <name|off>` — exact branch name, `main*` prefix allowed.

Backward compatibility (still works, not primary help copy):

- `/start <code>` consumes the same one-time code.
- `/settings alerts|jsonc|both` sets the delivery mode.
- `/settings scenario <workflow_id>` selects a workflow, validated in the bound
  workspace.

### Callback safety

`telegram.menus` encodes each button as
`n1.<action>[.<arg>].<binding_epoch>.<signature>` (≤ 64 bytes). The signature is
HMAC-SHA256 over the action, argument, epoch, Telegram sender id and private
chat id, keyed by a value derived from the bot token. A copied/forwarded button
from another user or chat fails signature verification.

On `callback_query` the runtime:

1. Rejects non-private chats, bots and `from.id != chat.id` before claiming the
   update. Group/foreign manipulation never reaches settings.
2. Claims `update_id` in `telegram.updates` (same dedupe as messages), so a
   redelivered callback cannot write twice.
3. Verifies the signature, length and action.
4. Answers `answerCallbackQuery` before DB work and again in `finally` if the
   first attempt was skipped — no spinner is left behind.
5. Requires the current binding epoch to match the signed epoch. A stale button
   is answered politely and the menu is re-rendered with fresh buttons.
6. Re-fetches scenarios from the *bound* workspace; selection uses a short
   fingerprint of the real workflow id, then verifies the workflow exists before
   `set_settings`. The client cannot choose a workspace, chat or scenario id.
7. Edits the existing message (`editMessageText`) and falls back to a new
   message if Telegram refuses the edit. `message is not modified` is treated as
   success.

Delivery labels are `Кратко` = alerts only, `JSON` = JSONC only, `Оба` =
alerts + JSONC. Stored values remain `alerts` / `jsonc` / `both`.

Group chats and bots are ignored. Files, archives (ZIP) and images are not
supported: the bot replies explicitly with the HTTPS repository-link instruction
instead of staying silent. No report is sent to an unbound chat. Codes are
single-use; a retried `/start` from the same chat inside 10 minutes returns the
existing binding rather than a misleading “used” error.

## 6. Repo job and scan path

`TelegramRuntime._run_job`:

1. Atomically claims `queued -> importing` (`telegram.jobs`).
2. Enters `telegram_workspace_scope(binding.workspace_id)`.
3. Calls the injected `import_repository(repo_url)`; importer is the existing
   GitHub/GitLab HTTPS-only intake, no git execution.
4. Saves artifact through the current `DatabaseRepository`.
5. Loads the selected workflow from the bound workspace, creates the run with
   `trigger_type="telegram"`, calls the existing `executor.execute_run`.
6. Checks the terminal run status and marks the job succeeded/failed.
   Delivery is no longer job-specific: `executor.execute_run` fires
   `runtime.on_run_completed` for **every** terminal run (manual, scheduled,
   repository and bot-triggered), which enqueues outbox rows per bound chat
   according to the account delivery mode. Scheduler paths that mark a run
   failed before execution call `executor.notify_run_completed` directly.

A failed import/run does not affect another run or the bot poller. Run
notifications are outbox-only; Telegram failure never changes scan status,
findings or verdict.

Delivery decision per claim (`telegram/delivery_policy.py`, evaluated in
`_deliver_outbox` inside the delivery's own workspace scope):

- delivery mode must allow the row's kind, otherwise the row is `cancelled`;
- severity threshold: the run's highest finding count
  (`critical..info`) must be `>= min_severity`; otherwise `cancelled` with the
  reason in `last_error`;
- project/branch filters match against the artifact's repository URL, filename
  and branch; unresolvable metadata fails closed (no delivery);
- quiet hours defer the claim to the window end in `quiet_timezone`
  (overnight windows and DST included); a deferral keeps `attempts` untouched
  and is re-evaluated on every claim.

Idempotency:

- completion hooks are idempotent: repeated `on_run_completed`/re-dispatch
  hits `telegram.outbox (chat_id, run_id, kind)` and inserts nothing;
- `telegram.updates.update_id` claim: one handler execution; failed/stale
  processing claims may retry.
- `telegram.jobs.source_update_id` unique: Telegram redelivery cannot create a
  second job.
- `telegram.outbox (chat_id, run_id, kind)` unique: one alert and one JSONC per
  run.
- `claim_job` is `queued -> importing` atomic; two workers cannot execute one
  job.
- Cursor is stored in `telegram.bot_state`; no token is stored there.

Outbox retries are bounded to 5 attempts with exponential backoff (5s base,
300s max); exhausted rows stay `failed` with sanitized `last_error`. Stale
`sending` rows are recovered after 5 minutes.

## 7. JSONC report

Generated at delivery time from:
- `runtime.runs`;
- `runtime.findings` for the same workspace/run (redacted again);
- `runtime.reports` passport;
- latest `ai_scan` node output in `runtime.node_runs`.

The file is a valid JSON document with `.jsonc` extension (JSON is a JSONC
subset; no comments that would break strict parsers).

Shape:

```json
{
  "format": "nedra-telegram-jsonc/1",
  "notice": "AI hypotheses are not verified",
  "run": {"id": "...", "executionStatus": "succeeded", "securityVerdict": "fail",
          "counts": {"total": 5, "critical": 0, "high": 1, "...": 0}},
  "deterministic": {"count": 5, "findings": [{"id": "...", "ruleId": "...",
                    "severity": "...", "primaryLocation": {...},
                    "redactedEvidence": "..."}]},
  "aiHypotheses": {"verified": false, "countsAsFindings": false,
                   "status": "succeeded", "observations": [...]},
  "securityPassport": {...}
}
```

AI observations are never merged into `deterministic.findings`, findings counts
or verdict. If the workflow has no `AI_SCAN`, `aiHypotheses.status` is
`disabled` and the list is empty. Redaction is applied again on export; no raw
secrets are added by Telegram code.

File path: `CG_DATA_DIR/telegram/reports/<workspace-uuid>/<run-id>.jsonc`,
directory 0700, file 0600, atomic replace, bounded to 10 MiB before send.

## 8. Button/callback regression checks (2026-11-22)

`backend/tests/test_telegram_buttons.py` is a plain Python suite (no pytest
dependency) with a recording fake Bot API: no external Telegram call is made.

- 95 checks without a database cover URL policy, callback signing, foreign
  sender/group rejection, invalid/oversized data, stale epoch, mode and
  scenario selection, shifted scenario fingerprints, duplicate updates,
  `editMessageText` fallback, startup command/menu publishing, the primary
  `/start` → bare-code flow, invalid plain code, legacy commands and post-bind
  menu plus settings.
- 4 additional checks use a disposable PostgreSQL database
  (`nedra_tg_buttons_probe`, local container, migrations 010–012): the current
  binding epoch writes, a stale epoch is rejected with
  `SettingsOwnershipLost`, the accepted value is still stored, and a
  pre-rebind epoch is rejected after `binding_epoch` moves to 3.
- Run: `/tmp/nedra-venv/bin/python backend/tests/test_telegram_buttons.py`
  (add `TELEGRAM_TEST_DATABASE_URL=...` for the DB probe). The probe refuses a
  database name that does not start with `nedra_tg_buttons_`.
- Result on this commit: **99/99 checks passed** (95/menu-level + 4/DB).
  `start`, bare-code binding, `/start <code>` compatibility, `/status`,
  `/settings`, `/settings alerts`, `/help` and unknown-text fallback were
  exercised through the same handler entry point as polling.

## 9. Live evidence (local temp PostgreSQL, 2026-11-21)

No production service, push or main was touched. Bot token stayed in env/0600
files and was never printed.

### Real repo importer + executor + Telegram job

- DB `tg_repo_7d4ab510`; job `succeeded`; run `8d6efdf2-5eff-47ba-bf8f-16c25cd372bb`.
- Imported `https://github.com/octocat/Hello-World` through the existing
  `import_repository` (real network, commit `7fd1a60...`).
- Existing executor ran the selected workflow: `executionStatus=succeeded`,
  `securityVerdict=inconclusive`, deterministic findings `0`.
- JSONC generated: format `nedra-telegram-jsonc/1`, deterministic count `0`,
  AI hypotheses `verified=false`, file exists.

### Telegram job with real AI_SCAN + JSONC

- DB `tg_ai_3efd618a`; run `765ca2be-a119-4c60-84bc-8f956517c3de`.
- SSRF project: deterministic findings `0`, verdict `inconclusive`.
- AI_SCAN found one `SSRF` hypothesis; JSONC has
  `aiHypotheses.verified=false`, `countsAsFindings=false`, one observation.
- Deterministic list remains empty; AI is separate.

### Binding/settings/idempotency

- Ephemeral `tg_check_*` temp DB: code TTL 600, one-time consume, same-chat
  retry returns binding, other-chat consume rejected.
- Duplicate `source_update_id` returns the same job; duplicate outbox enqueue
  returns one row; second `claim_update` rejected; failed claim can retry.
- Router factory live-tested with a custom account principal:
  `POST /telegram/binding` 200, `GET /telegram/me` false→true after bot consume,
  `GET /telegram/scenarios` returns workspace workflow, `PUT /telegram/settings`
  200, invalid mode 400, `POST /telegram/unbind` removed binding.

### External Telegram check through proxy

- `load_bot_settings` found the token (not printed) and proxy
  `http://127.0.0.1:14600`.
- `getMe` succeeded: `username=chilli_nedra_bot`, `bot_id=9239629524`.
- Runtime startup through proxy: `externalStatus="ready"`,
  `botUsername="chilli_nedra_bot"`, `proxyConfigured=true` (DB
  `tg_proxy_986c1357`), then graceful stop.
- Outbox delivery against an unreachable local endpoint left both rows
  `pending`, `attempts=1`, sanitized error `All connection attempts failed`;
  the run itself was already `succeeded`.

## 10. Honest limitations

- Direct Bot API is blocked; the app container cannot reach host loopback, so
  production needs the scoped UNIX-socket relay (`docs/telegram-deployment.md`).
  The runtime reports `blocked` if the configured path fails instead of claiming
  success. Real message/document delivery to a chat was not exercised.
- `main.py` starts polling only with `TELEGRAM_ENABLED=1`; a token alone never
  enables polling. Production was silent because this variable was unset.
- The bot uses long polling, not webhooks. If a webhook is configured on the
  bot, root must call `deleteWebhook` once; this module does not change global
  bot state automatically.
- One chat per account (unique `user_id`); rebinding moves the account to the
  new chat.
- Repo import uses the existing importer defaults (default branch, no branch
  command). Explicit branch selection can be added later.
- Job import/run errors use a best-effort direct message; run notifications use
  the durable outbox. A Telegram outage during an import error may leave the
  user without that immediate error message.
- JSONC includes AI hypotheses only if the workflow contains `AI_SCAN`; they
  are always `verified=false` and never affect verdict/counts.
- On a pre-auth checkout, `telegram_workspace_scope` refuses a workspace other
  than the process workspace. Multi-workspace behavior is available only after
  the auth-owner `workspace_scope` is integrated.
- `Открыть NEDRA` and the chat menu button point at the public `/miniapp` URL;
  the frontend route is owned by another team and was not added here. The
  default URL is valid policy-wise, but a real Mini App open was not exercised
  locally.
- Real Telegram callback delivery was not exercised (no authorized live chat).
  Callback handling is covered by the fake transport and a disposable DB;
  production publication happens on coordinator deployment.
- No frontend panel or report integration was added; the HTTP router factory
  and runtime are ready for root to mount.

**Final status: implemented and checked with fake transport plus a disposable
PostgreSQL; not pushed, not merged into main, not deployed, no Telegram
messages or bot config published from this worktree.**

## Интеграция приложения

`api/router.py` монтирует binding/settings через проверенную account-сессию.
Demo-сессии не принимаются. `main.py` запускает polling/outbox только при
`TELEGRAM_ENABLED=1`; это позволяет проверять приложение без второго poller
того же бота. Сохранение токена само по себе не включает polling. Маршрут до
Bot API из контейнера — UNIX-сокет-релей к `127.0.0.1:14600`; проверен живым
`getMe` из контейнера, доставка конечному пользователю ещё требует проверки
после включения env.
