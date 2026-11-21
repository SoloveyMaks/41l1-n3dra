-- Optional demo/fixture only: requires db/optional/003 and installed pgvector.
--
-- These tables are deliberately separate from remediation_examples and
-- example_embeddings. They hold 3-5 synthetic examples with clearly synthetic
-- vectors, are draft-only by CHECK constraint, and cannot be promoted to vetted.
-- They exist to prove storage and exact-search mechanics while no embedding
-- provider and no real vetted examples exist. They are not scanner provenance
-- and must never be shown as verified fixes or as evidence of retrieval quality.
--
-- Seed the rows with:
--   python -m backend.app.ai.vector_demo --seed --dsn <dsn>
-- The fixture lives in db/optional/fixtures/vector_demo_examples.json.
BEGIN;
SET LOCAL search_path = data_contract, public;

CREATE TABLE IF NOT EXISTS vector_demo_specs (
  workspace_id uuid NOT NULL,
  embedding_spec_id uuid NOT NULL,
  provider text NOT NULL CHECK (provider = 'demo-fixture'),
  model text NOT NULL CHECK (model <> ''),
  model_version text NOT NULL CHECK (model_version <> ''),
  dimension integer NOT NULL CHECK (dimension > 0),
  metric text NOT NULL CHECK (metric IN ('cosine', 'l2', 'inner_product')),
  query_preparation_version text NOT NULL CHECK (query_preparation_version <> ''),
  document_preparation_version text NOT NULL CHECK (document_preparation_version <> ''),
  redaction_version text NOT NULL CHECK (redaction_version <> ''),
  PRIMARY KEY (workspace_id, embedding_spec_id)
);

CREATE TABLE IF NOT EXISTS vector_demo_examples (
  workspace_id uuid NOT NULL,
  example_id uuid NOT NULL,
  source_ref text NOT NULL CHECK (source_ref LIKE 'demo/fixture:%'),
  language text NOT NULL CHECK (language <> ''),
  framework text NOT NULL CHECK (framework <> ''),
  rule_family text NOT NULL CHECK (rule_family <> ''),
  rule_version text NOT NULL CHECK (rule_version LIKE 'demo/%'),
  redacted_before text NOT NULL,
  redacted_fix text NOT NULL,
  verification_basis text NOT NULL CHECK (verification_basis <> ''),
  limitations text[] NOT NULL CHECK (cardinality(limitations) > 0),
  status text NOT NULL DEFAULT 'draft' CHECK (status = 'draft'),
  dimension integer NOT NULL CHECK (dimension > 0),
  embedding public.vector NOT NULL,
  PRIMARY KEY (workspace_id, example_id),
  CHECK (public.vector_dims(embedding) = dimension),
  CHECK ((embedding OPERATOR(public.<#>) embedding) < 0)
);

DROP FUNCTION IF EXISTS data_contract.exact_demo_fixes(
  uuid, uuid, public.vector, text, text, text, text, text, text, text,
  text, text, text, text, integer);

CREATE OR REPLACE FUNCTION exact_demo_fixes(
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
  es vector_demo_specs%ROWTYPE;
BEGIN
  SELECT * INTO es
  FROM vector_demo_specs
  WHERE workspace_id = w AND embedding_spec_id = spec;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'demo retrieval unavailable: unknown demo embedding spec';
  END IF;

  IF es.provider IS DISTINCT FROM provider_filter
     OR es.model IS DISTINCT FROM model_filter
     OR es.model_version IS DISTINCT FROM model_version_filter
     OR es.metric IS DISTINCT FROM metric_filter
     OR es.query_preparation_version IS DISTINCT FROM query_preparation_filter
     OR es.document_preparation_version IS DISTINCT FROM document_preparation_filter
     OR es.redaction_version IS DISTINCT FROM redaction_filter THEN
    RAISE EXCEPTION 'demo retrieval unavailable: demo embedding spec mismatch';
  END IF;

  IF q IS NULL OR public.vector_dims(q) <> es.dimension THEN
    RAISE EXCEPTION 'demo retrieval unavailable: incompatible query vector dimension';
  END IF;

  IF es.metric IN ('cosine', 'inner_product')
     AND (q OPERATOR(public.<#>) q) >= 0 THEN
    RAISE EXCEPTION 'demo retrieval unavailable: zero query vector for % metric', es.metric;
  END IF;

  IF status_filter IS DISTINCT FROM 'draft' THEN
    RAISE EXCEPTION 'demo retrieval unavailable: demo fixtures are draft-only';
  END IF;
  IF language_filter IS NULL OR language_filter = ''
     OR framework_filter IS NULL OR framework_filter = ''
     OR rule_family_filter IS NULL OR rule_family_filter = '' THEN
    RAISE EXCEPTION 'demo retrieval unavailable: explicit language/framework/rule filters are required';
  END IF;
  IF result_limit IS NULL OR result_limit NOT BETWEEN 1 AND 20 THEN
    RAISE EXCEPTION 'demo retrieval unavailable: result limit must be between 1 and 20';
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
           WHEN 'cosine' THEN e.embedding OPERATOR(public.<=>) q
           WHEN 'l2' THEN e.embedding OPERATOR(public.<->) q
           WHEN 'inner_product' THEN e.embedding OPERATOR(public.<#>) q
         END,
         es.metric
  FROM vector_demo_examples e
  WHERE e.workspace_id = w
    AND e.dimension = es.dimension
    AND e.status = status_filter
    AND e.language = language_filter
    AND e.framework = framework_filter
    AND e.rule_family = rule_family_filter
  ORDER BY 9, e.example_id
  LIMIT result_limit;
END;
$$;

COMMENT ON TABLE vector_demo_specs IS
  'Synthetic demo/fixture embedding spec. Not a provider choice and not a real embedding_specs row.';
COMMENT ON TABLE vector_demo_examples IS
  'Synthetic demo/fixture examples (draft-only, 16-dim token-hash vectors). '
  'Not vetted, not scanner provenance, and not evidence of retrieval quality.';
COMMENT ON FUNCTION exact_demo_fixes(
  uuid, uuid, public.vector, text, text, text, text, text, text, text,
  text, text, text, text, integer
) IS 'Exact search over synthetic demo/fixture examples only. Draft-only; never reports vetted.';

COMMIT;