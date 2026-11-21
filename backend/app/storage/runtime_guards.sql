-- Immutability of the runtime compatibility layer (schema version 3).
-- Артефакты и revision после записи не меняются; identity run-а не
-- переписывается, историю run-а нельзя удалить. Обновляются только поля
-- статуса/вердикта/счётчиков/времени.

CREATE FUNCTION runtime.reject_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is immutable (% forbidden)', TG_TABLE_NAME, TG_OP;
END;
$$;

CREATE TRIGGER immutable_workflow_revision
BEFORE UPDATE OR DELETE ON runtime.workflow_revisions
FOR EACH ROW EXECUTE FUNCTION runtime.reject_mutation();

CREATE TRIGGER immutable_artifact
BEFORE UPDATE OR DELETE ON runtime.artifacts
FOR EACH ROW EXECUTE FUNCTION runtime.reject_mutation();

CREATE FUNCTION runtime.guard_run_identity() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'run history cannot be deleted';
    END IF;
    IF (to_jsonb(NEW) - ARRAY['execution_status','security_verdict','total_findings',
            'critical_count','high_count','medium_count','low_count','info_count',
            'started_at','finished_at'])
       IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['execution_status','security_verdict','total_findings',
            'critical_count','high_count','medium_count','low_count','info_count',
            'started_at','finished_at'])
    THEN
        RAISE EXCEPTION 'run identity is immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER guard_run_identity
BEFORE UPDATE OR DELETE ON runtime.runs
FOR EACH ROW EXECUTE FUNCTION runtime.guard_run_identity();