BEGIN;
SET LOCAL search_path = telegram, public;

-- Durable delivery intent per completed run. The executor completion hook
-- records the intent BEFORE touching the outbox: if settings/outbox reads
-- fail transiently (or the process dies between record and enqueue), the
-- background sweep replays the intent and the idempotent outbox unique key
-- (chat_id, run_id, kind) creates exactly the missing rows. A completed run
-- can therefore never end up permanently without its Telegram delivery.
CREATE TABLE IF NOT EXISTS telegram.run_delivery_intents (
  run_id text PRIMARY KEY CHECK (run_id <> '' AND length(run_id) <= 128),
  workspace_id uuid NOT NULL,
  status text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'done')),
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz
);

CREATE INDEX IF NOT EXISTS run_delivery_intents_pending_idx
  ON telegram.run_delivery_intents (created_at)
  WHERE status = 'pending';

-- Finished intents are an audit trace, not state: purge them after a week.
CREATE INDEX IF NOT EXISTS run_delivery_intents_done_idx
  ON telegram.run_delivery_intents (completed_at)
  WHERE status = 'done';

CREATE OR REPLACE FUNCTION telegram.schema_version_v5() RETURNS text
LANGUAGE sql IMMUTABLE AS $$ SELECT '018'::text $$;

COMMIT;
