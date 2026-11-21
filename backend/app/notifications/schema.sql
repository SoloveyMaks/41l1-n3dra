-- Durable completion inbox + webhook outbox, scoped by workspace.
-- Installed by store.initialize() from the NotificationRuntime lifespan hook.
-- The trigger only records terminal runs: queued/running runs never appear as
-- notifications, and a skipped delivery never turns into "delivered".

CREATE TABLE IF NOT EXISTS runtime.notification_settings (
    workspace_id uuid PRIMARY KEY REFERENCES data_contract.workspaces(workspace_id),
    enabled boolean NOT NULL DEFAULT false,
    webhook_url text NOT NULL DEFAULT '',
    allow_local boolean NOT NULL DEFAULT false,
    allow_external boolean NOT NULL DEFAULT false,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS runtime.completion_notifications (
    workspace_id uuid NOT NULL,
    run_id text NOT NULL,
    enabled boolean NOT NULL DEFAULT false,
    webhook_url text NOT NULL DEFAULT '',
    allow_local boolean NOT NULL DEFAULT false,
    allow_external boolean NOT NULL DEFAULT false,
    execution_status text NOT NULL,
    security_verdict text NOT NULL,
    delivery_status text NOT NULL DEFAULT 'skipped' CHECK
        (delivery_status IN ('pending','sending','delivered','failed','skipped')),
    attempts integer NOT NULL DEFAULT 0,
    status_code integer,
    detail text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz NOT NULL DEFAULT now(),
    delivered_at timestamptz,
    read_at timestamptz,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    lease_token text,
    lease_until timestamptz,
    PRIMARY KEY(workspace_id, run_id),
    FOREIGN KEY(workspace_id, run_id) REFERENCES runtime.runs(workspace_id, id)
);

CREATE INDEX IF NOT EXISTS completion_notifications_pending
    ON runtime.completion_notifications(next_attempt_at)
    WHERE delivery_status IN ('pending','sending');
CREATE INDEX IF NOT EXISTS completion_notifications_inbox
    ON runtime.completion_notifications(workspace_id, completed_at DESC);

CREATE OR REPLACE FUNCTION runtime.capture_run_completion() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.execution_status NOT IN ('succeeded','failed','cancelled','interrupted') THEN
        RETURN NEW;
    END IF;
    INSERT INTO runtime.completion_notifications
        (workspace_id, run_id, enabled, webhook_url, allow_local, allow_external,
         execution_status, security_verdict, delivery_status, detail, completed_at)
    SELECT NEW.workspace_id, NEW.id, coalesce(s.enabled,false), coalesce(s.webhook_url,''),
           coalesce(s.allow_local,false), coalesce(s.allow_external,false),
           NEW.execution_status, NEW.security_verdict,
           CASE WHEN coalesce(s.enabled,false) THEN 'pending' ELSE 'skipped' END,
           CASE WHEN coalesce(s.enabled,false) THEN 'Ожидает отправки'
                ELSE 'Внешняя доставка выключена: уведомление осталось в журнале' END,
           coalesce(NEW.finished_at, now())
      FROM (SELECT 1) seed LEFT JOIN runtime.notification_settings s
        ON s.workspace_id = NEW.workspace_id
    ON CONFLICT (workspace_id, run_id) DO NOTHING;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS capture_run_completion ON runtime.runs;
CREATE TRIGGER capture_run_completion AFTER INSERT OR UPDATE OF execution_status ON runtime.runs
FOR EACH ROW EXECUTE FUNCTION runtime.capture_run_completion();