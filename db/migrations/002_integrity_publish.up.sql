BEGIN;
SET LOCAL search_path = data_contract, public;
CREATE FUNCTION reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION '% is immutable (% forbidden)', TG_TABLE_NAME, TG_OP; END;
$$;
CREATE FUNCTION guard_artifact() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP='DELETE' OR (to_jsonb(NEW)-'purged_at') IS DISTINCT FROM (to_jsonb(OLD)-'purged_at') OR OLD.purged_at IS NOT NULL OR NEW.purged_at IS NULL THEN
    RAISE EXCEPTION 'artifact metadata immutable except one-way purge timestamp';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER guard_artifact BEFORE UPDATE OR DELETE ON artifacts FOR EACH ROW EXECUTE FUNCTION guard_artifact();
CREATE TRIGGER immutable_revision BEFORE UPDATE OR DELETE ON workflow_revisions FOR EACH ROW EXECUTE FUNCTION reject_mutation();
CREATE TRIGGER immutable_policy BEFORE UPDATE OR DELETE ON domain_policy_revisions FOR EACH ROW EXECUTE FUNCTION reject_mutation();
CREATE TRIGGER immutable_event BEFORE UPDATE OR DELETE ON run_events FOR EACH ROW EXECUTE FUNCTION reject_mutation();
CREATE FUNCTION guard_explanation_source() RETURNS trigger LANGUAGE plpgsql SET search_path = data_contract, pg_temp AS $$
BEGIN
  PERFORM 1 FROM analysis_bundles WHERE workspace_id=NEW.workspace_id AND run_id=NEW.run_id AND snapshot_id=NEW.snapshot_id AND published_at IS NOT NULL FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'AI explanation requires published immutable source bundle'; END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER guard_explanation_source BEFORE INSERT ON ai_explanations FOR EACH ROW EXECUTE FUNCTION guard_explanation_source();
CREATE TRIGGER immutable_explanation BEFORE UPDATE OR DELETE ON ai_explanations FOR EACH ROW EXECUTE FUNCTION reject_mutation();

CREATE FUNCTION guard_snapshot_file() RETURNS trigger LANGUAGE plpgsql SET search_path = data_contract, pg_temp AS $$
DECLARE r record;
BEGIN
  IF TG_OP = 'UPDATE' AND (NEW.workspace_id,NEW.snapshot_id) IS DISTINCT FROM (OLD.workspace_id,OLD.snapshot_id) THEN
    RAISE EXCEPTION 'snapshot scope cannot change';
  END IF;
  IF TG_OP = 'DELETE' THEN r := OLD; ELSE r := NEW; END IF;
  PERFORM 1 FROM snapshots WHERE workspace_id = r.workspace_id AND snapshot_id = r.snapshot_id AND sealed_at IS NULL FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'snapshot is absent or sealed'; END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END;
$$;
CREATE TRIGGER guard_snapshot_file BEFORE INSERT OR UPDATE OR DELETE ON snapshot_files FOR EACH ROW EXECUTE FUNCTION guard_snapshot_file();
CREATE FUNCTION snapshot_manifest_hash(w uuid, s uuid) RETURNS sha256_hex LANGUAGE sql STABLE SET search_path = data_contract, pg_temp AS $$
  SELECT encode(sha256(convert_to(COALESCE(jsonb_agg(jsonb_build_object('path',path,'sourceHash',source_hash,'byteLength',byte_length,'mediaType',media_type) ORDER BY path COLLATE "C"),'[]'::jsonb)::text,'UTF8')),'hex')::sha256_hex
  FROM snapshot_files WHERE workspace_id = w AND snapshot_id = s;
$$;
CREATE FUNCTION guard_snapshot() RETURNS trigger LANGUAGE plpgsql SET search_path = data_contract, pg_temp AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.sealed_at IS NOT NULL THEN RAISE EXCEPTION 'insert open snapshot, add manifest, then seal'; END IF;
    RETURN NEW;
  END IF;
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'snapshot cannot be deleted'; END IF;
  IF OLD.sealed_at IS NOT NULL OR NEW.sealed_at IS NULL OR
    (to_jsonb(NEW) - ARRAY['sealed_at','manifest_hash']) IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['sealed_at','manifest_hash']) THEN
    RAISE EXCEPTION 'only open-to-sealed snapshot transition is allowed';
  END IF;
  IF NEW.manifest_hash IS DISTINCT FROM snapshot_manifest_hash(NEW.workspace_id,NEW.snapshot_id) THEN RAISE EXCEPTION 'manifest hash mismatch'; END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER guard_snapshot BEFORE INSERT OR UPDATE OR DELETE ON snapshots FOR EACH ROW EXECUTE FUNCTION guard_snapshot();
CREATE FUNCTION guard_run() RETURNS trigger LANGUAGE plpgsql SET search_path = data_contract, pg_temp AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'run cannot be deleted'; END IF;
  IF TG_OP = 'INSERT' THEN
    PERFORM 1 FROM snapshots WHERE workspace_id = NEW.workspace_id AND snapshot_id = NEW.snapshot_id AND sealed_at IS NOT NULL;
    IF NOT FOUND THEN RAISE EXCEPTION 'run requires sealed snapshot'; END IF;
  ELSIF (to_jsonb(NEW) - ARRAY['execution_status','security_verdict','finished_at']) IS DISTINCT FROM
        (to_jsonb(OLD) - ARRAY['execution_status','security_verdict','finished_at']) THEN
    RAISE EXCEPTION 'run identity and static signature are immutable';
  END IF;
  IF NEW.security_verdict='pass' THEN
    IF NOT EXISTS (SELECT 1 FROM analysis_bundles WHERE workspace_id=NEW.workspace_id AND run_id=NEW.run_id AND published_at IS NOT NULL) OR
      (SELECT count(*) FROM scan_outcomes WHERE workspace_id=NEW.workspace_id AND run_id=NEW.run_id AND status='complete') <> 3 OR
      EXISTS (SELECT 1 FROM coverage WHERE workspace_id=NEW.workspace_id AND run_id=NEW.run_id AND status <> 'checked') OR
      EXISTS (SELECT 1 FROM scan_outcomes o WHERE o.workspace_id=NEW.workspace_id AND o.run_id=NEW.run_id AND
        NOT EXISTS (SELECT 1 FROM coverage c WHERE c.workspace_id=o.workspace_id AND c.run_id=o.run_id AND c.snapshot_id=o.snapshot_id AND c.category=o.category AND c.status='checked')) THEN
      RAISE EXCEPTION 'pass requires published complete outcomes and nonempty checked coverage for every scanner family';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER guard_run BEFORE INSERT OR UPDATE OR DELETE ON runs FOR EACH ROW EXECUTE FUNCTION guard_run();

CREATE FUNCTION guard_bundle_member() RETURNS trigger LANGUAGE plpgsql SET search_path = data_contract, pg_temp AS $$
DECLARE r record;
BEGIN
  IF TG_OP = 'UPDATE' AND (NEW.workspace_id,NEW.run_id,NEW.snapshot_id) IS DISTINCT FROM (OLD.workspace_id,OLD.run_id,OLD.snapshot_id) THEN
    RAISE EXCEPTION 'bundle scope cannot change';
  END IF;
  IF TG_OP = 'DELETE' THEN r := OLD; ELSE r := NEW; END IF;
  PERFORM 1 FROM analysis_bundles WHERE workspace_id = r.workspace_id AND run_id = r.run_id AND snapshot_id = r.snapshot_id AND published_at IS NULL FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'bundle is absent or published'; END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END;
$$;
DO $$ DECLARE n text; BEGIN
  FOREACH n IN ARRAY ARRAY['scan_outcomes','coverage','graph_coverage','evidence','findings','code_nodes','code_edges','finding_evidence','node_evidence','edge_evidence','finding_nodes','diagnostics'] LOOP
    EXECUTE format('CREATE TRIGGER guard_bundle_member BEFORE INSERT OR UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION guard_bundle_member()',n);
  END LOOP;
END $$;
CREATE FUNCTION guard_evidence() RETURNS trigger LANGUAGE plpgsql SET search_path = data_contract, pg_temp AS $$
BEGIN
  PERFORM 1 FROM snapshot_files WHERE workspace_id = NEW.workspace_id AND snapshot_id = NEW.snapshot_id AND path = NEW.path AND source_hash = NEW.source_hash AND byte_length >= NEW.byte_end;
  IF NOT FOUND THEN RAISE EXCEPTION 'evidence range/hash is outside snapshot file'; END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER guard_evidence BEFORE INSERT OR UPDATE ON evidence FOR EACH ROW EXECUTE FUNCTION guard_evidence();
CREATE FUNCTION guard_edge_policy() RETURNS trigger LANGUAGE plpgsql SET search_path = data_contract, pg_temp AS $$
BEGIN
  IF NEW.policy_revision_id IS NOT NULL THEN
    PERFORM 1 FROM runs WHERE workspace_id = NEW.workspace_id AND run_id = NEW.run_id AND snapshot_id = NEW.snapshot_id AND domain_policy_revision_id = NEW.policy_revision_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'edge policy differs from run policy'; END IF;
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER guard_edge_policy BEFORE INSERT OR UPDATE ON code_edges FOR EACH ROW EXECUTE FUNCTION guard_edge_policy();

CREATE FUNCTION bundle_document(w uuid, r uuid, s uuid) RETURNS jsonb LANGUAGE plpgsql STABLE SET search_path = data_contract, pg_temp AS $$
DECLARE result jsonb; n text; rows_json jsonb;
BEGIN
  SELECT jsonb_build_object('schemaVersion','0.1','run',to_jsonb(x) - ARRAY['execution_status','security_verdict','created_at','finished_at'], 'manifestHash',y.manifest_hash)
    INTO result FROM runs x JOIN snapshots y USING (workspace_id,snapshot_id) WHERE x.workspace_id=w AND x.run_id=r AND x.snapshot_id=s;
  IF result IS NULL THEN RAISE EXCEPTION 'run not found'; END IF;
  FOREACH n IN ARRAY ARRAY['scan_outcomes','coverage','graph_coverage','evidence','findings','code_nodes','code_edges','finding_evidence','node_evidence','edge_evidence','finding_nodes','diagnostics'] LOOP
    EXECUTE format('SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text COLLATE "C"),''[]''::jsonb) FROM %I t WHERE workspace_id=$1 AND run_id=$2 AND snapshot_id=$3',n) INTO rows_json USING w,r,s;
    result := result || jsonb_build_object(n,rows_json);
  END LOOP;
  RETURN result;
END;
$$;
CREATE FUNCTION bundle_document_hash(w uuid, r uuid, s uuid) RETURNS sha256_hex LANGUAGE sql STABLE SET search_path = data_contract, pg_temp AS $$
  SELECT encode(sha256(convert_to(bundle_document(w,r,s)::text,'UTF8')),'hex')::sha256_hex;
$$;
CREATE FUNCTION guard_bundle() RETURNS trigger LANGUAGE plpgsql SET search_path = data_contract, pg_temp AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.published_at IS NOT NULL THEN RAISE EXCEPTION 'bundle must start as draft'; END IF;
    RETURN NEW;
  END IF;
  IF TG_OP = 'DELETE' OR OLD.published_at IS NOT NULL THEN RAISE EXCEPTION 'bundle cannot be deleted or changed after publication'; END IF;
  IF NEW.published_at IS NULL OR (to_jsonb(NEW) - ARRAY['published_at','bundle_hash']) IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['published_at','bundle_hash']) THEN
    RAISE EXCEPTION 'only draft-to-published transition allowed';
  END IF;
  IF (SELECT count(*) FROM scan_outcomes WHERE workspace_id=NEW.workspace_id AND run_id=NEW.run_id AND snapshot_id=NEW.snapshot_id) <> 3 THEN
    RAISE EXCEPTION 'require SECRET/SCA/SAST outcome, including explicit failure envelopes';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM graph_coverage WHERE workspace_id=NEW.workspace_id AND run_id=NEW.run_id AND snapshot_id=NEW.snapshot_id) THEN
    RAISE EXCEPTION 'bundle requires explicit graph coverage, unavailable included';
  END IF;
  IF EXISTS (SELECT 1 FROM code_edges e WHERE e.workspace_id=NEW.workspace_id AND e.run_id=NEW.run_id AND e.snapshot_id=NEW.snapshot_id AND e.basis='source'
    AND NOT EXISTS (SELECT 1 FROM edge_evidence ee WHERE (ee.workspace_id,ee.run_id,ee.snapshot_id,ee.edge_id)=(e.workspace_id,e.run_id,e.snapshot_id,e.edge_id))) THEN
    RAISE EXCEPTION 'source edge needs evidence';
  END IF;
  IF EXISTS (SELECT 1 FROM code_nodes n WHERE n.workspace_id=NEW.workspace_id AND n.run_id=NEW.run_id AND n.snapshot_id=NEW.snapshot_id AND n.kind <> 'actor'
    AND NOT EXISTS (SELECT 1 FROM node_evidence ne WHERE (ne.workspace_id,ne.run_id,ne.snapshot_id,ne.node_id)=(n.workspace_id,n.run_id,n.snapshot_id,n.node_id))) THEN
    RAISE EXCEPTION 'source node needs evidence';
  END IF;
  IF NEW.bundle_hash IS DISTINCT FROM bundle_document_hash(NEW.workspace_id,NEW.run_id,NEW.snapshot_id) THEN RAISE EXCEPTION 'bundle content hash mismatch'; END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER guard_bundle BEFORE INSERT OR UPDATE OR DELETE ON analysis_bundles FOR EACH ROW EXECUTE FUNCTION guard_bundle();
CREATE FUNCTION publish_bundle(w uuid,r uuid,s uuid,expected_hash sha256_hex,expected_counts jsonb) RETURNS sha256_hex LANGUAGE plpgsql SET search_path = data_contract, pg_temp AS $$
DECLARE b analysis_bundles%ROWTYPE; d jsonb; counts jsonb := '{}'; n text;
BEGIN
  SELECT * INTO STRICT b FROM analysis_bundles WHERE workspace_id=w AND run_id=r AND snapshot_id=s FOR UPDATE;
  d := bundle_document(w,r,s);
  FOREACH n IN ARRAY ARRAY['scan_outcomes','coverage','graph_coverage','evidence','findings','code_nodes','code_edges','finding_evidence','node_evidence','edge_evidence','finding_nodes','diagnostics'] LOOP
    counts := counts || jsonb_build_object(n,jsonb_array_length(d->n));
  END LOOP;
  IF counts IS DISTINCT FROM expected_counts THEN RAISE EXCEPTION 'bundle row counts mismatch: %',counts; END IF;
  IF b.published_at IS NOT NULL THEN
    IF b.bundle_hash IS DISTINCT FROM expected_hash THEN RAISE EXCEPTION 'idempotency conflict: run already has different bundle'; END IF;
    RETURN b.bundle_hash;
  END IF;
  UPDATE analysis_bundles SET bundle_hash=expected_hash,published_at=clock_timestamp() WHERE workspace_id=w AND run_id=r AND snapshot_id=s;
  RETURN expected_hash;
END;
$$;
CREATE VIEW published_bundles AS SELECT * FROM analysis_bundles WHERE published_at IS NOT NULL;
CREATE VIEW published_findings AS SELECT f.* FROM findings f JOIN published_bundles USING (workspace_id,run_id,snapshot_id);

CREATE FUNCTION static_signature(w uuid,r uuid) RETURNS jsonb LANGUAGE sql STABLE SET search_path = data_contract, pg_temp AS $$
  SELECT jsonb_build_array(parser_version,parser_hash,rules_version,rules_hash,advisory_version,advisory_hash,config_version,scan_config_hash,
    domain_policy_revision_id,domain_policy_version,domain_policy_hash,hmac_key_version) FROM runs WHERE workspace_id=w AND run_id=r;
$$;
CREATE FUNCTION guard_comparison_member() RETURNS trigger LANGUAGE plpgsql SET search_path = data_contract, pg_temp AS $$
DECLARE x comparisons%ROWTYPE; r record;
BEGIN
  IF TG_OP='UPDATE' AND (NEW.workspace_id,NEW.comparison_id) IS DISTINCT FROM (OLD.workspace_id,OLD.comparison_id) THEN RAISE EXCEPTION 'comparison scope immutable'; END IF;
  IF TG_OP='DELETE' THEN r:=OLD; ELSE r:=NEW; END IF;
  SELECT * INTO x FROM comparisons WHERE workspace_id=r.workspace_id AND comparison_id=r.comparison_id FOR UPDATE;
  IF NOT FOUND OR x.published_at IS NOT NULL THEN RAISE EXCEPTION 'comparison absent or published'; END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END;
$$;
CREATE TRIGGER guard_comparison_member BEFORE INSERT OR UPDATE OR DELETE ON comparison_items FOR EACH ROW EXECUTE FUNCTION guard_comparison_member();
CREATE FUNCTION guard_comparison() RETURNS trigger LANGUAGE plpgsql SET search_path = data_contract, pg_temp AS $$
DECLARE i comparison_items%ROWTYPE; before_f findings%ROWTYPE; after_f findings%ROWTYPE; p source_path;
BEGIN
  IF TG_OP='INSERT' THEN
    IF NEW.published_at IS NOT NULL THEN RAISE EXCEPTION 'comparison starts draft'; END IF;
    RETURN NEW;
  END IF;
  IF TG_OP='DELETE' OR OLD.published_at IS NOT NULL OR NEW.published_at IS NULL OR
    (to_jsonb(NEW)-'published_at') IS DISTINCT FROM (to_jsonb(OLD)-'published_at') THEN RAISE EXCEPTION 'comparison immutable except publication'; END IF;
  IF (SELECT count(*) FROM published_bundles WHERE workspace_id=NEW.workspace_id AND run_id IN (NEW.baseline_run_id,NEW.current_run_id)) <> 2 THEN RAISE EXCEPTION 'comparison requires two published bundles'; END IF;
  IF NEW.comparable AND static_signature(NEW.workspace_id,NEW.baseline_run_id) IS DISTINCT FROM static_signature(NEW.workspace_id,NEW.current_run_id) THEN RAISE EXCEPTION 'analysis signatures incompatible'; END IF;
  FOR i IN SELECT * FROM comparison_items WHERE workspace_id=NEW.workspace_id AND comparison_id=NEW.comparison_id LOOP
    IF NOT NEW.comparable AND i.status NOT IN ('inconclusive','file_deleted') THEN RAISE EXCEPTION 'incompatible run requires inconclusive'; END IF;
    SELECT * INTO before_f FROM findings WHERE workspace_id=i.workspace_id AND run_id=i.baseline_run_id AND snapshot_id=i.baseline_snapshot_id AND finding_id=i.baseline_finding_id;
    SELECT * INTO after_f FROM findings WHERE workspace_id=i.workspace_id AND run_id=i.current_run_id AND snapshot_id=i.current_snapshot_id AND finding_id=i.current_finding_id;
    IF i.status NOT IN ('inconclusive','file_deleted') THEN
      IF i.baseline_finding_id IS NOT NULL AND (SELECT count(*) FROM findings WHERE workspace_id=i.workspace_id AND run_id=i.baseline_run_id AND fingerprint=before_f.fingerprint) <> 1 THEN RAISE EXCEPTION 'baseline fingerprint ambiguous'; END IF;
      IF i.current_finding_id IS NOT NULL AND (SELECT count(*) FROM findings WHERE workspace_id=i.workspace_id AND run_id=i.current_run_id AND fingerprint=after_f.fingerprint) <> 1 THEN RAISE EXCEPTION 'current fingerprint ambiguous'; END IF;
    END IF;
    IF i.status='persistent' AND before_f.fingerprint IS DISTINCT FROM after_f.fingerprint THEN RAISE EXCEPTION 'persistent requires same fingerprint'; END IF;
    IF i.status IN ('resolved','file_deleted') THEN
      SELECT path INTO p FROM evidence WHERE workspace_id=i.workspace_id AND run_id=i.baseline_run_id AND snapshot_id=i.baseline_snapshot_id AND evidence_id=before_f.primary_evidence_id;
      IF i.status='file_deleted' THEN
        IF EXISTS (SELECT 1 FROM snapshot_files WHERE workspace_id=i.workspace_id AND snapshot_id=i.current_snapshot_id AND path=p) THEN RAISE EXCEPTION 'file still exists'; END IF;
      ELSE
        IF NOT EXISTS (SELECT 1 FROM coverage WHERE workspace_id=i.workspace_id AND run_id=i.current_run_id AND snapshot_id=i.current_snapshot_id AND path=p AND category=before_f.category AND rule_id=before_f.rule_id AND status='checked') OR
           NOT EXISTS (SELECT 1 FROM coverage WHERE workspace_id=i.workspace_id AND run_id=i.baseline_run_id AND snapshot_id=i.baseline_snapshot_id AND path=p AND category=before_f.category AND rule_id=before_f.rule_id AND status='checked') THEN RAISE EXCEPTION 'resolved requires checked file/rule in both runs'; END IF;
        IF EXISTS (SELECT 1 FROM findings WHERE workspace_id=i.workspace_id AND run_id=i.current_run_id AND fingerprint=before_f.fingerprint) THEN RAISE EXCEPTION 'pattern still present'; END IF;
        IF EXISTS (SELECT 1 FROM findings f JOIN evidence e ON (e.workspace_id,e.run_id,e.snapshot_id,e.evidence_id)=(f.workspace_id,f.run_id,f.snapshot_id,f.primary_evidence_id)
          WHERE f.workspace_id=i.workspace_id AND f.run_id=i.current_run_id AND f.category=before_f.category AND f.rule_id=before_f.rule_id AND f.symbol_key=before_f.symbol_key AND f.anchor_role=before_f.anchor_role AND e.path=p) THEN
          RAISE EXCEPTION 'same semantic anchor still has a finding: use changed or inconclusive';
        END IF;
      END IF;
    END IF;
    IF i.status='introduced' THEN
      SELECT path INTO p FROM evidence WHERE workspace_id=i.workspace_id AND run_id=i.current_run_id AND snapshot_id=i.current_snapshot_id AND evidence_id=after_f.primary_evidence_id;
      IF NOT EXISTS (SELECT 1 FROM scan_outcomes WHERE workspace_id=i.workspace_id AND run_id=i.baseline_run_id AND category=after_f.category AND status='complete') OR
         NOT EXISTS (SELECT 1 FROM coverage WHERE workspace_id=i.workspace_id AND run_id=i.current_run_id AND snapshot_id=i.current_snapshot_id AND path=p AND category=after_f.category AND rule_id=after_f.rule_id AND status='checked') THEN RAISE EXCEPTION 'introduced requires complete baseline category and checked current file/rule'; END IF;
      IF EXISTS (SELECT 1 FROM findings WHERE workspace_id=i.workspace_id AND run_id=i.baseline_run_id AND fingerprint=after_f.fingerprint) THEN RAISE EXCEPTION 'introduced fingerprint already existed'; END IF;
      IF EXISTS (SELECT 1 FROM findings f JOIN evidence e ON (e.workspace_id,e.run_id,e.snapshot_id,e.evidence_id)=(f.workspace_id,f.run_id,f.snapshot_id,f.primary_evidence_id)
        WHERE f.workspace_id=i.workspace_id AND f.run_id=i.baseline_run_id AND f.category=after_f.category AND f.rule_id=after_f.rule_id AND f.symbol_key=after_f.symbol_key AND f.anchor_role=after_f.anchor_role AND e.path=p) THEN
        RAISE EXCEPTION 'baseline semantic anchor already has finding: use changed or inconclusive';
      END IF;
      IF EXISTS (SELECT 1 FROM snapshot_files WHERE workspace_id=i.workspace_id AND snapshot_id=i.baseline_snapshot_id AND path=p) AND
         NOT EXISTS (SELECT 1 FROM coverage WHERE workspace_id=i.workspace_id AND run_id=i.baseline_run_id AND snapshot_id=i.baseline_snapshot_id AND path=p AND category=after_f.category AND rule_id=after_f.rule_id AND status='checked') THEN RAISE EXCEPTION 'introduced requires checked baseline file/rule or genuinely new path'; END IF;
    END IF;
  END LOOP;
  RETURN NEW;
END;
$$;
CREATE TRIGGER guard_comparison BEFORE INSERT OR UPDATE OR DELETE ON comparisons FOR EACH ROW EXECUTE FUNCTION guard_comparison();

CREATE FUNCTION guard_occurrence() RETURNS trigger LANGUAGE plpgsql SET search_path = data_contract, pg_temp AS $$
BEGIN
  IF NEW.run_id IS NOT NULL THEN
    PERFORM 1 FROM runs r JOIN snapshots s USING(workspace_id,snapshot_id)
      WHERE r.workspace_id=NEW.workspace_id AND r.run_id=NEW.run_id AND r.workflow_revision_id=NEW.workflow_revision_id AND s.artifact_id=NEW.artifact_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'occurrence run differs from pinned revision/artifact'; END IF;
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER guard_occurrence BEFORE INSERT OR UPDATE ON schedule_occurrences FOR EACH ROW EXECUTE FUNCTION guard_occurrence();
CREATE FUNCTION guard_schedule() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_timezone_names WHERE name=NEW.timezone) THEN RAISE EXCEPTION 'unknown IANA timezone'; END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER guard_schedule BEFORE INSERT OR UPDATE ON schedules FOR EACH ROW EXECUTE FUNCTION guard_schedule();
CREATE FUNCTION guard_example() RETURNS trigger LANGUAGE plpgsql SET search_path = data_contract, pg_temp AS $$
BEGIN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'retire examples instead of deleting'; END IF;
  IF TG_OP='UPDATE' AND OLD.status IN ('vetted','retired') AND
     ((to_jsonb(NEW)-'status') IS DISTINCT FROM (to_jsonb(OLD)-'status') OR NEW.status <> 'retired') THEN RAISE EXCEPTION 'vetted example content immutable; create new example'; END IF;
  IF NEW.status='vetted' THEN
    IF NOT EXISTS (SELECT 1 FROM published_bundles WHERE workspace_id=NEW.workspace_id AND run_id=NEW.source_run_id AND snapshot_id=NEW.source_snapshot_id) THEN RAISE EXCEPTION 'vetted example requires published source'; END IF;
    IF NEW.fixed_run_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM published_bundles WHERE workspace_id=NEW.workspace_id AND run_id=NEW.fixed_run_id AND snapshot_id=NEW.fixed_snapshot_id) THEN RAISE EXCEPTION 'fixed run not published'; END IF;
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER guard_example BEFORE INSERT OR UPDATE OR DELETE ON remediation_examples FOR EACH ROW EXECUTE FUNCTION guard_example();
CREATE FUNCTION guard_embedding_spec() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'retire embedding spec instead of deleting'; END IF;
  IF TG_OP='UPDATE' AND OLD.status <> 'pending' AND ((to_jsonb(NEW)-'status') IS DISTINCT FROM (to_jsonb(OLD)-'status') OR NEW.status <> 'retired') THEN
    RAISE EXCEPTION 'active/retired embedding spec immutable; use a new spec';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER guard_embedding_spec BEFORE UPDATE OR DELETE ON embedding_specs FOR EACH ROW EXECUTE FUNCTION guard_embedding_spec();
DO $$ DECLARE n text; BEGIN
  FOR n IN SELECT tablename FROM pg_tables WHERE schemaname='data_contract' LOOP
    EXECUTE format('CREATE TRIGGER forbid_truncate BEFORE TRUNCATE ON %I FOR EACH STATEMENT EXECUTE FUNCTION reject_mutation()',n);
  END LOOP;
END $$;
COMMIT;
