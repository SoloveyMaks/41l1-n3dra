BEGIN;
CREATE SCHEMA IF NOT EXISTS profile;
SET LOCAL search_path = profile, public;

-- Профиль здесь — это НЕ аккаунт и НЕ доказательство владения запуском.
-- Это стабильный на время жизни подписанной сессии псевдоним (HMAC от nonce
-- cookie `cg_session`), под которым пользователь может отметить свои запуски.
-- Схема отделена от data_contract: канонические факты анализа не мутируют,
-- строки профиля можно удалить без потери истории запусков.
CREATE TABLE IF NOT EXISTS profile.session_profiles (
  workspace_id uuid NOT NULL REFERENCES data_contract.workspaces (workspace_id),
  profile_id text NOT NULL CHECK (profile_id ~ '^p_[0-9a-f]{32}$'),
  created_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, profile_id)
);

CREATE TABLE IF NOT EXISTS profile.run_bookmarks (
  workspace_id uuid NOT NULL,
  profile_id text NOT NULL,
  run_id text NOT NULL CHECK (run_id <> '' AND length(run_id) <= 128),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, profile_id, run_id),
  FOREIGN KEY (workspace_id, profile_id)
    REFERENCES profile.session_profiles (workspace_id, profile_id)
    ON DELETE CASCADE
);

-- История читается всегда с фильтром (workspace_id, profile_id) и сортировкой
-- по времени отметки.
CREATE INDEX IF NOT EXISTS run_bookmarks_profile_created_idx
  ON profile.run_bookmarks (workspace_id, profile_id, created_at DESC);

-- Sentinel: транзакция 008 дошла до конца. Используется только для
-- диагностики, не для вывода о владении запусками.
CREATE OR REPLACE FUNCTION profile.schema_version_v1() RETURNS text
LANGUAGE sql IMMUTABLE AS $$ SELECT '008'::text $$;

COMMIT;