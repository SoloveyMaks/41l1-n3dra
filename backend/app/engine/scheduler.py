import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
import uuid

from croniter import croniter

from backend.app.contracts.schemas import WorkflowDraft
from backend.app.storage.database import DatabaseRepository

logger = logging.getLogger("WorkflowScheduler")


MAX_INTERVAL_SECONDS = 365 * 24 * 60 * 60


def _interval_seconds(expression: str) -> int:
    raw = expression.split(":", 1)[1].strip()
    try:
        seconds = int(raw)
    except ValueError as exc:
        raise ValueError("interval должен быть целым числом секунд") from exc
    if not 1 <= seconds <= MAX_INTERVAL_SECONDS:
        raise ValueError(f"interval должен быть от 1 до {MAX_INTERVAL_SECONDS} секунд")
    return seconds


def next_fire_at(expression: str, now: datetime) -> datetime:
    if expression.startswith("interval:"):
        return now + timedelta(seconds=_interval_seconds(expression))
    return croniter(expression, now).get_next(datetime)


def validate_schedule_expression(value: Any) -> str:
    """Accept a bounded interval or a cron that really has a next fire."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("cronOrInterval должен быть interval:N или 5-полевым cron")
    expression = value.strip()
    if expression.startswith("interval:"):
        _interval_seconds(expression)
        return expression
    if len(expression.split()) != 5:
        raise ValueError("cron должен состоять из 5 полей")
    try:
        next_fire_at(expression, datetime.now(timezone.utc))
    except Exception as exc:
        raise ValueError("cron не имеет следующего срабатывания или некорректен") from exc
    return expression


class WorkflowScheduler:
    def __init__(self, db: DatabaseRepository, executor: Any = None):
        self.db = db
        self.executor = executor
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._active_schedules: set[str] = set()

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
                try:
                    next_dt = next_fire_at(sched.get("cron_or_interval", "interval:60"), now)
                except Exception as exc:
                    logger.warning("Пропущено расписание %s: %s", sched.get("id"), exc)
                    continue
                self.db.update_schedule_run_time(sched["id"], sched.get("last_run_at") or "", next_dt.isoformat())
                continue

            next_dt = datetime.fromisoformat(next_fire)
            if now >= next_dt:
                # Do not overlap an active run of the same schedule: advance the
                # next window instead of accumulating queued runs.
                if sched["id"] in self._active_schedules:
                    try:
                        new_next_dt = next_fire_at(sched.get("cron_or_interval", "interval:60"), now)
                    except Exception as exc:
                        logger.warning("Не удалось пересчитать расписание %s: %s", sched.get("id"), exc)
                        continue
                    self.db.update_schedule_run_time(
                        sched["id"], sched.get("last_run_at") or "", new_next_dt.isoformat()
                    )
                    continue

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
                    try:
                        new_next_dt = next_fire_at(sched.get("cron_or_interval", "interval:60"), now)
                    except Exception as exc:
                        logger.warning("Не удалось вычислить следующее срабатывание %s: %s", sched.get("id"), exc)
                        continue
                    self.db.update_schedule_run_time(sched["id"], now.isoformat(), new_next_dt.isoformat())

                    if self.executor:
                        draft_obj = WorkflowDraft(**wf["draft"])
                        self._active_schedules.add(sched["id"])

                        async def _run_scheduled(schedule_id: str = sched["id"],
                                                 scheduled_run_id: str = run_id,
                                                 scheduled_draft: WorkflowDraft = draft_obj,
                                                 scheduled_artifact: str = sched["artifact_id"]) -> None:
                            try:
                                await self.executor.execute_run(
                                    run_id=scheduled_run_id,
                                    draft=scheduled_draft,
                                    artifact_id=scheduled_artifact,
                                    trigger_type="schedule",
                                )
                            finally:
                                self._active_schedules.discard(schedule_id)

                        asyncio.create_task(_run_scheduled())
