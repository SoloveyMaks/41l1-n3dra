BEGIN;
SET LOCAL search_path = data_contract, public;

-- Forward guard migration. 006 is historical and immutable: it created the
-- origins/content-hash tables and row immutability triggers. 007 adds the
-- deferred consistency check and TRUNCATE guards without rewriting 006.
-- This file is safe to re-run on the already-finalized 006 schema: it does
-- not insert or infer provenance, and replaces only the guard objects.

DO $$
BEGIN
  IF to_regclass('data_contract.snapshot_content_hashes') IS NULL
     OR to_regclass('data_contract.snapshot_origins') IS NULL
     OR to_regclass('data_contract.runs') IS NULL
     OR to_regclass('data_contract.artifacts') IS NULL THEN
    RAISE EXCEPTION '007: required snapshot/origin tables are missing';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conrelid = 'data_contract.runs'::regclass
       AND conname = 'runs_snapshot_content_hash_fkey'
       AND contype = 'f'
       AND confrelid = 'data_contract.snapshot_content_hashes'::regclass
  ) THEN
    RAISE EXCEPTION '007: runs snapshot-content FK is missing';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conrelid = 'data_contract.snapshot_content_hashes'::regclass
       AND contype = 'p'
  ) OR NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conrelid = 'data_contract.snapshot_origins'::regclass
       AND contype = 'p'
  ) THEN
    RAISE EXCEPTION '007: snapshot origin/content primary keys are missing';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conrelid = 'data_contract.snapshot_content_hashes'::regclass
       AND contype = 'f'
       AND confrelid = 'data_contract.snapshots'::regclass
  ) OR NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conrelid = 'data_contract.snapshot_origins'::regclass
       AND contype = 'f'
       AND confrelid = 'data_contract.snapshot_content_hashes'::regclass
  ) OR NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conrelid = 'data_contract.snapshot_origins'::regclass
       AND contype = 'f'
       AND confrelid = 'data_contract.artifacts'::regclass
  ) THEN
    RAISE EXCEPTION '007: snapshot origin/content foreign keys are missing';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger
     WHERE tgrelid = 'data_contract.snapshot_content_hashes'::regclass
       AND tgname = 'guard_snapshot_content_hash'
       AND NOT tgisinternal
       AND tgenabled = 'O'
  ) OR NOT EXISTS (
    SELECT 1 FROM pg_trigger
     WHERE tgrelid = 'data_contract.snapshot_origins'::regclass
       AND tgname = 'guard_snapshot_origin'
       AND NOT tgisinternal
       AND tgenabled = 'O'
  ) THEN
    RAISE EXCEPTION '007: historical row guards are missing or disabled';
  END IF;

  IF EXISTS (
    SELECT 1
      FROM data_contract.snapshot_content_hashes h
     WHERE NOT EXISTS (
       SELECT 1
         FROM data_contract.snapshot_origins o
        WHERE o.workspace_id = h.workspace_id
          AND o.snapshot_id = h.snapshot_id
          AND o.artifact_hash = h.artifact_hash
     )
  ) THEN
    RAISE EXCEPTION '007: content hash without origin; refusing to fabricate provenance';
  END IF;

  IF EXISTS (
    SELECT 1
      FROM data_contract.snapshot_origins o
      LEFT JOIN data_contract.artifacts a
        ON a.workspace_id = o.workspace_id
       AND a.artifact_id = o.artifact_id
       AND a.artifact_hash = o.artifact_hash
     WHERE a.artifact_id IS NULL
  ) THEN
    RAISE EXCEPTION '007: origin references a missing/mismatched artifact';
  END IF;
END;
$$;

CREATE OR REPLACE FUNCTION check_snapshot_content_origin() RETURNS trigger
LANGUAGE plpgsql SET search_path = data_contract, pg_temp AS $$
BEGIN
  PERFORM 1 FROM snapshot_origins
   WHERE workspace_id = NEW.workspace_id
     AND snapshot_id = NEW.snapshot_id
     AND artifact_hash = NEW.artifact_hash;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'snapshot content hash has no artifact origin';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS check_snapshot_content_origin ON snapshot_content_hashes;
CREATE CONSTRAINT TRIGGER check_snapshot_content_origin
AFTER INSERT OR UPDATE ON snapshot_content_hashes
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION check_snapshot_content_origin();

CREATE OR REPLACE FUNCTION guard_snapshot_origin_truncate() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'snapshot origin/content hash truncate is not allowed';
END;
$$;

DROP TRIGGER IF EXISTS guard_snapshot_content_hashes_truncate ON snapshot_content_hashes;
CREATE TRIGGER guard_snapshot_content_hashes_truncate
BEFORE TRUNCATE ON snapshot_content_hashes
FOR EACH STATEMENT EXECUTE FUNCTION guard_snapshot_origin_truncate();

DROP TRIGGER IF EXISTS guard_snapshot_origins_truncate ON snapshot_origins;
CREATE TRIGGER guard_snapshot_origins_truncate
BEFORE TRUNCATE ON snapshot_origins
FOR EACH STATEMENT EXECUTE FUNCTION guard_snapshot_origin_truncate();

-- 007-only sentinel. Its presence means the validation and guard installation
-- transaction completed; it is not used to infer any snapshot provenance.
CREATE OR REPLACE FUNCTION snapshot_origin_guards_v1() RETURNS text
LANGUAGE sql IMMUTABLE AS $$ SELECT '007'::text $$;
COMMIT;
