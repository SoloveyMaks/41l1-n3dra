-- Repository watches (runtime schema v4), additive upgrade from v3.
--
-- Одна строка watch на workspace+workflow; url/branch уже канонизированы
-- importer-ом, credentials в url не хранятся. Claim-поля нужны для
-- DB-backed исключения параллельных проверок между процессами и рестартами:
-- просроченный claim освобождается по времени, не вручную.
--
-- repository_watch_commits — ledger идемпотентности на пару
-- (watch, commit_sha). Переход processing -> queued (run создан) делает только
-- владелец claim_token. failed разрешает повтор, queued/running/done запрещают
-- второй run на тот же SHA даже после overlapping tick/check-now/restart.

CREATE TABLE IF NOT EXISTS runtime.repository_watches (
    workspace_id uuid NOT NULL REFERENCES data_contract.workspaces (workspace_id),
    id text NOT NULL,
    workflow_id text NOT NULL,
    url text NOT NULL,
    provider text NOT NULL CHECK (provider IN ('github', 'gitlab')),
    branch text NOT NULL,
    cron_or_interval text NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    next_check_at timestamptz,
    last_checked_at timestamptz,
    last_seen_commit text,
    last_run_id text,
    last_error text,
    claim_token text,
    claimed_until timestamptz,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, id),
    FOREIGN KEY (workspace_id, workflow_id) REFERENCES runtime.workflows (workspace_id, id)
);

CREATE TABLE IF NOT EXISTS runtime.repository_watch_commits (
    workspace_id uuid NOT NULL,
    watch_id text NOT NULL,
    commit_sha text NOT NULL,
    state text NOT NULL CHECK (state IN ('processing', 'queued', 'running', 'done', 'failed')),
    claim_token text,
    claimed_until timestamptz,
    artifact_id text,
    run_id text,
    error text,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, watch_id, commit_sha),
    FOREIGN KEY (workspace_id, watch_id)
        REFERENCES runtime.repository_watches (workspace_id, id)
);

CREATE INDEX IF NOT EXISTS idx_repository_watches_due
    ON runtime.repository_watches (workspace_id, next_check_at)
    WHERE enabled;
CREATE INDEX IF NOT EXISTS idx_repository_watch_commits_run
    ON runtime.repository_watch_commits (workspace_id, run_id);