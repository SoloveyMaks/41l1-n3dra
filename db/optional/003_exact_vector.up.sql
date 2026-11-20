-- Optional: requires installed pgvector; does not select a provider or dimension.
BEGIN;
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
SET LOCAL search_path = data_contract, public;
CREATE TABLE example_embeddings (
  workspace_id uuid NOT NULL,
  example_id uuid NOT NULL,
  content_hash sha256_hex NOT NULL,
  embedding_spec_id uuid NOT NULL,
  dimension integer NOT NULL,
  embedding public.vector NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, example_id, embedding_spec_id),
  FOREIGN KEY (workspace_id, example_id, content_hash) REFERENCES remediation_examples (workspace_id, example_id, content_hash),
  FOREIGN KEY (workspace_id, embedding_spec_id, dimension) REFERENCES embedding_specs (workspace_id, embedding_spec_id, dimension),
  CHECK (public.vector_dims(embedding) = dimension),
  CHECK ((embedding OPERATOR(public.<#>) embedding) < 0)
);
CREATE FUNCTION guard_embedding() RETURNS trigger LANGUAGE plpgsql SET search_path = data_contract, public, pg_temp AS $$
BEGIN
  PERFORM 1 FROM embedding_specs WHERE workspace_id=NEW.workspace_id AND embedding_spec_id=NEW.embedding_spec_id AND status='active' FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'embedding requires active compatible spec'; END IF;
  PERFORM 1 FROM remediation_examples WHERE workspace_id=NEW.workspace_id AND example_id=NEW.example_id AND content_hash=NEW.content_hash AND status='vetted' FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'embedding requires vetted unchanged redacted example'; END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER guard_embedding BEFORE INSERT ON example_embeddings FOR EACH ROW EXECUTE FUNCTION guard_embedding();
CREATE TRIGGER immutable_embedding BEFORE UPDATE OR DELETE ON example_embeddings FOR EACH ROW EXECUTE FUNCTION reject_mutation();
CREATE TRIGGER forbid_truncate BEFORE TRUNCATE ON example_embeddings FOR EACH STATEMENT EXECUTE FUNCTION reject_mutation();
CREATE FUNCTION exact_similar_fixes(w uuid,spec uuid,q public.vector,language_filter text,framework_filter text,rule_family_filter text,result_limit integer DEFAULT 5)
RETURNS TABLE(example_id uuid,source_ref text,verification_basis text,limitations text[],redacted_fix text,distance double precision,metric text)
LANGUAGE plpgsql STABLE SET search_path = data_contract, public, pg_temp AS $$
DECLARE es embedding_specs%ROWTYPE;
BEGIN
  SELECT * INTO es FROM embedding_specs WHERE workspace_id=w AND embedding_spec_id=spec AND status='active';
  IF NOT FOUND THEN RAISE EXCEPTION 'retrieval unavailable: inactive or unknown embedding spec'; END IF;
  IF q IS NULL OR public.vector_dims(q) <> es.dimension OR (q OPERATOR(public.<#>) q) >= 0 THEN RAISE EXCEPTION 'retrieval unavailable: incompatible query vector'; END IF;
  IF result_limit IS NULL OR result_limit NOT BETWEEN 1 AND 20 OR language_filter IS NULL OR framework_filter IS NULL OR rule_family_filter IS NULL THEN RAISE EXCEPTION 'explicit retrieval filters and limit 1..20 required'; END IF;
  RETURN QUERY SELECT e.example_id,e.source_ref,e.verification_basis,e.limitations,e.redacted_fix,
    CASE es.metric WHEN 'cosine' THEN v.embedding OPERATOR(public.<=>) q
      WHEN 'l2' THEN v.embedding OPERATOR(public.<->) q
      WHEN 'inner_product' THEN v.embedding OPERATOR(public.<#>) q END AS d, es.metric
    FROM example_embeddings v JOIN remediation_examples e USING(workspace_id,example_id,content_hash)
    WHERE v.workspace_id=w AND v.embedding_spec_id=spec AND v.dimension=es.dimension AND e.status='vetted'
      AND e.language=language_filter AND e.framework=framework_filter AND e.rule_family=rule_family_filter
    ORDER BY d,e.example_id LIMIT result_limit;
END;
$$;
COMMIT;
