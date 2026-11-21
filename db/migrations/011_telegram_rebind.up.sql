BEGIN;
SET LOCAL search_path = telegram, public;

-- Re-bind fencing. binding_epoch increments only when the chat owner or
-- workspace changes; same-owner /start is idempotent. Jobs and outbox rows
-- carry the epoch they were created under, so a stale worker cannot act for
-- the new owner.
ALTER TABLE telegram.bindings
  ADD COLUMN IF NOT EXISTS binding_epoch bigint NOT NULL DEFAULT 1
  CHECK (binding_epoch > 0);

ALTER TABLE telegram.jobs
  ADD COLUMN IF NOT EXISTS binding_epoch bigint NOT NULL DEFAULT 1;

ALTER TABLE telegram.outbox
  ADD COLUMN IF NOT EXISTS user_id uuid,
  ADD COLUMN IF NOT EXISTS workspace_id uuid,
  ADD COLUMN IF NOT EXISTS binding_epoch bigint;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conrelid = 'telegram.outbox'::regclass
       AND conname = 'outbox_status_check'
  ) THEN
    ALTER TABLE telegram.outbox DROP CONSTRAINT outbox_status_check;
  END IF;
  ALTER TABLE telegram.outbox
    ADD CONSTRAINT outbox_status_check
    CHECK (status IN ('pending', 'sending', 'sent', 'failed', 'cancelled'));
END $$;

-- There is no historical owner for rows written before 011. Unsent rows are
-- conservatively cancelled rather than guessed; sent/failed rows stay history.
UPDATE telegram.outbox
   SET status = 'cancelled',
       last_error = 'owner changed before migration',
       updated_at = now()
 WHERE status IN ('pending', 'sending');

UPDATE telegram.outbox o
   SET user_id = b.user_id,
       workspace_id = b.workspace_id,
       binding_epoch = b.binding_epoch
  FROM telegram.bindings b
 WHERE o.chat_id = b.chat_id;

UPDATE telegram.jobs j
   SET binding_epoch = COALESCE((
       SELECT b.binding_epoch
         FROM telegram.bindings b
        WHERE b.chat_id = j.chat_id
          AND b.user_id = j.user_id
          AND b.workspace_id = j.workspace_id
   ), 1);

ALTER TABLE telegram.outbox
  ALTER COLUMN user_id SET NOT NULL,
  ALTER COLUMN workspace_id SET NOT NULL,
  ALTER COLUMN binding_epoch SET NOT NULL;

CREATE INDEX IF NOT EXISTS outbox_owner_idx
  ON telegram.outbox (chat_id, user_id, workspace_id, binding_epoch, status);

CREATE OR REPLACE FUNCTION telegram.schema_version_v2() RETURNS text
LANGUAGE sql IMMUTABLE AS $$ SELECT '011'::text $$;

COMMIT;
