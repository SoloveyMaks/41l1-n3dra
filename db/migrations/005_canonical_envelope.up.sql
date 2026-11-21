-- Separate the immutable source AnalysisBundle from the canonical stored bundle.
-- 004 kept one envelope; the bridge may honestly downgrade unproven coverage
-- (expectedScopesVerified=false or complete-with-holes), so the returned bundle
-- must match SQL facts while the original source stays immutable for audit.
--
-- Pre-005 rows have no canonical copy. Reconstruct it from the already stored
-- scan_outcomes.coverage_summary and graph_coverage.coverage, so no republish is
-- needed and source/document hashes stay untouched. If a fact row is missing,
-- mark that coverage partial with an explicit reason instead of a false complete.

BEGIN;
SET LOCAL search_path = data_contract, public;

ALTER TABLE bundle_envelopes RENAME COLUMN envelope TO source_envelope;
ALTER TABLE bundle_envelopes ADD COLUMN canonical_envelope jsonb;
ALTER TABLE bundle_envelopes ADD COLUMN canonical_hash sha256_hex;

-- The immutability trigger from 004 must not block this one-time backfill.
DROP TRIGGER immutable_envelope ON bundle_envelopes;

UPDATE bundle_envelopes be
   SET canonical_envelope = jsonb_set(
         jsonb_set(
           be.source_envelope,
           '{scanResults}',
           COALESCE(
             (
               SELECT jsonb_agg(
                 CASE
                   WHEN so.category IS NULL THEN
                     jsonb_set(
                       jsonb_set(elem, '{status}', '"partial"'::jsonb),
                       '{coverage}',
                       jsonb_set(
                         jsonb_set(
                           COALESCE(elem#>'{coverage}', '{}'::jsonb),
                           '{status}', '"partial"'::jsonb
                         ),
                         '{limitsReached}',
                         COALESCE(elem#>'{coverage,limitsReached}', '[]'::jsonb)
                           || to_jsonb('reconstructed: missing scan_outcome'::text)
                       )
                     )
                   ELSE
                     jsonb_set(
                       jsonb_set(elem, '{status}', to_jsonb(so.status)),
                       '{coverage}', so.coverage_summary
                     )
                 END
                 ORDER BY ord
               )
                 FROM jsonb_array_elements(be.source_envelope->'scanResults')
                      WITH ORDINALITY AS t(elem, ord)
                 LEFT JOIN data_contract.scan_outcomes so
                   ON so.workspace_id = be.workspace_id
                  AND so.run_id = be.run_id
                  AND so.snapshot_id = be.snapshot_id
                  AND so.category = elem->>'category'
             ),
             be.source_envelope->'scanResults'
           )
         ),
         '{codeFacts,coverage}',
         COALESCE(
           (
             SELECT gc.coverage
               FROM data_contract.graph_coverage gc
              WHERE gc.workspace_id = be.workspace_id
                AND gc.run_id = be.run_id
                AND gc.snapshot_id = be.snapshot_id
           ),
           jsonb_set(
             jsonb_set(
               COALESCE(be.source_envelope#>'{codeFacts,coverage}', '{}'::jsonb),
               '{status}', '"partial"'::jsonb
             ),
             '{limitsReached}',
             COALESCE(be.source_envelope#>'{codeFacts,coverage,limitsReached}', '[]'::jsonb)
               || to_jsonb('reconstructed: missing graph_coverage'::text)
           )
         )
       )
 WHERE be.canonical_envelope IS NULL;

UPDATE bundle_envelopes
   SET canonical_hash = encode(sha256(convert_to(canonical_envelope::text, 'UTF8')), 'hex')
 WHERE canonical_hash IS NULL;

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