"""Durable inbox and a bounded completion-webhook outbox, scoped by workspace.

The outbox row is a *snapshot* of the delivery settings at completion time.
Changing settings later never rewrites history: an in-flight row is skipped if
the URL or permission flags changed, and a skipped row stays skipped.
"""
from pathlib import Path
from uuid import UUID, uuid4

from backend.app.storage.database import get_pool


def _workspace(value) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def initialize():
    with get_pool().connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('nedra-completion-notifications-v1'))")
        conn.execute(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))


def settings(workspace):
    with get_pool().connection() as conn:
        row = conn.execute(
            "SELECT enabled, webhook_url, allow_local, allow_external "
            "FROM runtime.notification_settings WHERE workspace_id=%s",
            (_workspace(workspace),),
        ).fetchone()
    if not row:
        return dict(enabled=False, webhook_url='', allow_local=False, allow_external=False)
    return dict(row)


def save_settings(workspace, value):
    ws = _workspace(workspace)
    with get_pool().connection() as conn:
        conn.execute(
            """INSERT INTO runtime.notification_settings
               (workspace_id,enabled,webhook_url,allow_local,allow_external)
               VALUES (%s,%s,%s,%s,%s)
               ON CONFLICT (workspace_id) DO UPDATE SET enabled=EXCLUDED.enabled,
               webhook_url=EXCLUDED.webhook_url,allow_local=EXCLUDED.allow_local,
               allow_external=EXCLUDED.allow_external,updated_at=now()""",
            (ws, value.enabled, value.webhook_url, value.allow_local, value.allow_external),
        )
        if not value.enabled:
            conn.execute(
                """UPDATE runtime.completion_notifications SET delivery_status='skipped',
                   detail='Доставка выключена пользователем до отправки',
                   lease_token=NULL,lease_until=NULL
                   WHERE workspace_id=%s AND delivery_status IN ('pending','sending')""",
                (ws,),
            )
    return settings(ws)


def inbox(workspace, limit=50):
    ws = _workspace(workspace)
    with get_pool().connection() as conn:
        rows = conn.execute(
            """SELECT n.run_id,n.execution_status,n.security_verdict,n.delivery_status,
               n.attempts,n.status_code,n.detail,n.created_at,n.completed_at,
               n.delivered_at,n.read_at,r.workflow_id,w.name AS workflow_name,
               r.total_findings,r.critical_count,r.high_count
               FROM runtime.completion_notifications n
               JOIN runtime.runs r ON (r.workspace_id,r.id)=(n.workspace_id,n.run_id)
               LEFT JOIN runtime.workflows w
                 ON (w.workspace_id,w.id)=(r.workspace_id,r.workflow_id)
               WHERE n.workspace_id=%s
               ORDER BY n.completed_at DESC,n.run_id DESC LIMIT %s""",
            (ws, limit),
        ).fetchall()
        unread = conn.execute(
            """SELECT count(*) AS n FROM runtime.completion_notifications
               WHERE workspace_id=%s AND read_at IS NULL""",
            (ws,),
        ).fetchone()['n']
    return [dict(row) for row in rows], int(unread)


def mark_read(workspace, run_id):
    with get_pool().connection() as conn:
        return conn.execute(
            """UPDATE runtime.completion_notifications SET read_at=coalesce(read_at,now())
               WHERE workspace_id=%s AND run_id=%s RETURNING run_id""",
            (_workspace(workspace), run_id),
        ).fetchone() is not None


def mark_all_read(workspace):
    with get_pool().connection() as conn:
        row = conn.execute(
            """UPDATE runtime.completion_notifications SET read_at=now()
               WHERE workspace_id=%s AND read_at IS NULL RETURNING run_id""",
            (_workspace(workspace),),
        ).fetchall()
    return len(row)


def claim():
    token = uuid4().hex
    with get_pool().connection() as conn:
        row = conn.execute(
            """WITH candidate AS (
                 SELECT workspace_id,run_id FROM runtime.completion_notifications
                 WHERE (delivery_status='pending' AND next_attempt_at<=now())
                    OR (delivery_status='sending' AND lease_until<now())
                 ORDER BY next_attempt_at FOR UPDATE SKIP LOCKED LIMIT 1)
               UPDATE runtime.completion_notifications n SET delivery_status='sending',
                   lease_token=%s,lease_until=now()+interval '45 seconds',
                   attempts=n.attempts+1
               FROM candidate c
               WHERE (n.workspace_id,n.run_id)=(c.workspace_id,c.run_id)
               RETURNING n.*""",
            (token,),
        ).fetchone()
    return dict(row) if row else None


def finish(row, status, detail, code=None, retry=False):
    with get_pool().connection() as conn:
        conn.execute(
            """UPDATE runtime.completion_notifications SET delivery_status=%s,detail=%s,
               status_code=%s,
               delivered_at=CASE WHEN %s='delivered' THEN now() ELSE delivered_at END,
               next_attempt_at=now()+interval '10 seconds',
               lease_until=NULL,lease_token=NULL
               WHERE workspace_id=%s AND run_id=%s AND lease_token=%s""",
            ('pending' if retry else status, detail, code, status,
             row['workspace_id'], row['run_id'], row['lease_token']),
        )