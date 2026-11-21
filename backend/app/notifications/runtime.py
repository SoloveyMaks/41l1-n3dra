"""Terminal-run notification delivery.

At-least-once with a stable event ID. The outbox is durable: pending rows are
resumed after a process restart, a `sending` row whose worker died is reclaimed
after its lease expires, and a skipped row is never rewritten as delivered.
"""
import asyncio
import logging
from urllib.parse import urlsplit

import httpx

from backend.app.engine.notify_policy import validate_webhook_url
from backend.app.notifications import store

logger = logging.getLogger(__name__)
DELIVERY_ATTEMPTS = 3
TIMEOUT_SECONDS = 5.0


def _pinned_url(url: str, address: str) -> str:
    parts = urlsplit(url)
    host = f'[{address}]' if ':' in address else address
    port = parts.port or (443 if parts.scheme == 'https' else 80)
    return f'{parts.scheme}://{host}:{port}{parts.path or "/"}'


def deliver(row):
    if row['attempts'] > DELIVERY_ATTEMPTS:
        store.finish(row, 'failed', 'Исчерпаны попытки доставки')
        return
    current = store.settings(str(row['workspace_id']))
    if not current['enabled'] or current['webhook_url'] != row['webhook_url']:
        store.finish(row, 'skipped', 'Настройки доставки выключены или изменены после запуска')
        return
    decision = validate_webhook_url(
        row['webhook_url'],
        allow_local=bool(row['allow_local'] and current['allow_local']),
        allow_external=bool(row['allow_external'] and current['allow_external']),
    )
    if not decision.allowed:
        store.finish(row, 'failed', decision.reason)
        return
    if not decision.addresses:
        store.finish(row, 'failed', 'Приёмник не разрешился в IP')
        return
    parts = urlsplit(row['webhook_url'])
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
        # original host for HTTPS certificate validation and SNI.
        with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False, trust_env=False) as client:
            with client.stream(
                'POST', _pinned_url(row['webhook_url'], decision.addresses[0]),
                json=payload, headers=headers,
                extensions={'sni_hostname': parts.hostname},
            ) as response:
                code = response.status_code
        if 200 <= code < 300:
            store.finish(row, 'delivered', f'Приёмник подтвердил HTTP {code}', code)
            return
        retry = (code == 408 or code == 429 or code >= 500) and row['attempts'] < DELIVERY_ATTEMPTS
        store.finish(row, 'failed', f'Приёмник вернул HTTP {code}', code, retry=retry)
    except httpx.HTTPError:
        retry = row['attempts'] < DELIVERY_ATTEMPTS
        store.finish(
            row, 'failed',
            'Не удалось получить подтверждение приёмника' if not retry else
            'Приёмник недоступен, будет повтор',
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