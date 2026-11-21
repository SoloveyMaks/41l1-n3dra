BEGIN;
SET LOCAL search_path = telegram, public;

-- Crash/restart recovery for Telegram jobs. Leases fence a live worker; an
-- expired lease may be retried only while no run exists. A job that already
-- owns a run is never re-imported: it is settled from the run or marked
-- 'interrupted' (honest terminal), preserving run history.
ALTER TABLE telegram.jobs
  ADD COLUMN IF NOT EXISTS lease_token text,
  ADD COLUMN IF NOT EXISTS lease_until timestamptz,
  ADD COLUMN IF NOT EXISTS heartbeat_at timestamptz,
  ADD COLUMN IF NOT EXISTS recovery_attempts integer NOT NULL DEFAULT 0
    CHECK (recovery_attempts >= 0);

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conrelid = 'telegram.jobs'::regclass
       AND conname = 'jobs_status_check'
  ) THEN
    ALTER TABLE telegram.jobs DROP CONSTRAINT jobs_status_check;
  END IF;
  ALTER TABLE telegram.jobs
    ADD CONSTRAINT jobs_status_check
    CHECK (status IN ('queued', 'importing', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted'));
END $$;

CREATE INDEX IF NOT EXISTS jobs_recovery_idx
  ON telegram.jobs (status, lease_until, recovery_attempts);

CREATE OR REPLACE FUNCTION telegram.schema_version_v3() RETURNS text
LANGUAGE sql IMMUTABLE AS $$ SELECT '012'::text $$;

COMMIT;
