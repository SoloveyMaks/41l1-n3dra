-- Forward notification upgrade. The original schema remains unchanged.
ALTER TABLE runtime.completion_notifications
    ADD COLUMN IF NOT EXISTS retry_cancelled boolean NOT NULL DEFAULT false;

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
