BEGIN;
CREATE SCHEMA data_contract;
SET LOCAL search_path = data_contract, public;
CREATE DOMAIN sha256_hex AS text CHECK (VALUE ~ '^[0-9a-f]{64}$');
CREATE DOMAIN source_path AS text CHECK (
  VALUE <> '' AND VALUE !~ '/$' AND VALUE !~ '(^/|\\|(^|/)\.\.?(/|$)|//|^[A-Za-z]:)' AND VALUE !~ '[[:cntrl:]]'
);
CREATE TABLE workspaces (
  workspace_id uuid PRIMARY KEY,
  name text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE workflows (
  workspace_id uuid NOT NULL REFERENCES workspaces,
  workflow_id uuid NOT NULL,
  name text NOT NULL,
  draft_version integer NOT NULL DEFAULT 1 CHECK (draft_version > 0),
  draft jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(draft) = 'object'),
  PRIMARY KEY (workspace_id, workflow_id)
);
CREATE TABLE workflow_revisions (
  workspace_id uuid NOT NULL,
  workflow_revision_id uuid NOT NULL,
  workflow_id uuid NOT NULL,
  revision_number integer NOT NULL CHECK (revision_number > 0),
  definition_hash sha256_hex NOT NULL,
  definition jsonb NOT NULL CHECK (jsonb_typeof(definition) = 'object'),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, workflow_revision_id),
  UNIQUE (workspace_id, workflow_id, revision_number),
  UNIQUE (workspace_id, workflow_id, workflow_revision_id),
  FOREIGN KEY (workspace_id, workflow_id) REFERENCES workflows
);
CREATE TABLE artifacts (
  workspace_id uuid NOT NULL REFERENCES workspaces,
  artifact_id uuid NOT NULL,
  artifact_hash sha256_hex NOT NULL,
  storage_ref text NOT NULL CHECK (storage_ref <> ''),
  compressed_bytes bigint NOT NULL CHECK (compressed_bytes >= 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  purged_at timestamptz,
  PRIMARY KEY (workspace_id, artifact_id),
  UNIQUE (workspace_id, artifact_id, artifact_hash),
  CHECK (expires_at > created_at)
);
CREATE TABLE snapshots (
  workspace_id uuid NOT NULL,
  snapshot_id uuid NOT NULL,
  artifact_id uuid NOT NULL,
  artifact_hash sha256_hex NOT NULL,
  manifest_hash sha256_hex,
  sealed_at timestamptz,
  PRIMARY KEY (workspace_id, snapshot_id),
  UNIQUE (workspace_id, snapshot_id, artifact_hash),
  FOREIGN KEY (workspace_id, artifact_id, artifact_hash) REFERENCES artifacts (workspace_id, artifact_id, artifact_hash),
  CHECK ((sealed_at IS NULL) = (manifest_hash IS NULL))
);
CREATE TABLE snapshot_files (
  workspace_id uuid NOT NULL,
  snapshot_id uuid NOT NULL,
  path source_path NOT NULL,
  source_hash sha256_hex NOT NULL,
  byte_length bigint NOT NULL CHECK (byte_length BETWEEN 0 AND 9007199254740991),
  media_type text NOT NULL,
  PRIMARY KEY (workspace_id, snapshot_id, path),
  UNIQUE (workspace_id, snapshot_id, path, source_hash),
  FOREIGN KEY (workspace_id, snapshot_id) REFERENCES snapshots
);
CREATE FUNCTION valid_domain_policy_document(document jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE resource jsonb; roles jsonb;
BEGIN
  IF jsonb_typeof(document) IS DISTINCT FROM 'object' OR document - 'resources' <> '{}'::jsonb OR
     jsonb_typeof(document->'resources') IS DISTINCT FROM 'array' THEN RETURN false; END IF;
  IF jsonb_array_length(document->'resources') = 0 THEN RETURN false; END IF;
  FOR resource IN SELECT value FROM jsonb_array_elements(document->'resources') LOOP
    IF jsonb_typeof(resource) IS DISTINCT FROM 'object' OR
       jsonb_typeof(resource->'resource') IS DISTINCT FROM 'string' OR resource->>'resource' = '' THEN RETURN false; END IF;
    IF resource->>'kind' = 'role_access' THEN
      IF resource - ARRAY['kind','resource','allowedRoles'] <> '{}'::jsonb THEN RETURN false; END IF;
      roles := resource->'allowedRoles';
      IF jsonb_typeof(roles) IS DISTINCT FROM 'array' THEN RETURN false; END IF;
      IF jsonb_array_length(roles) = 0 THEN RETURN false; END IF;
    ELSIF resource->>'kind' = 'ownership' THEN
      IF resource - ARRAY['kind','resource','ownerField','trustedIdentity','roleExceptions'] <> '{}'::jsonb OR
         jsonb_typeof(resource->'ownerField') IS DISTINCT FROM 'string' OR resource->>'ownerField' = '' OR
         jsonb_typeof(resource->'trustedIdentity') IS DISTINCT FROM 'string' OR resource->>'trustedIdentity' = '' THEN RETURN false; END IF;
      roles := resource->'roleExceptions';
      IF jsonb_typeof(roles) IS DISTINCT FROM 'array' THEN RETURN false; END IF;
    ELSE RETURN false;
    END IF;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(roles) v WHERE jsonb_typeof(v) <> 'string' OR v #>> '{}' = '') OR
       (SELECT count(DISTINCT value) FROM jsonb_array_elements(roles)) <> jsonb_array_length(roles) THEN RETURN false; END IF;
  END LOOP;
  RETURN true;
END;
$$;
CREATE TABLE domain_policy_revisions (
  workspace_id uuid NOT NULL REFERENCES workspaces,
  policy_revision_id uuid NOT NULL,
  version text NOT NULL CHECK (version <> ''),
  policy_hash sha256_hex NOT NULL,
  source_ref text NOT NULL,
  approved_by text,
  approved_at timestamptz,
  policy jsonb NOT NULL CHECK (valid_domain_policy_document(policy)),
  PRIMARY KEY (workspace_id, policy_revision_id),
  UNIQUE (workspace_id, policy_revision_id, version, policy_hash),
  CHECK ((approved_by IS NULL) = (approved_at IS NULL))
);
CREATE TABLE runs (
  workspace_id uuid NOT NULL,
  run_id uuid NOT NULL,
  snapshot_id uuid NOT NULL,
  artifact_hash sha256_hex NOT NULL,
  workflow_id uuid NOT NULL,
  workflow_revision_id uuid NOT NULL,
  parser_version text NOT NULL, parser_hash sha256_hex NOT NULL,
  rules_version text NOT NULL, rules_hash sha256_hex NOT NULL,
  advisory_version text NOT NULL, advisory_hash sha256_hex NOT NULL,
  config_version text NOT NULL, scan_config_hash sha256_hex NOT NULL,
  domain_policy_revision_id uuid, domain_policy_version text, domain_policy_hash sha256_hex,
  hmac_key_version text NOT NULL,
  execution_status text NOT NULL DEFAULT 'queued' CHECK (execution_status IN ('queued','running','succeeded','failed','interrupted','cancelled')),
  security_verdict text NOT NULL DEFAULT 'pending' CHECK (security_verdict IN ('pending','pass','fail','inconclusive')),
  created_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  PRIMARY KEY (workspace_id, run_id),
  UNIQUE (workspace_id, run_id, snapshot_id),
  FOREIGN KEY (workspace_id, snapshot_id, artifact_hash) REFERENCES snapshots (workspace_id, snapshot_id, artifact_hash),
  FOREIGN KEY (workspace_id, workflow_id, workflow_revision_id) REFERENCES workflow_revisions (workspace_id, workflow_id, workflow_revision_id),
  FOREIGN KEY (workspace_id, domain_policy_revision_id, domain_policy_version, domain_policy_hash)
    REFERENCES domain_policy_revisions (workspace_id, policy_revision_id, version, policy_hash),
  CHECK ((domain_policy_revision_id IS NULL) = (domain_policy_version IS NULL)
    AND (domain_policy_revision_id IS NULL) = (domain_policy_hash IS NULL)),
  CHECK (execution_status <> 'succeeded' OR security_verdict <> 'pending')
);
CREATE UNIQUE INDEX one_active_workflow ON runs (workspace_id, workflow_id)
  WHERE execution_status IN ('queued','running');
CREATE TABLE node_runs (
  workspace_id uuid NOT NULL, run_id uuid NOT NULL, node_id text NOT NULL,
  attempt integer NOT NULL CHECK (attempt > 0),
  status text NOT NULL CHECK (status IN ('pending','running','retrying','succeeded','failed','skipped','cancelled')),
  started_at timestamptz, finished_at timestamptz,
  PRIMARY KEY (workspace_id, run_id, node_id, attempt),
  FOREIGN KEY (workspace_id, run_id) REFERENCES runs
);
CREATE TABLE analysis_bundles (
  workspace_id uuid NOT NULL, run_id uuid NOT NULL, snapshot_id uuid NOT NULL,
  schema_version text NOT NULL DEFAULT '0.1' CHECK (schema_version = '0.1'),
  bundle_hash sha256_hex,
  published_at timestamptz,
  PRIMARY KEY (workspace_id, run_id, snapshot_id),
  FOREIGN KEY (workspace_id, run_id, snapshot_id) REFERENCES runs (workspace_id, run_id, snapshot_id),
  CHECK ((bundle_hash IS NULL) = (published_at IS NULL))
);
CREATE TABLE scan_outcomes (
  workspace_id uuid NOT NULL, run_id uuid NOT NULL, snapshot_id uuid NOT NULL,
  category text NOT NULL CHECK (category IN ('SECRET','SCA','SAST')),
  status text NOT NULL CHECK (status IN ('complete','partial','failed','timeout','unsupported')),
  producer_version text NOT NULL,
  coverage_summary jsonb NOT NULL CHECK (jsonb_typeof(coverage_summary) = 'object'),
  duration_ms bigint NOT NULL CHECK (duration_ms BETWEEN 0 AND 9007199254740991),
  PRIMARY KEY (workspace_id, run_id, snapshot_id, category),
  FOREIGN KEY (workspace_id, run_id, snapshot_id) REFERENCES analysis_bundles,
  CHECK (coverage_summary->>'status' IS NOT NULL AND coverage_summary->>'status' IN ('complete','partial','unavailable')),
  CHECK (status <> 'complete' OR coverage_summary->>'status' = 'complete')
);
CREATE TABLE coverage (
  workspace_id uuid NOT NULL, run_id uuid NOT NULL, snapshot_id uuid NOT NULL,
  path source_path NOT NULL, rule_id text NOT NULL,
  category text NOT NULL,
  status text NOT NULL CHECK (status IN ('checked','partial','skipped','error','unsupported')),
  reason text NOT NULL,
  PRIMARY KEY (workspace_id, run_id, snapshot_id, category, path, rule_id),
  FOREIGN KEY (workspace_id, run_id, snapshot_id, category) REFERENCES scan_outcomes,
  FOREIGN KEY (workspace_id, snapshot_id, path) REFERENCES snapshot_files
);
CREATE TABLE graph_coverage (
  workspace_id uuid NOT NULL, run_id uuid NOT NULL, snapshot_id uuid NOT NULL,
  coverage jsonb NOT NULL CHECK (jsonb_typeof(coverage) = 'object'),
  PRIMARY KEY (workspace_id, run_id, snapshot_id),
  FOREIGN KEY (workspace_id, run_id, snapshot_id) REFERENCES analysis_bundles,
  CHECK (coverage->>'status' IS NOT NULL AND coverage->>'status' IN ('complete','partial','unavailable'))
);
CREATE TABLE evidence (
  workspace_id uuid NOT NULL, run_id uuid NOT NULL, snapshot_id uuid NOT NULL, evidence_id uuid NOT NULL,
  path source_path NOT NULL, source_hash sha256_hex NOT NULL,
  line integer NOT NULL CHECK (line > 0), column_number integer NOT NULL CHECK (column_number > 0),
  end_line integer NOT NULL, end_column integer NOT NULL CHECK (end_column > 0),
  byte_start bigint NOT NULL CHECK (byte_start >= 0), byte_end bigint NOT NULL CHECK (byte_end >= byte_start),
  kind text NOT NULL CHECK (kind IN ('source','ast','dsl','sql','manifest')),
  redacted_snippet text NOT NULL,
  producer_version text NOT NULL,
  PRIMARY KEY (workspace_id, run_id, snapshot_id, evidence_id),
  FOREIGN KEY (workspace_id, run_id, snapshot_id) REFERENCES analysis_bundles,
  FOREIGN KEY (workspace_id, snapshot_id, path, source_hash) REFERENCES snapshot_files (workspace_id, snapshot_id, path, source_hash),
  CHECK (end_line > line OR (end_line = line AND end_column >= column_number))
);
CREATE TABLE findings (
  workspace_id uuid NOT NULL, run_id uuid NOT NULL, snapshot_id uuid NOT NULL, finding_id uuid NOT NULL,
  fingerprint sha256_hex NOT NULL, fingerprint_version text NOT NULL CHECK (fingerprint_version = 'fp/v1'),
  category text NOT NULL CHECK (category IN ('SECRET','SCA','SAST')),
  rule_id text NOT NULL, rule_version text NOT NULL, rule_family text NOT NULL,
  severity text NOT NULL CHECK (severity IN ('info','low','medium','high','critical')),
  severity_basis text NOT NULL,
  confidence text NOT NULL CHECK (confidence IN ('high','medium','low')),
  confidence_basis text NOT NULL,
  status text NOT NULL CHECK (status IN ('confirmed','potential','inconclusive')),
  primary_evidence_id uuid NOT NULL,
  symbol_key text NOT NULL, normalized_context_hash sha256_hex NOT NULL, anchor_role text NOT NULL,
  secret_hmac sha256_hex,
  advisory_ids text[] NOT NULL DEFAULT '{}', coverage_warnings text[] NOT NULL DEFAULT '{}',
  PRIMARY KEY (workspace_id, run_id, snapshot_id, finding_id),
  UNIQUE (workspace_id, run_id, finding_id),
  FOREIGN KEY (workspace_id, run_id, snapshot_id, category) REFERENCES scan_outcomes,
  FOREIGN KEY (workspace_id, run_id, snapshot_id, primary_evidence_id) REFERENCES evidence,
  CHECK ((category = 'SECRET') = (secret_hmac IS NOT NULL))
);
CREATE INDEX findings_lookup ON findings (workspace_id, run_id, fingerprint, rule_id, status);
CREATE TABLE code_nodes (
  workspace_id uuid NOT NULL, run_id uuid NOT NULL, snapshot_id uuid NOT NULL, node_id uuid NOT NULL,
  kind text NOT NULL CHECK (kind IN ('actor','route','security_chain','matcher','guard','function','source','sink','data','config')),
  symbol_key text NOT NULL, label text NOT NULL, producer_version text NOT NULL,
  PRIMARY KEY (workspace_id, run_id, snapshot_id, node_id),
  UNIQUE (workspace_id, run_id, node_id),
  FOREIGN KEY (workspace_id, run_id, snapshot_id) REFERENCES analysis_bundles
);
CREATE TABLE code_edges (
  workspace_id uuid NOT NULL, run_id uuid NOT NULL, snapshot_id uuid NOT NULL, edge_id uuid NOT NULL,
  from_id uuid NOT NULL, to_id uuid NOT NULL,
  kind text NOT NULL CHECK (kind IN ('routes_to','matches','permits','requires','calls','flows_to','guards','reads','writes')),
  resolution text NOT NULL CHECK (resolution IN ('resolved','partial','unknown')),
  resolution_reason text NOT NULL, producer_version text NOT NULL,
  basis text NOT NULL CHECK (basis IN ('source','policy_assumption')),
  policy_revision_id uuid,
  PRIMARY KEY (workspace_id, run_id, snapshot_id, edge_id),
  UNIQUE (workspace_id, run_id, edge_id),
  FOREIGN KEY (workspace_id, run_id, snapshot_id, from_id) REFERENCES code_nodes,
  FOREIGN KEY (workspace_id, run_id, snapshot_id, to_id) REFERENCES code_nodes,
  FOREIGN KEY (workspace_id, policy_revision_id) REFERENCES domain_policy_revisions,
  CHECK ((basis = 'policy_assumption') = (policy_revision_id IS NOT NULL))
);
CREATE TABLE finding_evidence (
  workspace_id uuid NOT NULL, run_id uuid NOT NULL, snapshot_id uuid NOT NULL,
  finding_id uuid NOT NULL, evidence_id uuid NOT NULL,
  role text NOT NULL CHECK (role IN ('related','trace')),
  ordinal integer NOT NULL CHECK (ordinal >= 0),
  PRIMARY KEY (workspace_id, run_id, snapshot_id, finding_id, role, ordinal),
  FOREIGN KEY (workspace_id, run_id, snapshot_id, finding_id) REFERENCES findings,
  FOREIGN KEY (workspace_id, run_id, snapshot_id, evidence_id) REFERENCES evidence
);
CREATE TABLE node_evidence (
  workspace_id uuid NOT NULL, run_id uuid NOT NULL, snapshot_id uuid NOT NULL,
  node_id uuid NOT NULL, evidence_id uuid NOT NULL,
  PRIMARY KEY (workspace_id, run_id, snapshot_id, node_id, evidence_id),
  FOREIGN KEY (workspace_id, run_id, snapshot_id, node_id) REFERENCES code_nodes,
  FOREIGN KEY (workspace_id, run_id, snapshot_id, evidence_id) REFERENCES evidence
);
CREATE TABLE edge_evidence (
  workspace_id uuid NOT NULL, run_id uuid NOT NULL, snapshot_id uuid NOT NULL,
  edge_id uuid NOT NULL, evidence_id uuid NOT NULL,
  PRIMARY KEY (workspace_id, run_id, snapshot_id, edge_id, evidence_id),
  FOREIGN KEY (workspace_id, run_id, snapshot_id, edge_id) REFERENCES code_edges,
  FOREIGN KEY (workspace_id, run_id, snapshot_id, evidence_id) REFERENCES evidence
);
CREATE TABLE finding_nodes (
  workspace_id uuid NOT NULL, run_id uuid NOT NULL, snapshot_id uuid NOT NULL,
  finding_id uuid NOT NULL, node_id uuid NOT NULL,
  PRIMARY KEY (workspace_id, run_id, snapshot_id, finding_id, node_id),
  FOREIGN KEY (workspace_id, run_id, snapshot_id, finding_id) REFERENCES findings,
  FOREIGN KEY (workspace_id, run_id, snapshot_id, node_id) REFERENCES code_nodes
);
CREATE TABLE diagnostics (
  workspace_id uuid NOT NULL, run_id uuid NOT NULL, snapshot_id uuid NOT NULL, diagnostic_id uuid NOT NULL,
  category text NOT NULL, code text NOT NULL, message text NOT NULL,
  evidence_id uuid,
  PRIMARY KEY (workspace_id, run_id, snapshot_id, diagnostic_id),
  FOREIGN KEY (workspace_id, run_id, snapshot_id, category) REFERENCES scan_outcomes,
  FOREIGN KEY (workspace_id, run_id, snapshot_id, evidence_id) REFERENCES evidence
);
CREATE TABLE run_events (
  workspace_id uuid NOT NULL, run_id uuid NOT NULL, event_seq bigint NOT NULL CHECK (event_seq BETWEEN 1 AND 9007199254740991),
  type text NOT NULL CHECK (type IN ('node_status','finding_summary','coverage','run_status','delivery_status','resync_required')),
  node_id text, payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, run_id, event_seq),
  FOREIGN KEY (workspace_id, run_id) REFERENCES runs
);
CREATE TABLE schedules (
  workspace_id uuid NOT NULL, schedule_id uuid NOT NULL, workflow_id uuid NOT NULL,
  workflow_revision_id uuid NOT NULL, artifact_id uuid NOT NULL,
  cron_expression text NOT NULL, timezone text NOT NULL, enabled boolean NOT NULL DEFAULT false,
  next_fire_at timestamptz,
  PRIMARY KEY (workspace_id, schedule_id),
  UNIQUE (workspace_id, schedule_id, workflow_revision_id, artifact_id),
  FOREIGN KEY (workspace_id, workflow_id, workflow_revision_id) REFERENCES workflow_revisions (workspace_id, workflow_id, workflow_revision_id),
  FOREIGN KEY (workspace_id, artifact_id) REFERENCES artifacts
);
CREATE TABLE schedule_occurrences (
  workspace_id uuid NOT NULL, schedule_id uuid NOT NULL, scheduled_for timestamptz NOT NULL,
  workflow_revision_id uuid NOT NULL, artifact_id uuid NOT NULL,
  run_id uuid,
  status text NOT NULL CHECK (status IN ('reserved','started','skipped','artifact_expired','failed')),
  PRIMARY KEY (workspace_id, schedule_id, scheduled_for),
  UNIQUE (workspace_id, run_id),
  FOREIGN KEY (workspace_id, schedule_id, workflow_revision_id, artifact_id) REFERENCES schedules (workspace_id, schedule_id, workflow_revision_id, artifact_id),
  FOREIGN KEY (workspace_id, run_id) REFERENCES runs,
  CHECK (status <> 'started' OR run_id IS NOT NULL)
);
CREATE TABLE deliveries (
  workspace_id uuid NOT NULL, run_id uuid NOT NULL, node_id text NOT NULL, message_version integer NOT NULL CHECK (message_version > 0),
  recipient_ref text NOT NULL, credential_ref text NOT NULL,
  status text NOT NULL CHECK (status IN ('pending','sending','delivered','failed','delivery_unknown')),
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  PRIMARY KEY (workspace_id, run_id, node_id, message_version),
  FOREIGN KEY (workspace_id, run_id) REFERENCES runs
);
CREATE TABLE ai_explanations (
  workspace_id uuid NOT NULL, run_id uuid NOT NULL, snapshot_id uuid NOT NULL,
  finding_id uuid NOT NULL, explanation_id uuid NOT NULL,
  provider text NOT NULL, model text NOT NULL, model_version text NOT NULL,
  prompt_version text NOT NULL, validator_version text NOT NULL,
  validation_status text NOT NULL CHECK (validation_status IN ('accepted','rejected','unavailable')),
  redacted_explanation text NOT NULL,
  limitations text[] NOT NULL,
  PRIMARY KEY (workspace_id, run_id, explanation_id),
  FOREIGN KEY (workspace_id, run_id, snapshot_id, finding_id) REFERENCES findings
);
CREATE TABLE comparisons (
  workspace_id uuid NOT NULL, comparison_id uuid NOT NULL,
  baseline_run_id uuid NOT NULL, baseline_snapshot_id uuid NOT NULL,
  current_run_id uuid NOT NULL, current_snapshot_id uuid NOT NULL,
  comparable boolean NOT NULL, reasons text[] NOT NULL,
  published_at timestamptz,
  PRIMARY KEY (workspace_id, comparison_id),
  UNIQUE (workspace_id, comparison_id, baseline_run_id, baseline_snapshot_id, current_run_id, current_snapshot_id),
  FOREIGN KEY (workspace_id, baseline_run_id, baseline_snapshot_id) REFERENCES analysis_bundles,
  FOREIGN KEY (workspace_id, current_run_id, current_snapshot_id) REFERENCES analysis_bundles,
  CHECK (baseline_run_id <> current_run_id),
  CHECK (comparable OR cardinality(reasons) > 0)
);
CREATE TABLE comparison_items (
  workspace_id uuid NOT NULL, comparison_id uuid NOT NULL, item_id uuid NOT NULL,
  baseline_run_id uuid NOT NULL, baseline_snapshot_id uuid NOT NULL,
  current_run_id uuid NOT NULL, current_snapshot_id uuid NOT NULL,
  baseline_finding_id uuid, current_finding_id uuid,
  status text NOT NULL CHECK (status IN ('persistent','introduced','changed','resolved','inconclusive','file_deleted')),
  matching_basis text NOT NULL CHECK (matching_basis IN ('unique_anchor','ambiguous','none')),
  reason text NOT NULL CHECK (reason <> ''),
  PRIMARY KEY (workspace_id, comparison_id, item_id),
  FOREIGN KEY (workspace_id, comparison_id, baseline_run_id, baseline_snapshot_id, current_run_id, current_snapshot_id) REFERENCES comparisons
    (workspace_id, comparison_id, baseline_run_id, baseline_snapshot_id, current_run_id, current_snapshot_id),
  FOREIGN KEY (workspace_id, baseline_run_id, baseline_snapshot_id, baseline_finding_id) REFERENCES findings,
  FOREIGN KEY (workspace_id, current_run_id, current_snapshot_id, current_finding_id) REFERENCES findings,
  CHECK (baseline_finding_id IS NOT NULL OR current_finding_id IS NOT NULL),
  CHECK (status NOT IN ('persistent','changed') OR (baseline_finding_id IS NOT NULL AND current_finding_id IS NOT NULL)),
  CHECK (status NOT IN ('resolved','file_deleted') OR (baseline_finding_id IS NOT NULL AND current_finding_id IS NULL)),
  CHECK (status <> 'introduced' OR (baseline_finding_id IS NULL AND current_finding_id IS NOT NULL)),
  CHECK (status IN ('inconclusive','file_deleted') OR matching_basis = 'unique_anchor')
);
CREATE UNIQUE INDEX one_baseline_comparison_item ON comparison_items (workspace_id,comparison_id,baseline_finding_id) WHERE baseline_finding_id IS NOT NULL;
CREATE UNIQUE INDEX one_current_comparison_item ON comparison_items (workspace_id,comparison_id,current_finding_id) WHERE current_finding_id IS NOT NULL;
CREATE TABLE remediation_examples (
  workspace_id uuid NOT NULL, example_id uuid NOT NULL,
  source_run_id uuid NOT NULL, source_snapshot_id uuid NOT NULL, source_finding_id uuid NOT NULL,
  fixed_run_id uuid, fixed_snapshot_id uuid,
  source_ref text NOT NULL, content_hash sha256_hex NOT NULL,
  language text NOT NULL, framework text NOT NULL, rule_family text NOT NULL,
  rule_version text NOT NULL, scenario_version text NOT NULL,
  redacted_before text NOT NULL, redacted_fix text NOT NULL,
  status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','vetted','retired')),
  verification_basis text, reviewed_by text, reviewed_at timestamptz,
  limitations text[] NOT NULL,
  PRIMARY KEY (workspace_id, example_id),
  UNIQUE (workspace_id, example_id, content_hash),
  FOREIGN KEY (workspace_id, source_run_id, source_snapshot_id, source_finding_id) REFERENCES findings,
  FOREIGN KEY (workspace_id, fixed_run_id, fixed_snapshot_id) REFERENCES analysis_bundles,
  CHECK ((fixed_run_id IS NULL) = (fixed_snapshot_id IS NULL)),
  CHECK (status <> 'vetted' OR (verification_basis IS NOT NULL AND verification_basis <> '' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL))
);
CREATE TABLE embedding_specs (
  workspace_id uuid NOT NULL REFERENCES workspaces, embedding_spec_id uuid NOT NULL,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','active','retired')),
  provider text, model text, model_version text, dimension integer CHECK (dimension > 0),
  metric text CHECK (metric IN ('cosine','l2','inner_product')),
  query_preparation_version text, document_preparation_version text, redaction_version text,
  PRIMARY KEY (workspace_id, embedding_spec_id),
  UNIQUE (workspace_id, embedding_spec_id, dimension),
  CHECK (status <> 'pending' OR (provider IS NULL AND model IS NULL AND model_version IS NULL AND dimension IS NULL AND metric IS NULL AND query_preparation_version IS NULL AND document_preparation_version IS NULL AND redaction_version IS NULL)),
  CHECK (status = 'pending' OR (provider IS NOT NULL AND model IS NOT NULL AND model_version IS NOT NULL
    AND dimension IS NOT NULL AND metric IS NOT NULL AND query_preparation_version IS NOT NULL
    AND document_preparation_version IS NOT NULL AND redaction_version IS NOT NULL))
);
CREATE UNIQUE INDEX one_active_embedding_spec ON embedding_specs (workspace_id) WHERE status = 'active';
COMMIT;
