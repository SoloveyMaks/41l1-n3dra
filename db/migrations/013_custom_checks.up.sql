BEGIN;
CREATE SCHEMA IF NOT EXISTS custom_checks;
SET LOCAL search_path = custom_checks, public;

-- User-defined checks are workspace-scoped definitions. They are deliberately
-- stored outside runtime.findings: execution output is a separate fact stream,
-- never a canonical finding or verdict input.
CREATE TABLE IF NOT EXISTS custom_checks.definitions (
  workspace_id uuid NOT NULL REFERENCES data_contract.workspaces (workspace_id),
  id uuid NOT NULL,
  name text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
  version text NOT NULL CHECK (length(version) BETWEEN 1 AND 32),
  language text NOT NULL CHECK (language = 'python'),
  runner text NOT NULL CHECK (runner = 'bwrap-python3'),
  source_kind text NOT NULL CHECK (source_kind IN ('script', 'template')),
  template_id text,
  source_text text NOT NULL CHECK (length(source_text) BETWEEN 1 AND 65536),
  source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
  timeout_seconds integer NOT NULL CHECK (timeout_seconds BETWEEN 1 AND 10),
  memory_mb integer NOT NULL CHECK (memory_mb BETWEEN 64 AND 512),
  max_output_bytes integer NOT NULL CHECK (max_output_bytes BETWEEN 1024 AND 65536),
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  last_validated_at timestamptz,
  enabled boolean NOT NULL DEFAULT true,
  PRIMARY KEY (workspace_id, id),
  UNIQUE (workspace_id, name, version),
  CHECK ((source_kind = 'template') = (template_id IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS custom_checks_definitions_workspace_idx
  ON custom_checks.definitions (workspace_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS custom_checks.runs (
  workspace_id uuid NOT NULL,
  id uuid NOT NULL,
  definition_id uuid NOT NULL,
  artifact_id text,
  artifact_hash text,
  workflow_run_id text,
  status text NOT NULL CHECK (status IN (
    'succeeded', 'failed', 'timeout', 'output_limit', 'contract_error', 'error'
  )),
  exit_code integer,
  duration_ms integer NOT NULL CHECK (duration_ms >= 0),
  stdout_redacted text NOT NULL DEFAULT '',
  stderr_redacted text NOT NULL DEFAULT '',
  result_json jsonb,
  diagnostics_json jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, id),
  FOREIGN KEY (workspace_id, definition_id)
    REFERENCES custom_checks.definitions (workspace_id, id)
);

CREATE INDEX IF NOT EXISTS custom_checks_runs_definition_idx
  ON custom_checks.runs (workspace_id, definition_id, created_at DESC);

CREATE INDEX IF NOT EXISTS custom_checks_runs_workflow_run_idx
  ON custom_checks.runs (workspace_id, workflow_run_id, created_at);

CREATE OR REPLACE FUNCTION custom_checks.schema_version_v1() RETURNS text
LANGUAGE sql IMMUTABLE AS $$ SELECT '013'::text $$;

COMMIT;
