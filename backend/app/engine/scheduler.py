import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
import uuid

from backend.app.contracts.schemas import WorkflowDraft
from backend.app.storage.database import DatabaseRepository

logger = logging.getLogger("WorkflowScheduler")


class WorkflowScheduler:
    def __init__(self, db: DatabaseRepository, executor: Any = None):
        self.db = db
        self.executor = executor
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._scheduler_loop())

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _scheduler_loop(self) -> None:
        while self._running:
            try:
                await self._check_and_trigger_schedules()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in scheduler tick: {e}")
            await asyncio.sleep(5)  # Tick every 5 seconds

    async def _check_and_trigger_schedules(self) -> None:
        schedules = self.db.list_schedules()
        now = datetime.now(timezone.utc)

        for sched in schedules:
            if not sched.get("enabled"):
                continue

            next_fire = sched.get("next_fire_at")
            if not next_fire:
                # Initialize next fire time
                next_dt = now + timedelta(seconds=self._parse_interval(sched.get("cron_or_interval", "interval:60")))
                self.db.update_schedule_run_time(sched["id"], sched.get("last_run_at") or "", next_dt.isoformat())
                continue

            next_dt = datetime.fromisoformat(next_fire)
            if now >= next_dt:
                # Trigger schedule!
                wf = self.db.get_workflow(sched["workflow_id"])
                if wf and "draft" in wf:
                    run_id = str(uuid.uuid4())
                    self.db.create_run(
                        run_id=run_id,
                        workflow_id=sched["workflow_id"],
                        revision_id=wf.get("latest_revision_id", "rev_1"),
                        artifact_id=sched["artifact_id"],
                        trigger_type="schedule"
                    )

                    # Update next fire time
                    interval_sec = self._parse_interval(sched.get("cron_or_interval", "interval:60"))
                    new_next_dt = now + timedelta(seconds=interval_sec)
                    self.db.update_schedule_run_time(sched["id"], now.isoformat(), new_next_dt.isoformat())

                    if self.executor:
                        draft_obj = WorkflowDraft(**wf["draft"])
                        asyncio.create_task(
                            self.executor.execute_run(
                                run_id=run_id,
                                draft=draft_obj,
                                artifact_id=sched["artifact_id"],
                                trigger_type="schedule"
                            )
                        )

    def _parse_interval(self, cron_or_interval: str) -> int:
        if cron_or_interval.startswith("interval:"):
            try:
                return int(cron_or_interval.split(":")[1])
            except Exception:
                return 60
        # If standard 5-part cron, e.g. "*/5 * * * *" -> approximate to 300s
        if "*/" in cron_or_interval:
            try:
                part = cron_or_interval.split()[0].replace("*/", "")
                return int(part) * 60
            except Exception:
                return 300
        return 60
