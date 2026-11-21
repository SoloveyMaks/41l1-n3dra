import asyncio
import json
from collections import defaultdict
from typing import Any, AsyncGenerator, Dict, List, Optional

from backend.app.ai.provider import mask_sensitive_text
from backend.app.security.presentation import mask_value_tree
from backend.app.storage.database import DatabaseRepository


class SSEBroker:
    def __init__(self, db: DatabaseRepository):
        self.db = db
        self._subscribers: Dict[str, List[asyncio.Queue]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def emit_event(self, run_id: str, event_type: str, payload: Dict[str, Any],
                         node_id: Optional[str] = None) -> int:
        # Save event to database first to get unique auto-increment sequence seq
        seq = self.db.add_run_event(run_id, event_type, payload, node_id)

        event_msg = {
            "seq": seq,
            "runId": run_id,
            "type": event_type,
            "nodeId": node_id,
            "payload": mask_value_tree(payload, mask_sensitive_text),
        }

        # Deliver to all live subscribers for this run
        async with self._lock:
            for q in self._subscribers[run_id]:
                await q.put(event_msg)

        return seq

    async def subscribe(self, run_id: str, after_seq: int = 0) -> AsyncGenerator[str, None]:
        queue: asyncio.Queue = asyncio.Queue()

        async with self._lock:
            self._subscribers[run_id].append(queue)

        try:
            # 1. First replay any historical events after_seq from SQLite
            historical_events = self.db.get_run_events(run_id, after_seq=after_seq)
            for ev in historical_events:
                data_str = json.dumps({
                    "seq": ev["seq"],
                    "runId": ev["run_id"],
                    "timestamp": ev["timestamp"],
                    "type": ev["type"],
                    "node_id": ev["node_id"],
                    "payload": mask_value_tree(ev["payload"], mask_sensitive_text),
                }, ensure_ascii=False)
                yield f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {data_str}\n\n"

            # 2. Stream new live events
            while True:
                ev = await queue.get()
                data_str = json.dumps(ev, ensure_ascii=False)
                yield f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {data_str}\n\n"
        finally:
            async with self._lock:
                if queue in self._subscribers[run_id]:
                    self._subscribers[run_id].remove(queue)
