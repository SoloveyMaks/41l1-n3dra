-- Optional: requires db/optional/003_exact_vector.up.sql and installed pgvector.
-- Replaces the 7-argument exact_similar_fixes with an explicit, compatible
-- contract: one active embedding spec must match provider/model/version/metric
-- and all text-preparation versions, and every filter is mandatory.
--
-- Retrieval still returns only rows whose remediation example status equals the
-- caller's status filter. The runtime defaults to 'vetted'; 'draft' exists for
-- review/demo tooling and must never be presented as a verified fix.
--
-- Defense in depth for vetted rows: the trusted admission path is
-- RemediationStore.save() with its scanner/comparison checks. Retrieval repeats
-- the durable provenance floors it can verify from stored data (fixed bundle,
-- scanner envelopes, matching static signature, resolved comparison, review
-- fields and non-empty limitations). This does not grant table privileges and
-- cannot stop a database writer from forging every referenced bundle; direct
-- writes still must be restricted by roles/grants (see docs/vetted-vector-retrieval.md).
BEGIN;
SET LOCAL search_path = data_contract, public;

DROP FUNCTION IF EXISTS data_contract.exact_similar_fixes(
  uuid, uuid, public.vector, text, text, text, integer);

CREATE OR REPLACE FUNCTION exact_similar_fixes(
  w uuid,
  spec uuid,
  q public.vector,
  provider_filter text,
  model_filter text,
  model_version_filter text,
  metric_filter text,
  query_preparation_filter text,
  document_preparation_filter text,
  redaction_filter text,
  language_filter text,
  framework_filter text,
  rule_family_filter text,
  status_filter text,
  result_limit integer DEFAULT 5
)
RETURNS TABLE(
  example_id uuid,
  source_ref text,
  redacted_before text,
  redacted_fix text,
  rule_version text,
  verification_basis text,
  limitations text[],
  example_status text,
  distance double precision,
  metric text
)
LANGUAGE plpgsql
STABLE
SET search_path = data_contract, public, pg_temp
AS $$
DECLARE
  es embedding_specs%ROWTYPE;
BEGIN
  SELECT * INTO es
  FROM embedding_specs
  WHERE workspace_id = w AND embedding_spec_id = spec AND status = 'active';

  IF NOT FOUND THEN
    RAISE EXCEPTION 'retrieval unavailable: inactive or unknown embedding spec';
  END IF;

  -- One model/version/dimension/metric and one text-preparation pipeline.
  -- A value read from the wrong row is a configuration error, not a fallback.
  IF es.provider IS DISTINCT FROM provider_filter
     OR es.model IS DISTINCT FROM model_filter
     OR es.model_version IS DISTINCT FROM model_version_filter
     OR es.metric IS DISTINCT FROM metric_filter
     OR es.query_preparation_version IS DISTINCT FROM query_preparation_filter
     OR es.document_preparation_version IS DISTINCT FROM document_preparation_filter
     OR es.redaction_version IS DISTINCT FROM redaction_filter THEN
    RAISE EXCEPTION 'retrieval unavailable: embedding spec does not match configured model/versions';
  END IF;

  IF q IS NULL OR public.vector_dims(q) <> es.dimension THEN
    RAISE EXCEPTION 'retrieval unavailable: incompatible query vector dimension';
  END IF;

  -- Cosine and inner product are undefined for a zero vector. L2 is defined,
  -- so a zero query is rejected only where the metric actually needs a norm.
  IF es.metric IN ('cosine', 'inner_product')
     AND (q OPERATOR(public.<#>) q) >= 0 THEN
    RAISE EXCEPTION 'retrieval unavailable: zero query vector for % metric', es.metric;
  END IF;

  IF status_filter IS NULL OR status_filter NOT IN ('vetted', 'draft') THEN
    RAISE EXCEPTION 'retrieval unavailable: status filter must be vetted or draft';
  END IF;
  IF language_filter IS NULL OR language_filter = ''
     OR framework_filter IS NULL OR framework_filter = ''
     OR rule_family_filter IS NULL OR rule_family_filter = '' THEN
    RAISE EXCEPTION 'retrieval unavailable: explicit language/framework/rule filters are required';
  END IF;
  IF result_limit IS NULL OR result_limit NOT BETWEEN 1 AND 20 THEN
    RAISE EXCEPTION 'retrieval unavailable: result limit must be between 1 and 20';
  END IF;

  RETURN QUERY
  SELECT e.example_id,
         e.source_ref,
         e.redacted_before,
         e.redacted_fix,
         e.rule_version,
         e.verification_basis,
         e.limitations,
         e.status,
         CASE es.metric
           WHEN 'cosine' THEN v.embedding OPERATOR(public.<=>) q
           WHEN 'l2' THEN v.embedding OPERATOR(public.<->) q
           WHEN 'inner_product' THEN v.embedding OPERATOR(public.<#>) q
         END,
         es.metric
  FROM example_embeddings v
  JOIN remediation_examples e
    USING (workspace_id, example_id, content_hash)
  WHERE v.workspace_id = w
    AND v.embedding_spec_id = spec
    AND v.dimension = es.dimension
    AND e.status = status_filter
    AND e.language = language_filter
    AND e.framework = framework_filter
    AND e.rule_family = rule_family_filter
    AND e.verification_basis IS NOT NULL
    AND e.verification_basis <> ''
    AND (
      e.status <> 'vetted'
      OR (
        e.fixed_run_id IS NOT NULL
        AND e.fixed_snapshot_id IS NOT NULL
        AND e.reviewed_by IS NOT NULL
        AND btrim(e.reviewed_by) <> ''
        AND e.reviewed_at IS NOT NULL
        AND cardinality(e.limitations) > 0
        AND EXISTS (
          SELECT 1 FROM analysis_bundles ab
          WHERE ab.workspace_id = e.workspace_id
            AND ab.run_id = e.source_run_id
            AND ab.snapshot_id = e.source_snapshot_id
            AND ab.published_at IS NOT NULL
        )
        AND EXISTS (
          SELECT 1 FROM analysis_bundles ab
          WHERE ab.workspace_id = e.workspace_id
            AND ab.run_id = e.fixed_run_id
            AND ab.snapshot_id = e.fixed_snapshot_id
            AND ab.published_at IS NOT NULL
        )
        AND EXISTS (
          SELECT 1 FROM bundle_envelopes be
          WHERE be.workspace_id = e.workspace_id
            AND be.run_id = e.source_run_id
            AND be.snapshot_id = e.source_snapshot_id
            AND be.origin = 'scanner'
            AND be.expected_scopes_verified
        )
        AND EXISTS (
          SELECT 1 FROM bundle_envelopes be
          WHERE be.workspace_id = e.workspace_id
            AND be.run_id = e.fixed_run_id
            AND be.snapshot_id = e.fixed_snapshot_id
            AND be.origin = 'scanner'
            AND be.expected_scopes_verified
        )
        AND data_contract.static_signature(e.workspace_id, e.source_run_id)
            IS NOT DISTINCT FROM
            data_contract.static_signature(e.workspace_id, e.fixed_run_id)
        AND EXISTS (
          SELECT 1
          FROM comparisons c
          JOIN comparison_items ci
            ON ci.workspace_id = c.workspace_id
           AND ci.comparison_id = c.comparison_id
          WHERE c.workspace_id = e.workspace_id
            AND c.published_at IS NOT NULL
            AND c.comparable
            AND c.baseline_run_id = e.source_run_id
            AND c.baseline_snapshot_id = e.source_snapshot_id
            AND c.current_run_id = e.fixed_run_id
            AND c.current_snapshot_id = e.fixed_snapshot_id
            AND ci.baseline_finding_id = e.source_finding_id
            AND ci.current_finding_id IS NULL
            AND ci.status = 'resolved'
        )
      )
    )
  ORDER BY 9, e.example_id
  LIMIT result_limit;
END;
$$;

COMMENT ON FUNCTION exact_similar_fixes(
  uuid, uuid, public.vector, text, text, text, text, text, text, text,
  text, text, text, text, integer
) IS 'Exact pgvector search over vetted (or explicitly draft) remediation examples. '
    'Requires the active embedding spec to match the configured provider, model, '
    'version, metric and preparation versions. Vetted rows additionally require '
    'stored provenance floors: fixed published bundle, scanner envelopes with '
    'verified scopes, matching static signature, resolved published comparison, '
    'review fields and non-empty limitations. Distance is a similarity score, '
    'not a human applicability or verification judgment.';

COMMIT;