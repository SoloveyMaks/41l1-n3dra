BEGIN;
SET LOCAL search_path = telegram, public;

-- Delivery filters for run notifications (mentor checklist §3.1):
--   min_severity   — run's highest finding severity must reach this threshold;
--   quiet_hours    — a local-time window during which the send is deferred to
--                    its end instead of being dropped (start = end disables);
--   project/branch — optional scope of the bound workspace's runs.
-- Defaults reproduce today's behaviour: every completed run of the bound
-- workspace is delivered, at any time, in any timezone.
-- The outbox row stays the idempotency record: filters decide send/defer/
-- cancel per claim, never a second row.
ALTER TABLE telegram.settings
  ADD COLUMN IF NOT EXISTS min_severity text NOT NULL DEFAULT 'info'
    CHECK (min_severity IN ('info', 'low', 'medium', 'high', 'critical')),
  ADD COLUMN IF NOT EXISTS quiet_start time without time zone,
  ADD COLUMN IF NOT EXISTS quiet_end time without time zone,
  ADD COLUMN IF NOT EXISTS quiet_timezone text NOT NULL DEFAULT 'UTC'
    CHECK (length(quiet_timezone) BETWEEN 1 AND 64),
  ADD COLUMN IF NOT EXISTS project_filter text NOT NULL DEFAULT ''
    CHECK (length(project_filter) <= 200),
  ADD COLUMN IF NOT EXISTS branch_filter text NOT NULL DEFAULT ''
    CHECK (length(branch_filter) <= 200),
  ADD CONSTRAINT settings_quiet_hours_pair CHECK (
    (quiet_start IS NULL) = (quiet_end IS NULL)
  );

CREATE OR REPLACE FUNCTION telegram.schema_version_v4() RETURNS text
LANGUAGE sql IMMUTABLE AS $$ SELECT '017'::text $$;

COMMIT;
