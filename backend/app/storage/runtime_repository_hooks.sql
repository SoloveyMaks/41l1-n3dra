-- Repository push triggers (runtime schema v5), additive upgrade from v4.
--
-- trigger_mode отделяет событийные watches от polling: webhook-строки не
-- выбираются due-запросом, а ждут подписанную доставку. webhook_nonce — не
-- секрет, а соль для per-watch HMAC-секрета; сам секрет в БД не хранится.
--
-- repository_hook_deliveries — durable inbox. Строка пишется до ответа 202,
-- уникальность (watch, delivery_id) отсекает повтор провайдера, а claim/lease
-- не дают двум процессам обработать одну доставку. Ошибка и число попыток
-- видимы; исходный payload не хранится.

ALTER TABLE runtime.repository_watches
    ADD COLUMN IF NOT EXISTS trigger_mode text NOT NULL DEFAULT 'poll';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'runtime.repository_watches'::regclass
           AND conname = 'repository_watches_trigger_mode_check'
    ) THEN
        ALTER TABLE runtime.repository_watches
            ADD CONSTRAINT repository_watches_trigger_mode_check
            CHECK (trigger_mode IN ('poll', 'webhook'));
    END IF;
END $$;

ALTER TABLE runtime.repository_watches
    ADD COLUMN IF NOT EXISTS webhook_nonce text,
    ADD COLUMN IF NOT EXISTS last_delivery_at timestamptz,
    ADD COLUMN IF NOT EXISTS last_delivery_status text,
    ADD COLUMN IF NOT EXISTS last_delivery_error text;

CREATE TABLE IF NOT EXISTS runtime.repository_hook_deliveries (
    workspace_id uuid NOT NULL,
    id text NOT NULL,
    watch_id text NOT NULL,
    provider text NOT NULL CHECK (provider IN ('github', 'gitlab')),
    delivery_id text NOT NULL,
    commit_sha text,
    ref text,
    workflow_revision_id text,
    state text NOT NULL CHECK (
        state IN ('pending', 'processing', 'done', 'ignored', 'failed')
    ),
    attempt integer NOT NULL DEFAULT 0,
    claim_token text,
    claimed_until timestamptz,
    next_attempt_at timestamptz,
    run_id text,
    detail text,
    error text,
    received_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, id),
    UNIQUE (workspace_id, watch_id, delivery_id),
    FOREIGN KEY (workspace_id, watch_id)
        REFERENCES runtime.repository_watches (workspace_id, id)
);

CREATE INDEX IF NOT EXISTS idx_repository_hook_deliveries_due
    ON runtime.repository_hook_deliveries (workspace_id, next_attempt_at)
    WHERE state IN ('pending', 'processing');
CREATE INDEX IF NOT EXISTS idx_repository_hook_deliveries_watch
    ON runtime.repository_hook_deliveries (workspace_id, watch_id, received_at DESC);
