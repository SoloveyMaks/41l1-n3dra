-- Runtime-таблицы API (A). Контрактная схема data_contract живёт отдельно
-- и приходит из db/migrations/001..002. Колонки здесь повторяют прежние
-- SQLite-таблицы, чтобы DTO не менялись.

CREATE TABLE IF NOT EXISTS runtime.workflows (
    id text PRIMARY KEY,
    name text NOT NULL,
    description text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    latest_revision_id text
);

CREATE TABLE IF NOT EXISTS runtime.workflow_revisions (
    id text PRIMARY KEY,
    workflow_id text NOT NULL REFERENCES runtime.workflows(id) ON DELETE CASCADE,
    revision_num integer NOT NULL CHECK (revision_num > 0),
    draft_json jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    UNIQUE (workflow_id, revision_num)
);

CREATE TABLE IF NOT EXISTS runtime.artifacts (
    id text PRIMARY KEY,
    filename text NOT NULL,
    hash_sha256 text NOT NULL,
    size_bytes bigint NOT NULL,
    uncompressed_size_bytes bigint NOT NULL,
    file_count integer NOT NULL,
    kotlin_count integer NOT NULL,
    gradle_count integer NOT NULL,
    extracted_path text NOT NULL,
    created_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime.runs (
    id text PRIMARY KEY,
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
    created_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime.node_runs (
    id text PRIMARY KEY,
    run_id text NOT NULL REFERENCES runtime.runs(id) ON DELETE CASCADE,
    node_id text NOT NULL,
    kind text NOT NULL,
    status text NOT NULL,
    started_at timestamptz,
    finished_at timestamptz,
    duration_ms double precision NOT NULL DEFAULT 0.0,
    error_message text,
    input_data_json jsonb,
    output_data_json jsonb
);

CREATE TABLE IF NOT EXISTS runtime.findings (
    id text PRIMARY KEY,
    run_id text NOT NULL REFERENCES runtime.runs(id) ON DELETE CASCADE,
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
    coverage_warnings_json jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime.reports (
    run_id text PRIMARY KEY REFERENCES runtime.runs(id) ON DELETE CASCADE,
    verdict text NOT NULL,
    passport_json jsonb NOT NULL,
    ai_summary text NOT NULL,
    ai_items_json jsonb NOT NULL,
    ai_status text NOT NULL,
    readable_markdown text NOT NULL,
    official_json text NOT NULL,
    created_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime.run_events (
    seq bigserial PRIMARY KEY,
    run_id text NOT NULL REFERENCES runtime.runs(id) ON DELETE CASCADE,
    timestamp timestamptz NOT NULL,
    type text NOT NULL,
    node_id text,
    payload_json jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime.schedules (
    id text PRIMARY KEY,
    workflow_id text NOT NULL REFERENCES runtime.workflows(id) ON DELETE CASCADE,
    artifact_id text NOT NULL,
    enabled integer NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    cron_or_interval text NOT NULL,
    timezone text NOT NULL DEFAULT 'UTC',
    last_run_at timestamptz,
    next_fire_at timestamptz,
    created_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime.deliveries (
    id text PRIMARY KEY,
    run_id text NOT NULL REFERENCES runtime.runs(id) ON DELETE CASCADE,
    channel text NOT NULL,
    status text NOT NULL,
    target text NOT NULL,
    status_code integer,
    message text NOT NULL,
    timestamp timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_events_run_id ON runtime.run_events(run_id);
CREATE INDEX IF NOT EXISTS idx_findings_run_id ON runtime.findings(run_id);
CREATE INDEX IF NOT EXISTS idx_node_runs_run_id ON runtime.node_runs(run_id);