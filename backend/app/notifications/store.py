"""Durable inbox and a bounded completion-webhook outbox, scoped by workspace.

The outbox row is a *snapshot* of the delivery settings at completion time.
Changing settings later never rewrites history: an in-flight row is skipped if
the URL or permission flags changed before dispatch authorization, and a
skipped row stays skipped.

Dispatch linearization
----------------------
Settings mutation and dispatch authorization take the same per-workspace
PostgreSQL advisory transaction lock.  A worker may only start the HTTP request
after :func:`authorize` commits ``authorized_at`` while still holding the
claimed lease.  Therefore:

* disable/URL change committed before authorization -> row is skipped, no POST;
* authorization committed before disable -> the request may already be on the
  wire; it completes and the journal records the real ``delivered``/``failed``
  result.  A request that has begun cannot be unsent, and this code does not
  pretend otherwise.
* lease revoked/reclaimed -> the old worker's authorization and completion are
  rejected by the lease token, so a late result cannot overwrite the row.

Only :func:`finish` (with ``authorized_at IS NOT NULL``) can record a send
result; :func:`abort` handles policy/settings refusals before a request starts.
"""

from contextlib import contextmanager
from uuid import UUID, uuid4

from backend.app.storage.database import get_pool, initialize_notification_schema

MAX_DELIVERY_ATTEMPTS = 3
DISPATCH_LOCK_NAMESPACE = 'nedra-completion-notifications-dispatch'

_DEFAULT_SETTINGS = {
    'enabled': False,
    'webhook_url': '',
    'webhook_ref': '',
    'allow_local': False,
    'allow_external': False,
}


class SettingsConflict(RuntimeError):
    """Concurrent settings edit invalidated a blank "keep URL" save."""


def _workspace(value) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _lock_workspace(conn, workspace) -> None:
    """Serialize settings writes and dispatch authorization for one workspace."""
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
        (DISPATCH_LOCK_NAMESPACE, str(workspace)),
    )


def _settings_row(conn, workspace) -> dict:
    row = conn.execute(
        "SELECT enabled, webhook_url, webhook_ref, allow_local, allow_external "
        "FROM runtime.notification_settings WHERE workspace_id=%s",
        (_workspace(workspace),),
    ).fetchone()
    return dict(row) if row else dict(_DEFAULT_SETTINGS)


@contextmanager
def _connection():
    """Notification transactions explicitly run at READ COMMITTED.

    The completion-trigger/opt-out contract relies on a fresh statement
    snapshot after waiting for the workspace advisory lock.  REPEATABLE READ and
    SERIALIZABLE can keep a pre-opt-out snapshot, so they are not supported;
    the run-status trigger rejects them as well.  The isolation statement must
    be the first command in the transaction opened by the pool.
    """
    with get_pool().connection() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
        yield conn


def initialize():
    initialize_notification_schema()


def settings(workspace):
    """Internal settings snapshot, including the server-side secret URL.

    HTTP handlers MUST render this through ``notifications.redact`` and never
    return ``webhook_url`` raw.
    """
    with _connection() as conn:
        return _settings_row(conn, workspace)


def save_settings(workspace, value, *, webhook_url=None, expected_url=None):
    """Persist settings and cancel dispatches that have not been authorized.

    ``webhook_url=None`` uses a non-empty ``value.webhook_url`` when present,
    otherwise keeps the stored URL.  A non-None string replaces it.
    ``expected_url`` guards the blank case: if a concurrent edit changed the
    URL after the caller validated it, raise :class:`SettingsConflict` instead
    of saving an unvalidated destination.
    """
    ws = _workspace(workspace)
    with _connection() as conn:
        _lock_workspace(conn, ws)
        current = _settings_row(conn, ws)
        if webhook_url is None:
            # Direct callers may carry the destination on the model; blank means
            # "keep the stored secret" for both direct calls and the API.
            candidate = (getattr(value, 'webhook_url', '') or '').strip()
            webhook_url = candidate or None
        effective_url = current['webhook_url'] if webhook_url is None else webhook_url
        if expected_url is not None and current['webhook_url'] != expected_url:
            raise SettingsConflict('Настройки изменились параллельно, повторите сохранение')
        webhook_ref = current['webhook_ref']
        if effective_url != current['webhook_url']:
            webhook_ref = uuid4().hex[:12] if effective_url else ''
        conn.execute(
            """INSERT INTO runtime.notification_settings
               (workspace_id,enabled,webhook_url,webhook_ref,allow_local,allow_external)
               VALUES (%s,%s,%s,%s,%s,%s)
               ON CONFLICT (workspace_id) DO UPDATE SET enabled=EXCLUDED.enabled,
               webhook_url=EXCLUDED.webhook_url,webhook_ref=EXCLUDED.webhook_ref,
               allow_local=EXCLUDED.allow_local,
               allow_external=EXCLUDED.allow_external,updated_at=clock_timestamp()""",
            (ws, value.enabled, effective_url, webhook_ref,
             value.allow_local, value.allow_external),
        )
        changed_destination = (
            effective_url != current['webhook_url']
            or value.allow_local != current['allow_local']
            or value.allow_external != current['allow_external']
        )
        if not value.enabled:
            reason = 'Доставка выключена пользователем до отправки'
        elif changed_destination:
            reason = 'Настройки доставки изменены после завершения запуска'
        else:
            reason = None
        if reason:
            # Only rows that have not been dispatch-authorized can be recalled.
            # An authorized request is already in flight and records its real
            # outcome instead of being rewritten as "skipped".
            conn.execute(
                """UPDATE runtime.completion_notifications SET delivery_status='skipped',
                   detail=%s, lease_token=NULL,lease_until=NULL
                   WHERE workspace_id=%s AND authorized_at IS NULL
                     AND delivery_status IN ('pending','sending')""",
                (reason, ws),
            )
            conn.execute(
                """UPDATE runtime.completion_notifications SET retry_cancelled=true
                   WHERE workspace_id=%s AND delivery_status='sending'
                     AND authorized_at IS NOT NULL""",
                (ws,),
            )
        return _settings_row(conn, ws)


def inbox(workspace, limit=50):
    ws = _workspace(workspace)
    with _connection() as conn:
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
    with _connection() as conn:
        return conn.execute(
            """UPDATE runtime.completion_notifications SET read_at=coalesce(read_at,now())
               WHERE workspace_id=%s AND run_id=%s RETURNING run_id""",
            (_workspace(workspace), run_id),
        ).fetchone() is not None


def mark_all_read(workspace):
    with _connection() as conn:
        row = conn.execute(
            """UPDATE runtime.completion_notifications SET read_at=now()
               WHERE workspace_id=%s AND read_at IS NULL RETURNING run_id""",
            (_workspace(workspace),),
        ).fetchall()
    return len(row)


def claim():
    token = uuid4().hex
    with _connection() as conn:
        row = conn.execute(
            """WITH candidate AS (
                 SELECT workspace_id,run_id FROM runtime.completion_notifications
                 WHERE (delivery_status='pending' AND next_attempt_at<=clock_timestamp())
                    OR (delivery_status='sending' AND lease_until<clock_timestamp())
                 ORDER BY next_attempt_at FOR UPDATE SKIP LOCKED LIMIT 1)
               UPDATE runtime.completion_notifications n SET delivery_status='sending',
                   lease_token=%s,lease_until=clock_timestamp()+interval '45 seconds',
                   authorized_at=NULL
               FROM candidate c
               WHERE (n.workspace_id,n.run_id)=(c.workspace_id,c.run_id)
               RETURNING n.*""",
            (token,),
        ).fetchone()
    return dict(row) if row else None


def authorize(row, *, expected_url, allow_local, allow_external):
    """Re-check settings and lease, then allow exactly one HTTP send.

    Returns ``{"action": "send", "attempts": N}``, ``{"action": "skip", ...}``,
    ``{"action": "exhausted"}`` or ``{"action": "lost"}``.  Only ``send`` may
    perform a network request.
    """
    ws = row['workspace_id']
    with _connection() as conn:
        _lock_workspace(conn, ws)
        current = conn.execute(
            """SELECT delivery_status,lease_token,attempts,authorized_at,retry_cancelled,
                      (lease_until > clock_timestamp()) AS lease_valid
               FROM runtime.completion_notifications
               WHERE workspace_id=%s AND run_id=%s FOR UPDATE""",
            (ws, row['run_id']),
        ).fetchone()
        if (
            not current
            or current['lease_token'] != row['lease_token']
            or current['delivery_status'] != 'sending'
            or not current['lease_valid']
            or current['authorized_at'] is not None
        ):
            return {'action': 'lost'}
        if current['retry_cancelled']:
            _skip_locked(conn, row, 'Повтор отменён изменением настроек')
            return {'action': 'skip', 'detail': 'Повтор отменён изменением настроек'}
        stored = _settings_row(conn, ws)
        if not stored['enabled']:
            _skip_locked(conn, row, 'Доставка выключена пользователем до отправки')
            return {'action': 'skip', 'detail': 'Доставка выключена пользователем до отправки'}
        if (
            stored['webhook_url'] != expected_url
            or stored['allow_local'] != allow_local
            or stored['allow_external'] != allow_external
        ):
            _skip_locked(conn, row, 'Настройки доставки изменены после завершения запуска')
            return {'action': 'skip', 'detail': 'Настройки доставки изменены после завершения запуска'}
        if current['attempts'] >= MAX_DELIVERY_ATTEMPTS:
            conn.execute(
                """UPDATE runtime.completion_notifications SET delivery_status='failed',
                   detail='Исчерпаны попытки доставки', lease_token=NULL,
                   lease_until=NULL
                   WHERE workspace_id=%s AND run_id=%s AND lease_token=%s
                     AND delivery_status='sending' AND authorized_at IS NULL""",
                (ws, row['run_id'], row['lease_token']),
            )
            return {'action': 'exhausted'}
        authorized = conn.execute(
            """UPDATE runtime.completion_notifications
               SET authorized_at=clock_timestamp(),attempts=attempts+1,detail='Отправка начата'
               WHERE workspace_id=%s AND run_id=%s AND lease_token=%s
                 AND delivery_status='sending' AND authorized_at IS NULL
               RETURNING attempts""",
            (ws, row['run_id'], row['lease_token']),
        ).fetchone()
        if authorized is None:
            return {'action': 'lost'}
        return {'action': 'send', 'attempts': authorized['attempts']}


def _skip_locked(conn, row, detail):
    conn.execute(
        """UPDATE runtime.completion_notifications SET delivery_status='skipped',
           detail=%s, lease_token=NULL,lease_until=NULL
           WHERE workspace_id=%s AND run_id=%s AND lease_token=%s
             AND delivery_status='sending' AND authorized_at IS NULL""",
        (detail, row['workspace_id'], row['run_id'], row['lease_token']),
    )


def abort(row, status, detail, code=None):
    """Record a refusal before any request started (never a delivery claim)."""
    with _connection() as conn:
        conn.execute(
            """UPDATE runtime.completion_notifications SET delivery_status=%s,detail=%s,
               status_code=%s,lease_until=NULL,lease_token=NULL
               WHERE workspace_id=%s AND run_id=%s AND lease_token=%s
                 AND delivery_status='sending' AND authorized_at IS NULL""",
            (status, detail, code, row['workspace_id'], row['run_id'], row['lease_token']),
        )


def finish(row, status, detail, code=None, retry=False):
    """Record the real outcome of an authorized request.

    The ``authorized_at IS NOT NULL`` and lease-token guards mean a late worker
    can never turn a skipped/revoked row into ``delivered``.
    """
    with _connection() as conn:
        _lock_workspace(conn, row['workspace_id'])
        current = conn.execute(
            """SELECT retry_cancelled FROM runtime.completion_notifications
               WHERE workspace_id=%s AND run_id=%s AND lease_token=%s
                 AND delivery_status='sending' AND authorized_at IS NOT NULL
               FOR UPDATE""",
            (row['workspace_id'], row['run_id'], row['lease_token']),
        ).fetchone()
        if current is None:
            return
        if retry and current['retry_cancelled']:
            retry = False
            detail = 'Отправка не подтверждена; повтор отменён изменением настроек'
        conn.execute(
            """UPDATE runtime.completion_notifications SET delivery_status=%s,detail=%s,
               status_code=%s,
               delivered_at=CASE WHEN %s='delivered' THEN clock_timestamp() ELSE delivered_at END,
               next_attempt_at=clock_timestamp()+interval '10 seconds',
               authorized_at=CASE WHEN %s THEN NULL ELSE authorized_at END,
               lease_until=NULL,lease_token=NULL
               WHERE workspace_id=%s AND run_id=%s AND lease_token=%s
                 AND delivery_status='sending' AND authorized_at IS NOT NULL""",
            ('pending' if retry else status, detail, code, status, retry,
             row['workspace_id'], row['run_id'], row['lease_token']),
        )
