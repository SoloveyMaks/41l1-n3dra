-- Separate the immutable source AnalysisBundle from the canonical stored bundle.
-- 004 kept one envelope; the bridge may honestly downgrade unproven coverage
-- (expectedScopesVerified=false or complete-with-holes), so the returned bundle
-- must match SQL facts while the original source stays immutable for audit.

BEGIN;
SET LOCAL search_path = data_contract, public;

ALTER TABLE bundle_envelopes RENAME COLUMN envelope TO source_envelope;
ALTER TABLE bundle_envelopes ADD COLUMN canonical_envelope jsonb;
ALTER TABLE bundle_envelopes ADD COLUMN canonical_hash sha256_hex;

-- Pre-005 rows only have the source. They are preserved as-is; canonical_hash
-- equals the source hash for them. Republish to obtain a downgraded canonical copy.
-- The immutability trigger from 004 must not block this one-time backfill.
DROP TRIGGER immutable_envelope ON bundle_envelopes;
UPDATE bundle_envelopes SET canonical_envelope = source_envelope WHERE canonical_envelope IS NULL;
UPDATE bundle_envelopes SET canonical_hash = encode(sha256(convert_to(canonical_envelope::text, 'UTF8')), 'hex') WHERE canonical_hash IS NULL;
ALTER TABLE bundle_envelopes ALTER COLUMN canonical_envelope SET NOT NULL;
ALTER TABLE bundle_envelopes ALTER COLUMN canonical_hash SET NOT NULL;
CREATE TRIGGER immutable_envelope BEFORE UPDATE OR DELETE ON bundle_envelopes FOR EACH ROW EXECUTE FUNCTION reject_mutation();

CREATE OR REPLACE FUNCTION envelope_hash() RETURNS trigger LANGUAGE plpgsql SET search_path = data_contract, pg_temp AS $$
BEGIN
  NEW.envelope_hash := encode(sha256(convert_to(NEW.source_envelope::text, 'UTF8')), 'hex')::sha256_hex;
  RETURN NEW;
END;
$$;

CREATE FUNCTION canonical_envelope_hash() RETURNS trigger LANGUAGE plpgsql SET search_path = data_contract, pg_temp AS $$
BEGIN
  NEW.canonical_hash := encode(sha256(convert_to(NEW.canonical_envelope::text, 'UTF8')), 'hex')::sha256_hex;
  RETURN NEW;
END;
$$;
CREATE TRIGGER canonical_envelope_hash BEFORE INSERT ON bundle_envelopes FOR EACH ROW EXECUTE FUNCTION canonical_envelope_hash();

COMMIT;