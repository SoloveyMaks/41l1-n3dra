-- Notification dispatch guard, installed after the base notification schema.
--
-- Installer contract (owned by storage.database.initialize_notification_schema):
--   * check the exact schema readiness first and do not execute this file when
--     the marker below and both triggers already exist;
--   * hold the notification DDL advisory lock;
--   * run base schema + this file under a bounded lock_timeout so a restart
--     cannot wait forever on a concurrent DDL transaction.
-- The guard is fail-closed: missing readiness stops startup instead of
-- silently dispatching with an older schema.

-- One durable migration record, independent of the retry_cancelled column.
-- A database that already ran an earlier forward file has the column but no
-- marker, so the legacy purge below is still executed exactly once.
CREATE TABLE IF NOT EXISTS runtime.notification_migrations (
    name text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

DO $$
BEGIN
    -- Serialize this one-time marker migration with itself.  The DDL advisory
    -- lock is the primary contract; this table lock keeps a second installer
    -- from observing "marker absent" while the first one is still running.
    LOCK TABLE runtime.notification_migrations IN EXCLUSIVE MODE;
    IF NOT EXISTS (
        SELECT 1 FROM runtime.notification_migrations
        WHERE name = 'legacy_active_consent_purge_v1'
    ) THEN
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute
            WHERE attrelid = 'runtime.completion_notifications'::regclass
              AND attname = 'retry_cancelled' AND NOT attisdropped
        ) THEN
            ALTER TABLE runtime.completion_notifications
                ADD COLUMN retry_cancelled boolean NOT NULL DEFAULT false;
        END IF;
        -- Legacy active rows have no durable opt-out history.  This first
        -- marker run is deliberately fail-closed: it cancels every active row,
        -- including any row created after an earlier partial hotfix, because
        -- no trustworthy per-row consent timestamp exists.  Finished rows are
        -- never rewritten; authorized in-flight rows keep their real outcome
        -- but lose the right to retry.
        UPDATE runtime.completion_notifications
        SET retry_cancelled=true
        WHERE delivery_status IN ('pending','sending');
        UPDATE runtime.completion_notifications
        SET delivery_status='skipped',
            detail='Ожидающая доставка отменена обновлением политики согласия',
            lease_token=NULL,lease_until=NULL
        WHERE delivery_status IN ('pending','sending') AND authorized_at IS NULL;
        INSERT INTO runtime.notification_migrations (name)
        VALUES ('legacy_active_consent_purge_v1')
        ON CONFLICT (name) DO NOTHING;
    END IF;
    -- A marker never authorizes a repeat purge.  If the guarded column is
    -- missing or malformed, fail closed here instead of installing triggers
    -- over an unusable schema; the installer's readiness check owns the exact
    -- default value.
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = 'runtime.completion_notifications'::regclass
          AND attname = 'retry_cancelled' AND NOT attisdropped
          AND atttypid = 'boolean'::regtype AND attnotnull AND atthasdef
    ) THEN
        RAISE EXCEPTION
            'notification schema is not ready: retry_cancelled boolean NOT NULL DEFAULT is required';
    END IF;
END $$;

CREATE OR REPLACE FUNCTION runtime.lock_run_completion_dispatch() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.execution_status IN ('succeeded','failed','cancelled','interrupted') THEN
        -- The capture trigger and settings/dispatch checks rely on a fresh
        -- READ COMMITTED statement snapshot after this lock wait.  Under
        -- REPEATABLE READ/SERIALIZABLE the AFTER trigger can still see stale
        -- enabled=true and revive a pre-opt-out completion, so fail closed.
        IF lower(current_setting('transaction_isolation')) <> 'read committed' THEN
            RAISE EXCEPTION
                'completion notifications require READ COMMITTED transaction (got %)',
                current_setting('transaction_isolation');
        END IF;
        -- Completion capture and settings changes share the dispatch lock, so
        -- opt-out cannot miss an uncommitted pending notification.
        PERFORM pg_advisory_xact_lock(
            hashtext('nedra-completion-notifications-dispatch'),
            hashtext(NEW.workspace_id::text)
        );
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS lock_run_completion_dispatch ON runtime.runs;
CREATE TRIGGER lock_run_completion_dispatch
BEFORE INSERT OR UPDATE OF execution_status ON runtime.runs
FOR EACH ROW EXECUTE FUNCTION runtime.lock_run_completion_dispatch();
