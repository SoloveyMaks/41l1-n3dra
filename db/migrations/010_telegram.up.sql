BEGIN;
CREATE SCHEMA IF NOT EXISTS telegram;
SET LOCAL search_path = telegram, public;

-- Telegram is a delivery/binding layer, not a second source of truth.
-- Bindings reference a real workspace from data_contract. user_id is validated
-- against the account session by the HTTP binding endpoint; no FK to
-- account.users is declared so this migration remains applicable while the
-- account layer is integrated separately.
CREATE TABLE IF NOT EXISTS telegram.bindings (
  chat_id bigint PRIMARY KEY,
  user_id uuid NOT NULL,
  workspace_id uuid NOT NULL REFERENCES data_contract.workspaces (workspace_id),
  telegram_username text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id)
);

CREATE INDEX IF NOT EXISTS bindings_workspace_idx
  ON telegram.bindings (workspace_id);

-- One-time account binding codes. Only sha256(code) is stored; the plaintext
-- code is returned once by the authenticated API and is never logged.
CREATE TABLE IF NOT EXISTS telegram.binding_codes (
  code_hash text PRIMARY KEY CHECK (code_hash ~ '^[0-9a-f]{64}$'),
  user_id uuid NOT NULL,
  workspace_id uuid NOT NULL REFERENCES data_contract.workspaces (workspace_id),
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  consumed_at timestamptz,
  consumed_chat_id bigint,
  CHECK (expires_at > created_at),
  CHECK ((consumed_at IS NULL) = (consumed_chat_id IS NULL))
);

CREATE INDEX IF NOT EXISTS binding_codes_user_idx
  ON telegram.binding_codes (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS telegram.settings (
  chat_id bigint PRIMARY KEY REFERENCES telegram.bindings (chat_id) ON DELETE CASCADE,
  delivery_mode text NOT NULL DEFAULT 'both'
    CHECK (delivery_mode IN ('alerts', 'jsonc', 'both')),
  scenario text NOT NULL DEFAULT '' CHECK (length(scenario) <= 128),
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- One repository import/scan request. source_update_id makes Telegram
-- redelivery idempotent even if the acknowledgement was lost.
CREATE TABLE IF NOT EXISTS telegram.jobs (
  job_id uuid PRIMARY KEY,
  source_update_id bigint NOT NULL UNIQUE,
  chat_id bigint NOT NULL REFERENCES telegram.bindings (chat_id) ON DELETE CASCADE,
  user_id uuid NOT NULL,
  workspace_id uuid NOT NULL REFERENCES data_contract.workspaces (workspace_id),
  repo_url text NOT NULL CHECK (length(repo_url) BETWEEN 1 AND 2048),
  workflow_id text NOT NULL,
  run_id text,
  status text NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'importing', 'running', 'succeeded', 'failed', 'cancelled')),
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS jobs_chat_created_idx
  ON telegram.jobs (chat_id, created_at DESC);
CREATE INDEX IF NOT EXISTS jobs_status_idx
  ON telegram.jobs (status, updated_at);

-- Delivery outbox. Unique (chat_id, run_id, kind) is the idempotency key for a
-- run notification; the scan never depends on a successful Telegram send.
CREATE TABLE IF NOT EXISTS telegram.outbox (
  id bigserial PRIMARY KEY,
  chat_id bigint NOT NULL REFERENCES telegram.bindings (chat_id) ON DELETE CASCADE,
  run_id text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('alert', 'jsonc')),
  status text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'sending', 'sent', 'failed')),
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (chat_id, run_id, kind)
);

CREATE INDEX IF NOT EXISTS outbox_due_idx
  ON telegram.outbox (status, next_attempt_at, id);

-- Cursor/outbox state. No bot token is stored here; key is a logical bot name
-- supplied by configuration.
CREATE TABLE IF NOT EXISTS telegram.bot_state (
  key text PRIMARY KEY CHECK (key <> '' AND length(key) <= 128),
  value text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- At-most-once claim per Telegram update_id, with a retry path for failures.
CREATE TABLE IF NOT EXISTS telegram.updates (
  update_id bigint PRIMARY KEY,
  chat_id bigint,
  status text NOT NULL DEFAULT 'processing'
    CHECK (status IN ('processing', 'done', 'failed')),
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS updates_stale_idx
  ON telegram.updates (status, updated_at);

CREATE OR REPLACE FUNCTION telegram.schema_version_v1() RETURNS text
LANGUAGE sql IMMUTABLE AS $$ SELECT '010'::text $$;

COMMIT;