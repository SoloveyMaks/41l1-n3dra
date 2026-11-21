"""Terminal-run notification delivery.

At-least-once with a stable event ID. The outbox is durable: pending rows are
resumed after a process restart, a `sending` row whose worker died is reclaimed
after its lease expires, and a skipped row is never rewritten as delivered.

Dispatch boundary: the request starts only after :func:`store.authorize`
commits under the per-workspace lock.  A settings disable/change committed
before that point cancels the row without a POST.  Once authorization is
committed the request may already be in flight; it is allowed to finish and the
journal records the real HTTP outcome.  There is no cancellation of an already
sent HTTP request, and no fake promise that opt-out retracts one.
"""
import asyncio
import logging
from urllib.parse import urlsplit

import httpx

from backend.app.engine.notify_policy import validate_webhook_url
from backend.app.notifications import store

logger = logging.getLogger(__name__)
DELIVERY_ATTEMPTS = store.MAX_DELIVERY_ATTEMPTS
TIMEOUT_SECONDS = 5.0


def _pinned_url(url: str, address: str) -> str:
    parts = urlsplit(url)
    host = f'[{address}]' if ':' in address else address
    port = parts.port or (443 if parts.scheme == 'https' else 80)
    return f'{parts.scheme}://{host}:{port}{parts.path or "/"}'


def deliver(row):
    if row['attempts'] >= DELIVERY_ATTEMPTS:
        store.abort(row, 'failed', 'Исчерпаны попытки доставки')
        return
    current = store.settings(str(row['workspace_id']))
    if not current['enabled']:
        store.abort(row, 'skipped', 'Доставка выключена пользователем до отправки')
        return
    if current['webhook_url'] != row['webhook_url']:
        store.abort(row, 'skipped', 'Настройки доставки изменены после завершения запуска')
        return
    # Snapshot permissions cannot be loosened by a later settings change.
    decision = validate_webhook_url(
        current['webhook_url'],
        allow_local=bool(row['allow_local'] and current['allow_local']),
        allow_external=bool(row['allow_external'] and current['allow_external']),
    )
    if not decision.allowed:
        store.abort(row, 'failed', decision.reason)
        return
    if not decision.addresses:
        store.abort(row, 'failed', 'Приёмник не разрешился в IP')
        return

    # Linearization point: settings and lease are re-checked atomically.  A
    # disable/URL change committed before this returns means no request starts.
    authorization = store.authorize(
        row,
        expected_url=current['webhook_url'],
        allow_local=bool(current['allow_local']),
        allow_external=bool(current['allow_external']),
    )
    if authorization['action'] != 'send':
        return

    parts = urlsplit(current['webhook_url'])
    event_id = f'completion:{row["run_id"]}'
    completed = row.get('completed_at') or row.get('created_at')
    payload = {
        'event': 'run_completed',
        'eventId': event_id,
        'runId': row['run_id'],
        'executionStatus': row['execution_status'],
        'securityVerdict': row['security_verdict'],
        'completedAt': completed.isoformat() if hasattr(completed, 'isoformat') else completed,
    }
    headers = {
        'Host': parts.netloc,
        'User-Agent': 'Nedra-Notifications/1.0',
        'Idempotency-Key': event_id,
    }
    try:
        # Connect to the IP that passed SSRF validation while keeping the
        # original host for HTTPS certificate validation and SNI.  Redirects
        # stay disabled: a 3xx is reported as a failure, never followed.
        with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False, trust_env=False) as client:
            with client.stream(
                'POST', _pinned_url(current['webhook_url'], decision.addresses[0]),
                json=payload, headers=headers,
                extensions={'sni_hostname': parts.hostname},
            ) as response:
                code = response.status_code
        if 200 <= code < 300:
            store.finish(row, 'delivered', f'Приёмник подтвердил HTTP {code}', code)
            return
        retry = (
            code in (408, 429) or code >= 500
        ) and authorization['attempts'] < DELIVERY_ATTEMPTS
        if 300 <= code < 400:
            detail = f'Приёмник вернул HTTP {code}: перенаправления запрещены'
        else:
            detail = f'Приёмник вернул HTTP {code}'
        store.finish(row, 'failed', detail, code, retry=retry)
    except (httpx.HTTPError, OSError):
        retry = authorization['attempts'] < DELIVERY_ATTEMPTS
        store.finish(
            row, 'failed',
            'Приёмник недоступен, будет повтор' if retry else
            'Не удалось получить подтверждение приёмника',
            retry=retry,
        )


class NotificationRuntime:
    def __init__(self):
        self.task = None

    async def start(self):
        await asyncio.to_thread(store.initialize)
        self.task = asyncio.create_task(self._loop(), name='completion-notifications')

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def _loop(self):
        while True:
            delay = 1
            try:
                row = await asyncio.to_thread(store.claim)
                if row:
                    await asyncio.to_thread(deliver, row)
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - delivery loop must survive
                delay = 5
                logger.warning('Completion delivery unavailable (%s)', type(exc).__name__)
            await asyncio.sleep(delay)
