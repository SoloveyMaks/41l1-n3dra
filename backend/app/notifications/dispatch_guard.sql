-- Notification dispatch guard, installed after the base notification schema.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = 'runtime.completion_notifications'::regclass
          AND attname = 'retry_cancelled' AND NOT attisdropped
    ) THEN
        ALTER TABLE runtime.completion_notifications
            ADD COLUMN retry_cancelled boolean NOT NULL DEFAULT false;
        -- Legacy active rows have no durable opt-out history. Preserve real
        -- in-flight outcomes, but never resume their unproven consent.
        UPDATE runtime.completion_notifications
        SET retry_cancelled=true
        WHERE delivery_status IN ('pending','sending');
        UPDATE runtime.completion_notifications
        SET delivery_status='skipped',
            detail='Ожидающая доставка отменена обновлением политики согласия',
            lease_token=NULL,lease_until=NULL
        WHERE delivery_status IN ('pending','sending') AND authorized_at IS NULL;
    END IF;
END $$;

CREATE OR REPLACE FUNCTION runtime.lock_run_completion_dispatch() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.execution_status IN ('succeeded','failed','cancelled','interrupted') THEN
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
