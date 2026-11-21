BEGIN;
SET LOCAL search_path = telegram, public;

-- Binding writers take this lock before issue/consume/unbind/move, including
-- operations for a chat that has no binding row yet.
SELECT pg_advisory_xact_lock(741205917);

DO $$
DECLARE
    relation_name text;
    relation_id regclass;
    persisted_max bigint := 0;
    relation_max bigint;
    sequence_last bigint;
    sequence_called boolean;
    next_epoch bigint;
BEGIN
    IF to_regclass('telegram.bindings') IS NULL THEN
        RAISE EXCEPTION '013: telegram.bindings is required';
    END IF;

    FOREACH relation_name IN ARRAY ARRAY['bindings', 'jobs', 'outbox'] LOOP
        relation_id := to_regclass(format('telegram.%I', relation_name));
        IF relation_id IS NULL THEN
            CONTINUE;
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute
             WHERE attrelid = relation_id AND attname = 'binding_epoch'
               AND atttypid = 'bigint'::regtype AND NOT attisdropped
        ) THEN
            RAISE EXCEPTION '013: telegram.% requires bigint binding_epoch', relation_name;
        END IF;
        -- Also exclude legacy writers that do not yet take the advisory lock
        -- while the persisted maximum is read and the sequence is seeded.
        EXECUTE format('LOCK TABLE telegram.%I IN SHARE ROW EXCLUSIVE MODE', relation_name);
        EXECUTE format('SELECT max(binding_epoch) FROM telegram.%I', relation_name)
           INTO relation_max;
        persisted_max := greatest(persisted_max, coalesce(relation_max, 0));
    END LOOP;

    CREATE SEQUENCE IF NOT EXISTS telegram.binding_epoch_seq AS bigint
        INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807
        START WITH 1 CACHE 1 NO CYCLE;
    IF NOT EXISTS (
        SELECT 1 FROM pg_sequence
         WHERE seqrelid = 'telegram.binding_epoch_seq'::regclass
           AND seqtypid = 'bigint'::regtype AND seqincrement = 1
           AND seqmin = 1 AND seqmax = 9223372036854775807
           AND seqcache = 1 AND NOT seqcycle
    ) THEN
        RAISE EXCEPTION '013: unexpected binding generation sequence definition';
    END IF;
    -- ALTER holds the sequence DDL lock until commit, fencing concurrent
    -- nextval while its already reserved high-water mark is inspected.
    ALTER SEQUENCE telegram.binding_epoch_seq AS bigint
        INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 CACHE 1 NO CYCLE;
    SELECT last_value, is_called INTO sequence_last, sequence_called
      FROM telegram.binding_epoch_seq;
    IF sequence_called AND sequence_last = 9223372036854775807
       OR persisted_max = 9223372036854775807 THEN
        RAISE EXCEPTION '013: binding generation exhausted; refusing wraparound';
    END IF;
    next_epoch := greatest(
        persisted_max + 1,
        sequence_last + CASE WHEN sequence_called THEN 1 ELSE 0 END
    );
    -- RESTART is transactional and does not lower an already issued/reserved
    -- generation. Existing bindings/jobs/outbox epochs remain unchanged.
    EXECUTE format('ALTER SEQUENCE telegram.binding_epoch_seq RESTART WITH %s', next_epoch);
END $$;

CREATE OR REPLACE FUNCTION telegram.binding_epoch_sequence_v1() RETURNS text
LANGUAGE sql IMMUTABLE AS $$ SELECT '013'::text $$;
COMMIT;
