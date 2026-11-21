-- Canonical publish bridge: immutable raw AnalysisBundle envelope for the
-- comparison package. Members themselves live in the existing scoped tables
-- of analysis_bundles; this table only keeps exactly what B returned plus
-- trusted provenance, after the bundle is published.

BEGIN;
SET LOCAL search_path = data_contract, public;

CREATE TABLE bundle_envelopes (
  workspace_id uuid NOT NULL,
  run_id uuid NOT NULL,
  snapshot_id uuid NOT NULL,
  envelope jsonb NOT NULL CHECK (jsonb_typeof(envelope) = 'object'),
  envelope_hash sha256_hex NOT NULL,
  document_hash sha256_hex NOT NULL,
  manifest_hash sha256_hex NOT NULL,
  normalization_version text NOT NULL CHECK (normalization_version <> ''),
  origin text NOT NULL CHECK (origin IN ('scanner', 'contract_scenario', 'manual_fixture')),
  expected_scopes_verified boolean NOT NULL,
  domain_policy_applicability text NOT NULL CHECK (domain_policy_applicability IN ('not_applicable', 'applied', 'unknown')),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, run_id, snapshot_id),
  FOREIGN KEY (workspace_id, run_id, snapshot_id) REFERENCES analysis_bundles (workspace_id, run_id, snapshot_id)
);

CREATE FUNCTION envelope_hash() RETURNS trigger LANGUAGE plpgsql SET search_path = data_contract, pg_temp AS $$
BEGIN
  NEW.envelope_hash := encode(sha256(convert_to(NEW.envelope::text, 'UTF8')), 'hex')::sha256_hex;
  RETURN NEW;
END;
$$;
CREATE TRIGGER envelope_hash BEFORE INSERT ON bundle_envelopes FOR EACH ROW EXECUTE FUNCTION envelope_hash();

CREATE FUNCTION guard_envelope() RETURNS trigger LANGUAGE plpgsql SET search_path = data_contract, pg_temp AS $$
BEGIN
  PERFORM 1 FROM analysis_bundles
    WHERE workspace_id = NEW.workspace_id AND run_id = NEW.run_id
      AND snapshot_id = NEW.snapshot_id AND published_at IS NOT NULL;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'envelope requires a published immutable bundle';
  END IF;
  PERFORM 1 FROM snapshots
    WHERE workspace_id = NEW.workspace_id AND snapshot_id = NEW.snapshot_id
      AND sealed_at IS NOT NULL AND manifest_hash = NEW.manifest_hash;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'envelope manifest hash differs from the sealed snapshot';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER guard_envelope BEFORE INSERT ON bundle_envelopes FOR EACH ROW EXECUTE FUNCTION guard_envelope();
CREATE TRIGGER immutable_envelope BEFORE UPDATE OR DELETE ON bundle_envelopes FOR EACH ROW EXECUTE FUNCTION reject_mutation();
CREATE TRIGGER forbid_truncate_envelope BEFORE TRUNCATE ON bundle_envelopes FOR EACH STATEMENT EXECUTE FUNCTION reject_mutation();

COMMIT;