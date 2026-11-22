BEGIN;

-- Public workflow template library: a published draft is immutable, secrets
-- are stripped by the API before insert, and any authenticated workspace may
-- copy it into its own scenario.
CREATE TABLE IF NOT EXISTS data_contract.workflow_templates (
    template_id text PRIMARY KEY,
    workspace_id uuid NOT NULL REFERENCES data_contract.workspaces (workspace_id),
    name text NOT NULL,
    description text NOT NULL DEFAULT '',
    draft_json jsonb NOT NULL,
    published_by text NOT NULL DEFAULT '',
    published_at timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS workflow_templates_published_at_idx
    ON data_contract.workflow_templates (published_at DESC);

COMMIT;
