import asyncio
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

from backend.app.ai.provider import mask_sensitive_text
from backend.app.security.presentation import mask_value_tree
from backend.app.storage.database import DatabaseRepository

MAX_SUBSCRIBER_EVENTS = 256
HEARTBEAT_SECONDS = 15


class SSEBroker:
    def __init__(self, db: DatabaseRepository):
        self.db = db
        self._subscribers: Dict[str, List[asyncio.Queue]] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._closing = False

    async def close(self) -> None:
        """Stop every active stream so graceful shutdown can finish.

        Without this uvicorn waits for open SSE connections until the
        container timeout, gets SIGKILL, and leaves the run ``running``.
        """
        async with self._lock:
            self._closing = True
            queues = [queue for group in self._subscribers.values() for queue in group]
            self._subscribers.clear()
        for queue in queues:
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass


    @staticmethod
    def _historical_message(ev: Dict[str, Any]) -> str:
        data = json.dumps({
            "seq": ev["seq"], "runId": ev["run_id"], "timestamp": ev["timestamp"],
            "type": ev["type"], "node_id": ev["node_id"], "nodeId": ev["node_id"],
            "payload": mask_value_tree(ev["payload"], mask_sensitive_text),
        }, ensure_ascii=False)
        return f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {data}\n\n"

    async def emit_event(self, run_id: str, event_type: str, payload: Dict[str, Any],
                         node_id: Optional[str] = None) -> int:
        payload = mask_value_tree(payload, mask_sensitive_text)
        async with self._lock:
            seq = await asyncio.to_thread(self.db.add_run_event, run_id, event_type, payload, node_id)
            event_msg = {
                "seq": seq,
                "runId": run_id,
                "type": event_type,
                "node_id": node_id,
                "nodeId": node_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": payload,
            }
            for q in list(self._subscribers.get(run_id, [])):
                if q.full():
                    # Close a slow stream; reconnect replays persisted events
                    # after the last delivered sequence without losing state.
                    while not q.empty():
                        q.get_nowait()
                    q.put_nowait(None)
                    self._subscribers[run_id].remove(q)
                else:
                    q.put_nowait(event_msg)
            if run_id in self._subscribers and not self._subscribers[run_id]:
                del self._subscribers[run_id]

        return seq

    async def subscribe(self, run_id: str, after_seq: int = 0) -> AsyncGenerator[str, None]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_SUBSCRIBER_EVENTS)
        last_seq = after_seq

        async with self._lock:
            if self._closing:
                return
            self._subscribers[run_id].append(queue)

        try:
            historical_events = await asyncio.to_thread(
                self.db.get_run_events, run_id, after_seq=after_seq,
            )
            for ev in historical_events:
                if ev["seq"] <= last_seq:
                    continue
                last_seq = ev["seq"]
                yield self._historical_message(ev)

            # 2. Stream new live events
            while not self._closing:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    if self._closing:
                        return
                    # Scheduler/HTTP workers may persist events in another
                    # process, outside this broker's in-memory subscriber list.
                    missed = await asyncio.to_thread(
                        self.db.get_run_events, run_id, after_seq=last_seq,
                    )
                    for historical in missed:
                        if historical["seq"] <= last_seq:
                            continue
                        last_seq = historical["seq"]
                        yield self._historical_message(historical)
                    yield ": heartbeat\n\n"
                    continue
                if ev is None:
                    return
                if ev["seq"] <= last_seq:
                    continue
                if ev["seq"] > last_seq + 1:
                    missed = await asyncio.to_thread(
                        self.db.get_run_events, run_id, after_seq=last_seq,
                    )
                    for historical in missed:
                        if historical["seq"] <= last_seq:
                            continue
                        last_seq = historical["seq"]
                        yield self._historical_message(historical)
                    if ev["seq"] <= last_seq:
                        continue
                last_seq = ev["seq"]
                data_str = json.dumps(ev, ensure_ascii=False)
                yield f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {data_str}\n\n"
        finally:
            async with self._lock:
                if queue in self._subscribers.get(run_id, []):
                    self._subscribers[run_id].remove(queue)
                if run_id in self._subscribers and not self._subscribers[run_id]:
                    del self._subscribers[run_id]
