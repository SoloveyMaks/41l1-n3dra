-- Runtime compatibility layer (A). Схема data_contract из db/migrations
-- остаётся источником контрактных фактов и не ослабляется: здесь только
-- таблицы API, привязанные к workspace. Версию схемы ведёт runtime.schema_meta
-- (создаётся кодом до применения этого файла).
--
-- Правила слоя:
--   * любой id уникален внутри workspace, поэтому PK всегда начинается с
--     workspace_id;
--   * finding/node_run/report/event принадлежат конкретному run: старые
--     scanner finding id могут повторяться между прогонами, поэтому они
--     scoped по (workspace_id, run_id) и не перетирают прошлую историю;
--   * ни одного ON DELETE CASCADE: история прогонов не удаляется каскадом.

CREATE TABLE runtime.workflows (
    workspace_id uuid NOT NULL REFERENCES data_contract.workspaces (workspace_id),
    id text NOT NULL,
    name text NOT NULL,
    description text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    latest_revision_id text,
    PRIMARY KEY (workspace_id, id)
);

CREATE TABLE runtime.workflow_revisions (
    workspace_id uuid NOT NULL,
    id text NOT NULL,
    workflow_id text NOT NULL,
    revision_num integer NOT NULL CHECK (revision_num > 0),
    draft_json jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, id),
    UNIQUE (workspace_id, workflow_id, revision_num),
    FOREIGN KEY (workspace_id, workflow_id) REFERENCES runtime.workflows (workspace_id, id)
);

CREATE TABLE runtime.artifacts (
    workspace_id uuid NOT NULL REFERENCES data_contract.workspaces (workspace_id),
    id text NOT NULL,
    filename text NOT NULL,
    hash_sha256 text NOT NULL,
    size_bytes bigint NOT NULL,
    uncompressed_size_bytes bigint NOT NULL,
    file_count integer NOT NULL,
    kotlin_count integer NOT NULL,
    gradle_count integer NOT NULL,
    extracted_path text NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, id)
);

CREATE TABLE runtime.runs (
    workspace_id uuid NOT NULL REFERENCES data_contract.workspaces (workspace_id),
    id text NOT NULL,
    workflow_id text NOT NULL,
    revision_id text NOT NULL,
    artifact_id text,
    trigger_type text NOT NULL,
    execution_status text NOT NULL,
    security_verdict text NOT NULL,
    total_findings integer NOT NULL DEFAULT 0,
    critical_count integer NOT NULL DEFAULT 0,
    high_count integer NOT NULL DEFAULT 0,
    medium_count integer NOT NULL DEFAULT 0,
    low_count integer NOT NULL DEFAULT 0,
    info_count integer NOT NULL DEFAULT 0,
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, id)
);

CREATE TABLE runtime.node_runs (
    workspace_id uuid NOT NULL,
    run_id text NOT NULL,
    id text NOT NULL,
    node_id text NOT NULL,
    kind text NOT NULL,
    status text NOT NULL,
    started_at timestamptz,
    finished_at timestamptz,
    duration_ms double precision NOT NULL DEFAULT 0.0,
    error_message text,
    input_data_json jsonb,
    output_data_json jsonb,
    PRIMARY KEY (workspace_id, run_id, id),
    FOREIGN KEY (workspace_id, run_id) REFERENCES runtime.runs (workspace_id, id)
);

CREATE TABLE runtime.findings (
    workspace_id uuid NOT NULL,
    run_id text NOT NULL,
    id text NOT NULL,
    fingerprint text NOT NULL,
    analyzer text NOT NULL,
    rule_id text NOT NULL,
    rule_version text NOT NULL,
    severity text NOT NULL,
    severity_basis text NOT NULL,
    confidence text NOT NULL,
    confidence_basis text NOT NULL,
    status text NOT NULL,
    primary_location_json jsonb NOT NULL,
    related_locations_json jsonb NOT NULL,
    trace_json jsonb NOT NULL,
    redacted_evidence text NOT NULL,
    advisory_ids_json jsonb NOT NULL,
    coverage_warnings_json jsonb NOT NULL,
    PRIMARY KEY (workspace_id, run_id, id),
    FOREIGN KEY (workspace_id, run_id) REFERENCES runtime.runs (workspace_id, id)
);

CREATE TABLE runtime.reports (
    workspace_id uuid NOT NULL,
    run_id text NOT NULL,
    verdict text NOT NULL,
    passport_json jsonb NOT NULL,
    ai_summary text NOT NULL,
    ai_items_json jsonb NOT NULL,
    ai_status text NOT NULL,
    readable_markdown text NOT NULL,
    official_json text NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, run_id),
    FOREIGN KEY (workspace_id, run_id) REFERENCES runtime.runs (workspace_id, id)
);

CREATE TABLE runtime.run_events (
    seq bigserial PRIMARY KEY,
    workspace_id uuid NOT NULL,
    run_id text NOT NULL,
    timestamp timestamptz NOT NULL,
    type text NOT NULL,
    node_id text,
    payload_json jsonb NOT NULL,
    FOREIGN KEY (workspace_id, run_id) REFERENCES runtime.runs (workspace_id, id)
);

CREATE TABLE runtime.schedules (
    workspace_id uuid NOT NULL REFERENCES data_contract.workspaces (workspace_id),
    id text NOT NULL,
    workflow_id text NOT NULL,
    artifact_id text NOT NULL,
    enabled integer NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    cron_or_interval text NOT NULL,
    timezone text NOT NULL DEFAULT 'UTC',
    last_run_at timestamptz,
    next_fire_at timestamptz,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, id),
    FOREIGN KEY (workspace_id, workflow_id) REFERENCES runtime.workflows (workspace_id, id)
);

CREATE TABLE runtime.deliveries (
    workspace_id uuid NOT NULL,
    id text NOT NULL,
    run_id text NOT NULL,
    channel text NOT NULL,
    status text NOT NULL,
    target text NOT NULL,
    status_code integer,
    message text NOT NULL,
    timestamp timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, id),
    FOREIGN KEY (workspace_id, run_id) REFERENCES runtime.runs (workspace_id, id)
);

CREATE INDEX idx_run_events_run ON runtime.run_events (workspace_id, run_id);
CREATE INDEX idx_findings_run ON runtime.findings (workspace_id, run_id);
CREATE INDEX idx_node_runs_run ON runtime.node_runs (workspace_id, run_id);
CREATE INDEX idx_runs_workspace_created ON runtime.runs (workspace_id, created_at DESC);