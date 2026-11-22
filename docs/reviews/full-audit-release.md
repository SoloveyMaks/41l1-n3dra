# NEDRA — release/build/lifecycle audit, 2026-11-22

## Revision and scope

- Exact starting HEAD: `35d27d30ae44fd955824001e18eff895197c933c` (clean worktree).
- Completed implementation commit: `2489ca77627eb84f9009e85f101b9ec2d9236844`.
- Later report/documentation and scheduler integration commits, if any, are
  descendants; their exact IDs are supplied in the worker handoff. No production
  revision is inferred from this checkout.
- Owned scope: `deploy/`, project scripts in `scripts/`, `backend/app/main.py`,
  `backend/requirements.txt`, README, deployment/handoff docs and this report.
  Router registration and middleware in main were preserved. No db migration,
  frontend package/lock, engine, user config or secret was changed.
- Context read: `/tmp/nedra-mini-release-preflight/report.md`. Its production
  observations were not repeated and are not current-release proof.
- All disposable data/probes: `/tmp/nedra-audit-release/`. No helper agents.

## Result

Confirmed source defects were corrected and the available local paths exercised.
Frontend build, env loading, manifest generation/verification, cleanup fault
probes and Python/Bash compilation completed. **Image build, running app with
PostgreSQL, uid10001/rootfs/volume execution, real single-poller operation,
backup restore, systemd startup/recovery and deployment remain unverified.**
No deploy/apply/rollback, container/image/tag/volume/service/network mutation,
production DB access or external provider/Bot API call was performed.

## Defects and corrections

### 1. Build revision did not identify build contents

Before: `nedra-build.sh` labelled a live worktree build with `rev-parse --short
HEAD` (or `unknown`). An edited tracked file or an untracked file under a COPY
directory could ship under the same revision. There was no runtime manifest.

Changed: refuse dirty tracked/index state, require full SHA, extract that git
commit to a private temporary build context and build only the archive.
Dockerfile requires `NEDRA_REVISION`. `nedra_manifest.py` records full revision
and file SHA-256 for app source, compiled frontend, deployment requirements and
healthcheck; Dockerfile also verifies the manifest after switching to uid10001.
Runtime files receive read/search permissions before that switch. Up runs the
resolved image ID rather than resolving a mutable tag again at `podman run`.

After: real dirty-worktree invocation exited 1 before calling Podman. Actual
`git archive 2489ca7…` had 611 entries and README bytes exactly equal to git's
committed blob despite different working README; no node_modules, pyc or git
metadata. Manifest verification covered 288 files; changing main.py in the
/tmp copy caused `release manifest mismatch`, restoring it verified again.

Limits: no image was built; Dockerfile's uid check is a future build gate, not
an observed container result. The manifest covers the listed runtime trees,
not installed wheel/system/Node binary bytes. Dependency versions/base digests
remain pinned; package lock has no artifact hashes for Python dependencies.

### 2. Packaged/local dependencies were incomplete

Before: Dockerfile omitted `rag/`, although `analyzers.contract_export` imports
`rag.store` with `--rag-store` and README documents that path. Local backend
requirements omitted python-docx, although report export imports it and reports
`python-docx не установлен` when absent. Deployment lock already had it.

Changed: copy rag into runtime; add `python-docx>=1.2,<2` to backend requirements.

After: actual `rag.store` import from the isolated assembled runtime tree
succeeded. Actual `_docx_modules` source, isolated from DB imports, returned
`ReportExportUnavailable` in the existing Python3.12 environment without docx;
the same code loaded 10 modules in host Python with docx. All 10 direct backend
requirements accept the versions already pinned in deployment requirements.

Limits: no new pip install/wheel build was executed; this is not proof that a
fresh environment installs successfully or that DOCX HTTP export works with DB.

### 3. Cleanup stopped at the first failure

Before: configuration error/scheduler.start failure preceded the try/finally;
Telegram stop failure skipped notification stop, scheduler stop and pool close.

Changed: register cleanup with AsyncExitStack before starting the corresponding
components. Pool close is registered before config validation; all registered
callbacks are attempted even if another fails. Telegram startup cancellation
is awaited before stopping Telegram. Telegram cleanup is registered before
notification initialization, so its failure does not leak the global runtime.

After: a disposable probe compiled the actual before/after lifespan function
and injected failures at config validation, scheduler.start, notification.start
and Telegram.stop. Before, config had no cleanup, scheduler.start had only its
start event, and Telegram.stop failure ended cleanup immediately. After, all
five scenarios (including normal yield) reached pool close; applicable
notification and scheduler cleanup ran. Real app import with DATABASE_URL
absent failed with `DatabaseUnavailable: DATABASE_URL не задан`.

Limits: injected component probes prove callback control flow only, not PG or
Telegram integration. Import-time DB initialization occurs in api/router before
lifespan; startup with a real isolated DB remains a gate. Scheduler stop is
synchronous cancellation in the starting revision and requires the separately
coordinated async-close integration before claiming tasks finish before pool
closure; see open items.

### 4. Bash and Podman could receive different environment values

Before: runtime loader accepted `export` and quotes through the source parser,
while Podman's env-file reads literal KEY=value. A quoted source value followed
by `# comment` also acquired the comment as part of its value because shlex used
comments=False.

Changed: explicit canonical mode for runtime startup, rejecting export/quoted
values, invalid entries and duplicate keys; source parser supports comments
following quoted values. Validate all parsed values for control characters.
Bootstrap validates its complete generated canonical file before replacing it.

After: 8 local cases passed (quoted #/comment, literal raw #, literal shell
substitution, rejected export/quotes/duplicates/invalid line/NUL). Actual Bash
`load_runtime_env` loaded a synthetic mode0600 file and preserved the literal
`space # literal $(false)` without evaluation. Real runtime.env was never read
or changed by these probes.

Limits: malformed/legacy runtime.env now fails closed until bootstrap
canonicalizes it under separate authorization. Bootstrap --write was not run.

### 5. Failed systemd startup could leave an app/poller running

Before: app Type=oneshot used ExecStop only. Failed ExecStart (including health
wait timeout) does not invoke ExecStop; a running/restarting container can
outlive the failed unit. Podman's usual ten-second stop also undercut the
15-second Telegram job grace visible in the source.

Changed: app unit adds idempotent owned-container ExecStopPost. Project stop,
down and recreate use 45-second stop; new app container stores stop-timeout45.
Missing Telegram proxy socket is checked before stopping/removing the old app.

After: Bash syntax passed and generated units contain ExecStopPost plus existing
Wants/After db/proxy. No actual unit was installed or started.

Limits: `systemd-analyze --user verify /tmp/nedra-audit-release/units/*.service`
exited1 on sandbox SO_PASSCRED `Operation not permitted`; unit verification is
not passed. Template ordering does not prove installed units have that order.
No real startup failure/recovery or graceful run completion was exercised.

### 6. Deployment lacked a local duplicate-Telegram guard

Before: another labelled app container with Telegram enabled did not prevent a
new token-enabled app from starting, permitting competing getUpdates consumers.

Changed: before any old-app removal/start, inspect other running
`io.nedra.component=app` containers; fail if Telegram is enabled on another one.
Inspection prints only the boolean, never tokens/env values. The candidate
TELEGRAM_ENABLED parser matches main's whitespace/case handling.

After: Bash syntax and fail-closed command substitution paths reviewed; no
container scenario was executed.

Limits: conservative local guard, not a distributed lock, atomic fencing or a
same-token comparison. Unlabelled processes, other hosts and simultaneous starts
require operator fencing; no claim of global single-poller safety.

### 7. Status ignored Docker health failures

Before: status displayed unhealthy/none health but could return status OK if
HTTP/pg_isready passed. Timeout diagnostic also named /api/runs instead of the
actual /api/health endpoint.

Changed: app and DB Docker health statuses must both be healthy; diagnostic
names /api/health.

After: Bash syntax passed and control flow inspected. No production status
probe was run; real Docker health/status behavior remains unverified.

### 8. Release instructions claimed old state as current

Before: README and handoff advertised bde900c, runtimev4, no accounts, no hooks;
deployment doc mixed several old image IDs and successful public checks.

Changed: describe source v5/accounts/hooks with explicit verification limits;
remove old production/image success claims; document immutable snapshot/manifest,
actual four units, non-destructive release preparation, consistent DB+globals+
app-data backup, isolated restore, poller fencing and data/schema rollback gates.
Remove frozen analyzer/RAG counts and current provider403 claim from README.

Source completeness: baseline contains 11 ledger SQL files
001/002/004/005/006/007/008/009/010/011/012 and optional003/014/015. No 013 and
no custom-check/sandbox runtime were found; reference_checks is present and is
not a sandbox. No missing feature/SQL was recreated speculatively. Coordinator
reports a pending forward migration for telegram.binding_epoch_seq in the data
owner's branch; it is not in this worker's SHA and must be integrated/rehearsed
as a new release. Historical applied SQL bytes were not edited.

## Commands and factual outputs

| Command / disposable probe | Actual result |
| --- | --- |
| `git rev-parse HEAD` at entry | Exact35d27d30ae44fd955824001e18eff895197c933c |
| `npm run build --prefix /tmp/nedra-audit-release/source/frontend` | 2223 modules; HTML1.02kB; CSS152.88kB; JS963.76kB; large-chunk warning |
| frontend package-lock comparison | 71 installed versions checked, 0 mismatch; 79 absent optional/platform entries |
| `python3 /tmp/nedra-audit-release/env-probe.py` | 8 cases passed; no shell evaluation |
| `load_runtime_env` with only synthetic /tmp env | Required keys loaded, literal value preserved |
| `TELEGRAM_ENABLED=0 python3 /tmp/nedra-audit-release/lifecycle-probe.py` | 5 before/after control-flow cases; pool close reached after each |
| `python3 /tmp/nedra-audit-release/manifest-probe.py` | 288 files verified; tamper rejected; restored verified |
| `python3 /tmp/nedra-audit-release/archive-probe.py` | 611 archived entries bound to2489ca7; working README excluded |
| Python `compile` under host3.14 and existing /tmp/nedra-profile-venv/bin/python3.12 | 5 owned Python files passed |
| `bash -n` each `scripts/*.sh` | 9 scripts passed |
| `git diff --check` | Passed |
| Real `import backend.app.main` with DATABASE_URL/provider/bot token removed and /tmp data root | Exit1, explicit missing DATABASE_URL; no connection attempted |
| `systemd-analyze --user verify` rendered units | Exit1, sandbox SO_PASSCRED Operation not permitted; blocked |
| `podman stop --help`, `podman run --help` | Could not obtain configuration: /run/user/1000/libpod chmod read-only; no container command executed |

Frontend deps were copied from the existing integration checkout into /tmp
without changing that checkout. Initial shutil copy dereferenced `.bin` symlinks
and failed to resolve lib/tsc.js; a fresh /tmp copy preserving symlinks produced
the successful real build above. No npm install/network proof is claimed.

## Open release gates / coordination

1. Await scheduler owner's async aclose contract; integrate main with
   push_async_callback(scheduler.aclose), await owned tasks before DB close and
   record a separate commit. Existing sync stop remains compatible signaling.
2. Final combined exact SHA differs from35d27d3; regenerate image/manifest,
   migration inventory/checksums and rehearsal. The old /tmp preflight's
   unchanged-schema/rollback assessment is invalid for a new migration set.
3. No image build or image UID/rootfs/volume execution. No fresh Python install,
   no isolated PG startup/migration or DB+file restore. Existing volumes must be
   checked for uid10001 access without blindly changing production ownership.
4. The explicitly rejected `podman start nedra-mini-integration-pg` was neither
   retried nor bypassed via another container/action/worker. The known refusal
   and earlier403 remain constraints; this worker made no new external403 probe.
5. Systemd full verification is blocked by sandbox; no bypass or service change.
   Source Wants/After already includes proxy, installed unit parity/reboot
   recovery requires coordinator verification.
6. No production/public/Telegram WebView/menu acceptance. No claim about current
   provider availability, current host linger or deployed app identity.
7. Backup consistency requires all writers/pollers fenced, DB/globals/app-data
   taken together and successfully restored in isolation. Image-only rollback
   needs matching schema and backward-compatible data, not just a tag or v5.

Verdict: useful source fixes and bounded local proofs are delivered; the next
release is **not deployment-ready on this audit's evidence alone**.
