BEGIN;
CREATE SCHEMA IF NOT EXISTS account;
SET LOCAL search_path = account, public;

-- Настоящие аккаунты. Схема отдельная от data_contract и от Supabase `auth`,
-- чтобы не пересекаться с чужими объектами. Пароль хранится только как
-- scrypt-строка, e-mail — нормализованный (lower/strip).
CREATE TABLE IF NOT EXISTS account.users (
  user_id uuid PRIMARY KEY,
  email text NOT NULL CHECK (email <> '' AND length(email) <= 254),
  password_hash text NOT NULL CHECK (password_hash LIKE 'scrypt$%'),
  display_name text,
  role text NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
  workspace_id uuid NOT NULL UNIQUE REFERENCES data_contract.workspaces (workspace_id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  disabled_at timestamptz
);

CREATE UNIQUE INDEX IF NOT EXISTS users_email_unique_idx
  ON account.users (lower(email));

CREATE TABLE IF NOT EXISTS account.sessions (
  session_id text PRIMARY KEY CHECK (session_id ~ '^[0-9a-f]{32}$'),
  user_id uuid NOT NULL REFERENCES account.users (user_id) ON DELETE CASCADE,
  workspace_id uuid NOT NULL,
  role text NOT NULL CHECK (role IN ('user', 'admin')),
  issued_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz,
  CHECK (expires_at > issued_at)
);

CREATE INDEX IF NOT EXISTS sessions_user_idx
  ON account.sessions (user_id, issued_at DESC);
CREATE INDEX IF NOT EXISTS sessions_live_idx
  ON account.sessions (session_id) WHERE revoked_at IS NULL;

-- Sentinel завершённой транзакции 009; не используется для авторизации.
CREATE OR REPLACE FUNCTION account.schema_version_v1() RETURNS text
LANGUAGE sql IMMUTABLE AS $$ SELECT '009'::text $$;

COMMIT;