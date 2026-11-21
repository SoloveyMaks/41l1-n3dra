BEGIN;
SET LOCAL search_path = data_contract, public;
-- A content snapshot is immutable and may arrive through several intake
-- artifacts. Keep its first identity in snapshots for compatibility, and
-- record every intake origin separately. Runs reference the content-hash
-- relation, so the same snapshot can back independent runs/artifacts.
CREATE TABLE snapshot_content_hashes (
  workspace_id uuid NOT NULL,
  snapshot_id uuid NOT NULL,
  artifact_hash sha256_hex NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, snapshot_id, artifact_hash),
  FOREIGN KEY (workspace_id, snapshot_id) REFERENCES snapshots
);

CREATE TABLE snapshot_origins (
  workspace_id uuid NOT NULL,
  snapshot_id uuid NOT NULL,
  artifact_id uuid NOT NULL,
  artifact_hash sha256_hex NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, snapshot_id, artifact_id),
  FOREIGN KEY (workspace_id, snapshot_id, artifact_hash)
    REFERENCES snapshot_content_hashes (workspace_id, snapshot_id, artifact_hash),
  FOREIGN KEY (workspace_id, artifact_id, artifact_hash)
    REFERENCES artifacts (workspace_id, artifact_id, artifact_hash)
);

INSERT INTO snapshot_content_hashes (workspace_id, snapshot_id, artifact_hash)
SELECT workspace_id, snapshot_id, artifact_hash FROM snapshots
ON CONFLICT (workspace_id, snapshot_id, artifact_hash) DO NOTHING;

INSERT INTO snapshot_origins (workspace_id, snapshot_id, artifact_id, artifact_hash)
SELECT workspace_id, snapshot_id, artifact_id, artifact_hash FROM snapshots
ON CONFLICT (workspace_id, snapshot_id, artifact_id) DO NOTHING;

DO $$
DECLARE constraint_name text;
BEGIN
  SELECT conname INTO constraint_name
    FROM pg_constraint
   WHERE conrelid = 'data_contract.runs'::regclass
     AND contype = 'f'
     AND confrelid = 'data_contract.snapshots'::regclass;
  IF constraint_name IS NOT NULL THEN
    EXECUTE format('ALTER TABLE data_contract.runs DROP CONSTRAINT %I', constraint_name);
  END IF;
END $$;

ALTER TABLE runs
  ADD CONSTRAINT runs_snapshot_content_hash_fkey
  FOREIGN KEY (workspace_id, snapshot_id, artifact_hash)
  REFERENCES snapshot_content_hashes (workspace_id, snapshot_id, artifact_hash);

CREATE FUNCTION guard_snapshot_origin() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'snapshot origin/content hash is immutable';
END;
$$;

CREATE TRIGGER guard_snapshot_content_hash
BEFORE UPDATE OR DELETE ON snapshot_content_hashes
FOR EACH ROW EXECUTE FUNCTION guard_snapshot_origin();

CREATE TRIGGER guard_snapshot_origin
BEFORE UPDATE OR DELETE ON snapshot_origins
FOR EACH ROW EXECUTE FUNCTION guard_snapshot_origin();

-- A content hash is only valid while at least one intake origin backs it.
-- Deferred check: CanonicalPublisher inserts the hash and origin in one
-- transaction; arbitrary/direct inserts without an origin fail at commit.
CREATE FUNCTION check_snapshot_content_origin() RETURNS trigger
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

CREATE CONSTRAINT TRIGGER check_snapshot_content_origin
AFTER INSERT OR UPDATE ON snapshot_content_hashes
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION check_snapshot_content_origin();

-- Row triggers do not fire for TRUNCATE. Keep the append-only facts in place
-- for ordinary SQL paths; DB owners/superusers can still disable/drop triggers.
CREATE FUNCTION guard_snapshot_origin_truncate() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'snapshot origin/content hash truncate is not allowed';
END;
$$;

CREATE TRIGGER guard_snapshot_content_hashes_truncate
BEFORE TRUNCATE ON snapshot_content_hashes
FOR EACH STATEMENT EXECUTE FUNCTION guard_snapshot_origin_truncate();

CREATE TRIGGER guard_snapshot_origins_truncate
BEFORE TRUNCATE ON snapshot_origins
FOR EACH STATEMENT EXECUTE FUNCTION guard_snapshot_origin_truncate();
COMMIT;
