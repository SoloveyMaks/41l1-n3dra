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
COMMIT;
