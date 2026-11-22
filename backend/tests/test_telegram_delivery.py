"""Completed-run delivery to Telegram: policy, quiet hours, idempotency.

Everything runs against fakes — an in-memory outbox repository and a
recording Bot API — so no PostgreSQL and no Telegram network access is used.
Covered:

* a completed run (manual/scheduled entrypoint) enqueues one durable delivery
  per bound chat and the fake Bot API actually receives it;
* severity threshold below / at / above the run's findings;
* quiet hours defer the send to the window end instead of dropping it;
* repeated completion/claim cycles never duplicate a row or a send;
* the JSONC document keeps every deterministic finding id.

    PYTHONPATH=. /tmp/nedra-venv/bin/python -m pytest \
        backend/tests/test_telegram_delivery.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.app.contracts.schemas import (  # noqa: E402
    NodeKind,
    WorkflowDraft,
    WorkflowNode,
)
from backend.app.engine.executor import WorkflowExecutor  # noqa: E402
from backend.app.storage.telegram import (  # noqa: E402
    Binding,
    DeliveryIntent,
    OutboxItem,
    Settings,
)
from backend.app.telegram import runtime as runtime_module  # noqa: E402
from backend.app.telegram.delivery_policy import (  # noqa: E402
    DeliveryFacts,
    evaluate_delivery,
    quiet_deferral,
    run_max_severity,
)
from backend.app.telegram.runtime import TelegramRuntime  # noqa: E402

USER_ID = "11111111-1111-1111-1111-111111111111"
WORKSPACE_ID = "22222222-2222-2222-2222-222222222222"
CHAT_ID = 4242
RUN_ID = "33333333-3333-3333-3333-333333333333"


@pytest.fixture(autouse=True)
def data_dir(tmp_path_factory: pytest.TempPathFactory):
    """Keep JSONC/canonical writes inside a throwaway directory."""
    root = tmp_path_factory.mktemp("nedra-tg-delivery")
    os.environ["CG_DATA_DIR"] = str(root)
    yield root
    os.environ.pop("CG_DATA_DIR", None)


class FakeDeliveryRepo:
    """In-memory subset of TelegramRepository used by the delivery paths."""

    def __init__(self, binding: Binding, settings: Settings) -> None:
        self.binding = binding
        self.settings = settings
        self.rows: Dict[int, Dict[str, Any]] = {}
        self.enqueued: List[tuple] = []
        self.claim_count = 0
        self.intents: Dict[str, Dict[str, Any]] = {}
        # Transient-failure switches the tests flip to simulate a DB outage.
        self.fail_bindings = False
        self.fail_record_times = 0
        self._next_id = 1

    # --- delivery intents (durable retry marker) -----------------------
    def record_delivery_intent(self, run_id: str, workspace_id: str) -> bool:
        if self.fail_record_times > 0:
            self.fail_record_times -= 1
            raise RuntimeError("transient database failure (intent write)")
        if run_id in self.intents:
            return False
        self.intents[run_id] = {
            "run_id": run_id,
            "workspace_id": workspace_id,
            "status": "pending",
            "attempts": 0,
            "last_error": None,
        }
        return True

    def pending_delivery_intents(self, limit: int = 20) -> List[DeliveryIntent]:
        rows = [
            row for row in self.intents.values() if row["status"] == "pending"
        ]
        rows.sort(key=lambda row: row["run_id"])
        return [
            DeliveryIntent(
                run_id=row["run_id"], workspace_id=row["workspace_id"],
                attempts=row["attempts"], last_error=row["last_error"],
            )
            for row in rows[: max(1, min(int(limit), 100))]
        ]

    def touch_delivery_intent(self, run_id: str, *, error: Optional[str] = None) -> None:
        row = self.intents.get(run_id)
        if row is None or row["status"] != "pending":
            return
        row["attempts"] += 1
        row["last_error"] = (error or "")[:500] or None

    def finish_delivery_intent(self, run_id: str) -> bool:
        row = self.intents.get(run_id)
        if row is None or row["status"] != "pending":
            return False
        row["status"] = "done"
        row["last_error"] = None
        return True

    def purge_delivery_intents(self, older_than_days: int = 7) -> int:
        stale = [
            run_id for run_id, row in self.intents.items()
            if row["status"] == "done"
        ]
        for run_id in stale:
            del self.intents[run_id]
        return len(stale)

    # --- enqueue -------------------------------------------------------
    def list_workspace_bindings(self, workspace_id: str) -> List[Binding]:
        if self.fail_bindings:
            raise RuntimeError("transient database failure (bindings read)")
        if str(workspace_id) == str(self.binding.workspace_id):
            return [self.binding]
        return []

    def get_settings(self, chat_id: int, **kwargs: Any) -> Settings:
        return self.settings

    def enqueue_outbox(
        self, chat_id: int, run_id: str, kind: str, *,
        user_id: str, workspace_id: str, binding_epoch: int,
    ) -> bool:
        # Mirrors ON CONFLICT (chat_id, run_id, kind) DO NOTHING.
        for row in self.rows.values():
            if (row["chat_id"], row["run_id"], row["kind"]) == (chat_id, run_id, kind):
                return False
        item_id = self._next_id
        self._next_id += 1
        self.rows[item_id] = {
            "id": item_id,
            "chat_id": chat_id,
            "run_id": run_id,
            "kind": kind,
            "status": "pending",
            "attempts": 0,
            "last_error": None,
            "user_id": user_id,
            "workspace_id": workspace_id,
            "binding_epoch": binding_epoch,
            "next_attempt_at": datetime.now(timezone.utc) - timedelta(seconds=1),
        }
        self.enqueued.append((chat_id, run_id, kind))
        return True

    # --- claim/finish --------------------------------------------------
    def _item(self, row: Dict[str, Any]) -> OutboxItem:
        return OutboxItem(
            id=row["id"], chat_id=row["chat_id"], run_id=row["run_id"],
            kind=row["kind"], status=row["status"], attempts=row["attempts"],
            last_error=row["last_error"], user_id=row["user_id"],
            workspace_id=row["workspace_id"],
            binding_epoch=row["binding_epoch"],
        )

    def claim_outbox(self, limit: int = 10) -> List[OutboxItem]:
        now = datetime.now(timezone.utc)
        claimed: List[OutboxItem] = []
        for row in sorted(self.rows.values(), key=lambda item: item["id"]):
            if len(claimed) >= limit:
                break
            if row["status"] == "pending" and row["next_attempt_at"] <= now:
                row["status"] = "sending"
                row["attempts"] += 1
                claimed.append(self._item(row))
                self.claim_count += 1
        return claimed

    def finish_outbox(
        self, outbox_id: int, status: str, *, error: Optional[str] = None,
        retry_delay_seconds: Optional[int] = None,
        user_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        binding_epoch: Optional[int] = None,
    ) -> bool:
        row = self.rows.get(int(outbox_id))
        if row is None or row["status"] == "cancelled":
            return False
        if user_id and row["user_id"] != user_id:
            return False
        if workspace_id and row["workspace_id"] != workspace_id:
            return False
        if binding_epoch is not None and row["binding_epoch"] != binding_epoch:
            return False
        row["status"] = status
        row["last_error"] = error
        if status == "pending":
            delay = max(1, min(int(retry_delay_seconds or 5), 3600))
            row["next_attempt_at"] = datetime.now(timezone.utc) + timedelta(seconds=delay)
        return True

    def defer_outbox(
        self, outbox_id: int, next_attempt_at: datetime, *, reason: str,
        user_id: str, workspace_id: str, binding_epoch: int,
    ) -> bool:
        row = self.rows.get(int(outbox_id))
        if row is None or row["status"] not in ("sending", "pending"):
            return False
        if (
            row["user_id"] != user_id
            or row["workspace_id"] != workspace_id
            or row["binding_epoch"] != binding_epoch
        ):
            return False
        row["status"] = "pending"
        # Mirrors GREATEST(attempts - 1, 0): the claim attempt is undone.
        row["attempts"] = max(int(row["attempts"]) - 1, 0)
        row["last_error"] = reason
        row["next_attempt_at"] = next_attempt_at
        return True

    # --- delivery guard ------------------------------------------------
    def delivery_is_current(self, item: OutboxItem) -> bool:
        return True

    def acquire_delivery_guard(self, item: OutboxItem):
        return None, True

    def release_delivery_guard(self, conn: Any) -> None:
        return None

    # --- helpers for assertions ---------------------------------------
    def statuses(self) -> List[str]:
        return [row["status"] for row in self.rows.values()]

    def rows_for(self, run_id: str) -> List[Dict[str, Any]]:
        return [row for row in self.rows.values() if row["run_id"] == run_id]


class FakeDB:
    """Storage surface used by WorkflowExecutor and the delivery runtime."""

    def __init__(self) -> None:
        self.runs: Dict[str, Dict[str, Any]] = {}
        self.findings: Dict[str, List[Dict[str, Any]]] = {}
        self.node_runs: Dict[str, List[Dict[str, Any]]] = {}
        self.claimed: set = set()
        self.status_writes: List[tuple] = []

    def add_run(
        self, run_id: str, *, status: str = "queued",
        counts: Optional[Dict[str, int]] = None,
        artifact_id: Optional[str] = None,
        verdict: str = "pending",
    ) -> Dict[str, Any]:
        counts = counts or {}
        run = {
            "id": run_id,
            "workspace_id": WORKSPACE_ID,
            "workflow_id": "wf-1",
            "revision_id": "rev-1",
            "artifact_id": artifact_id,
            "trigger_type": "manual",
            "execution_status": status,
            "security_verdict": verdict,
            "total_findings": counts.get("total", 0),
            "critical_count": counts.get("critical", 0),
            "high_count": counts.get("high", 0),
            "medium_count": counts.get("medium", 0),
            "low_count": counts.get("low", 0),
            "info_count": counts.get("info", 0),
        }
        self.runs[run_id] = run
        return run

    def claim_run_start(self, run_id: str, started_at: str) -> bool:
        if run_id in self.claimed:
            return False
        self.claimed.add(run_id)
        return True

    def update_run_status(
        self, run_id: str, execution_status: str,
        security_verdict: Optional[str] = None,
        started_at: Optional[str] = None,
        finished_at: Optional[str] = None,
        findings_summary: Optional[Dict[str, int]] = None,
    ) -> None:
        run = self.runs[run_id]
        run["execution_status"] = execution_status
        if security_verdict is not None:
            run["security_verdict"] = security_verdict
        if findings_summary:
            run["total_findings"] = findings_summary.get("total", 0)
            for key in ("critical", "high", "medium", "low", "info"):
                run[f"{key}_count"] = findings_summary.get(key, 0)
        self.status_writes.append((run_id, execution_status))

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        return self.runs.get(run_id)

    def save_node_run(self, payload: Dict[str, Any]) -> None:
        self.node_runs.setdefault(str(payload.get("run_id")), []).append(dict(payload))

    def get_node_runs(self, run_id: str) -> List[Dict[str, Any]]:
        return list(self.node_runs.get(run_id, []))

    def get_report(self, run_id: str) -> Optional[Dict[str, Any]]:
        return None

    def get_findings(self, run_id: str) -> List[Dict[str, Any]]:
        return list(self.findings.get(run_id, []))

    def get_workflow(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        return {"id": workflow_id, "name": "Delivery Scenario"}


class FakeSSE:
    def __init__(self) -> None:
        self.events: List[tuple] = []

    async def emit_event(self, run_id: str, event_type: str,
                         payload: Dict[str, Any], node_id: Optional[str] = None) -> int:
        self.events.append((run_id, event_type, payload))
        return len(self.events)


class FakeAPI:
    """Recording Bot API: no network, no token."""

    def __init__(self) -> None:
        self.bot_key = "fake"
        self.bot_username = "fake_bot"
        self.callback_secret = "fake-secret"
        self.messages: List[tuple] = []
        self.documents: List[tuple] = []
        self.send_error: Optional[Exception] = None

    def redact(self, text: Any) -> str:
        return str(text)[:500]

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> Dict[str, Any]:
        if self.send_error is not None:
            raise self.send_error
        self.messages.append((chat_id, text, kwargs))
        return {"message_id": len(self.messages)}

    async def send_document(self, chat_id: int, filename: str, content: bytes,
                            caption: str = "", **kwargs: Any) -> Dict[str, Any]:
        if self.send_error is not None:
            raise self.send_error
        self.documents.append((chat_id, filename, content, caption))
        return {"message_id": len(self.documents)}


def binding() -> Binding:
    return Binding(
        chat_id=CHAT_ID, user_id=USER_ID, workspace_id=WORKSPACE_ID,
        telegram_username="probe", binding_epoch=1,
    )


def make_runtime(
    *, settings: Optional[Settings] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> tuple[TelegramRuntime, FakeDeliveryRepo, FakeAPI, FakeDB]:
    repo = FakeDeliveryRepo(binding(), settings or Settings(chat_id=CHAT_ID))
    api = FakeAPI()
    db = FakeDB()
    runtime = TelegramRuntime(
        db, None, None,
        repository=repo,
        api=api,
        artifact_meta_loader=lambda workspace, artifact_id: meta,
    )
    # The transport counts as running; background loops stay off so claims are
    # driven explicitly by each test.
    runtime._started = True
    return runtime, repo, api, db


def manual_draft() -> WorkflowDraft:
    return WorkflowDraft(
        schemaVersion=1,
        name="Delivery scenario",
        nodes=[WorkflowNode(id="trigger", kind=NodeKind.MANUAL_TRIGGER, title="Start")],
        edges=[],
    )


async def complete_run(
    runtime: TelegramRuntime, db: FakeDB, executor: Optional[WorkflowExecutor] = None,
) -> WorkflowExecutor:
    """Execute a real single-node run through WorkflowExecutor (manual/scheduled entrypoint)."""
    if db.get_run(RUN_ID) is None:
        db.add_run(RUN_ID, status="queued")
    executor = executor or WorkflowExecutor(db, FakeSSE())
    executor.add_run_completion_hook(runtime.on_run_completed)
    await executor.execute_run(
        RUN_ID, manual_draft(), None, trigger_type="manual",
    )
    return executor


# --- 1. completion creates the delivery ---------------------------------

def test_run_completion_creates_delivery() -> None:
    async def scenario() -> None:
        runtime, repo, api, db = make_runtime()
        await complete_run(runtime, db)

        run = db.get_run(RUN_ID)
        assert run is not None and run["execution_status"] == "failed"  # no scan nodes
        rows = repo.rows_for(RUN_ID)
        assert sorted(row["kind"] for row in rows) == ["alert", "jsonc"]

        delivered = await runtime.process_outbox_once()
        assert delivered == 2
        assert len(api.messages) == 1 and len(api.documents) == 1
        chat_id, text, _ = api.messages[0]
        assert chat_id == CHAT_ID and RUN_ID in text
        assert repo.statuses() == ["sent", "sent"]

    asyncio.run(scenario())


def test_running_run_is_not_enqueued() -> None:
    async def scenario() -> None:
        runtime, repo, _api, db = make_runtime()
        db.add_run(RUN_ID, status="running")
        await runtime.on_run_completed(RUN_ID)
        assert repo.rows_for(RUN_ID) == []

    asyncio.run(scenario())


# --- 2. severity threshold ---------------------------------------------

def _settings(**kwargs: Any) -> Settings:
    return Settings(chat_id=CHAT_ID, **kwargs)


def test_severity_threshold_below_on_above_policy() -> None:
    run = {"critical_count": 0, "high_count": 2, "medium_count": 0,
           "low_count": 0, "info_count": 0}
    assert run_max_severity(run) == "high"

    def decide(threshold: str):
        return evaluate_delivery(
            kind="alert", delivery_mode="both", run=run,
            min_severity=threshold, project_filter="", branch_filter="",
            quiet_start=None, quiet_end=None, quiet_timezone="UTC",
            facts=DeliveryFacts(), now_utc=datetime.now(timezone.utc),
        )

    below = decide("medium")    # threshold below the run's severity
    on = decide("high")         # threshold exactly at the run's severity
    above = decide("critical")  # threshold above the run's severity
    assert below.deliver and below.reason == ""
    assert on.deliver and on.reason == ""
    assert not above.deliver and "critical" in above.reason


def test_severity_threshold_delivers_or_cancels() -> None:
    async def scenario(threshold: str, expect_send: bool) -> None:
        runtime, repo, api, db = make_runtime(
            settings=_settings(min_severity=threshold),
        )
        db.add_run(
            RUN_ID, status="failed", verdict="fail",
            counts={"total": 2, "high": 2},
        )
        await runtime.on_run_completed(RUN_ID)
        delivered = await runtime.process_outbox_once()
        if expect_send:
            assert delivered == 2
            assert len(api.messages) == 1 and len(api.documents) == 1
        else:
            assert delivered == 2
            assert api.messages == [] and api.documents == []
            assert repo.statuses() == ["cancelled", "cancelled"]
            assert all(
                "порог" in (row["last_error"] or "") for row in repo.rows_for(RUN_ID)
            )

    asyncio.run(scenario("medium", True))   # below: deliver
    asyncio.run(scenario("high", True))     # on: deliver
    asyncio.run(scenario("critical", False))  # above: skip


# --- 3. quiet hours defer the send -------------------------------------

def test_quiet_hours_defer_delivery() -> None:
    async def scenario() -> None:
        now = datetime.now(timezone.utc)
        start = (now - timedelta(hours=1)).strftime("%H:%M")
        end = (now + timedelta(hours=2)).strftime("%H:%M")
        settings = _settings(
            quiet_start=time.fromisoformat(start),
            quiet_end=time.fromisoformat(end),
            quiet_timezone="UTC",
        )
        runtime, repo, api, db = make_runtime(settings=settings)
        db.add_run(RUN_ID, status="succeeded", verdict="pass", counts={"total": 1, "low": 1})
        await runtime.on_run_completed(RUN_ID)

        await runtime.process_outbox_once()
        assert api.messages == [] and api.documents == []
        rows = repo.rows_for(RUN_ID)
        assert all(row["status"] == "pending" for row in rows)
        window_end = now + timedelta(hours=2)
        for row in rows:
            assert window_end - timedelta(minutes=5) <= row["next_attempt_at"] <= window_end + timedelta(minutes=5)
            # A deferral is not a failure: the claim increment is compensated
            # (GREATEST(attempts - 1, 0)), so deferrals never eat the budget.
            assert row["attempts"] == 0
            assert "тихие часы" in (row["last_error"] or "")

        # A repeat claim during the window neither sends nor duplicates.
        await runtime.process_outbox_once()
        assert api.messages == [] and api.documents == []
        assert len(repo.rows_for(RUN_ID)) == 2
        assert repo.claim_count == 2

        # Window over (quiet hours switched off): the same row is delivered once.
        runtime.repo.settings = _settings()
        for row in repo.rows_for(RUN_ID):
            row["next_attempt_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        await runtime.process_outbox_once()
        assert len(api.messages) == 1 and len(api.documents) == 1
        assert repo.statuses() == ["sent", "sent"]

    asyncio.run(scenario())


def test_quiet_deferral_window_math() -> None:
    # Moscow (UTC+3): 21:30 UTC on 15 June is 00:30 local, inside 22:00–07:00.
    now = datetime(2026, 6, 15, 23, 30, tzinfo=timezone.utc)
    local = now.astimezone(ZoneInfo("Europe/Moscow"))
    assert (local.hour, local.day) == (2, 16)
    defer = quiet_deferral(now, time(22, 0), time(7, 0), "Europe/Moscow")
    assert defer is not None
    # The window ends 07:00 Moscow on 16 June = 04:00 UTC.
    assert defer == datetime(2026, 6, 16, 4, 0, tzinfo=timezone.utc)

    # Outside the window: no deferral.
    assert quiet_deferral(
        datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc),
        time(22, 0), time(7, 0), "Europe/Moscow",
    ) is None
    # Degenerate window disables quiet hours; broken timezone never defers.
    assert quiet_deferral(now, time(22, 0), time(22, 0), "UTC") is None
    assert quiet_deferral(now, time(22, 0), time(7, 0), "Not/AZone") is None
    # Overnight window in UTC crosses midnight.
    cross = quiet_deferral(
        datetime(2026, 6, 15, 23, 30, tzinfo=timezone.utc),
        time(23, 0), time(1, 0), "UTC",
    )
    assert cross == datetime(2026, 6, 16, 1, 0, tzinfo=timezone.utc)


# --- 4. repeats never duplicate ----------------------------------------

def test_repeat_completion_and_claim_do_not_duplicate() -> None:
    async def scenario() -> None:
        runtime, repo, api, db = make_runtime()
        executor = await complete_run(runtime, db)
        assert len(repo.rows_for(RUN_ID)) == 2

        # Repeated completions: a second dispatcher, scheduler recovery, ...
        await runtime.on_run_completed(RUN_ID)
        await runtime.on_run_completed(RUN_ID)
        # ... and re-running the same completed run through the executor.
        await executor.execute_run(RUN_ID, manual_draft(), None, trigger_type="schedule")
        assert len(repo.rows_for(RUN_ID)) == 2

        first = await runtime.process_outbox_once()
        second = await runtime.process_outbox_once()
        assert first == 2 and second == 0
        assert len(api.messages) == 1 and len(api.documents) == 1

    asyncio.run(scenario())


# --- 5. JSONC findings survive the delivery path -----------------------

def test_jsonc_delivery_keeps_findings() -> None:
    async def scenario() -> None:
        runtime, _repo, api, db = make_runtime()
        db.add_run(
            RUN_ID, status="failed", verdict="fail",
            counts={"total": 3, "high": 1, "medium": 1, "low": 1},
        )
        db.findings[RUN_ID] = [
            {"id": "F-SECRET-1", "severity": "high", "ruleId": "SEC-1",
             "status": "confirmed", "primaryLocation": None},
            {"id": "F-SCA-2", "severity": "medium", "ruleId": "SCA-1",
             "status": "confirmed", "primaryLocation": None},
            {"id": "F-SAST-3", "severity": "low", "ruleId": "SAST-1",
             "status": "potential", "primaryLocation": None},
        ]
        await runtime.on_run_completed(RUN_ID)
        await runtime.process_outbox_once()

        assert len(api.documents) == 1
        _chat_id, filename, content, caption = api.documents[0]
        assert filename.endswith(f"{RUN_ID}.jsonc")
        document = json.loads(content.decode("utf-8"))
        ids = {item["id"] for item in document["deterministic"]["findings"]}
        assert ids == {"F-SECRET-1", "F-SCA-2", "F-SAST-3"}
        assert document["deterministic"]["count"] == 3
        assert document["run"]["id"] == RUN_ID
        assert RUN_ID in caption

    asyncio.run(scenario())


# --- 6. project / branch scope -----------------------------------------

META = {
    "filename": "contract-abc123.zip",
    "repository": {
        "url": "https://github.com/acme/contract",
        "branch": "release/1.4",
    },
}


def test_project_and_branch_filters() -> None:
    async def scenario(settings: Settings, expect_send: bool) -> None:
        runtime, repo, api, db = make_runtime(settings=settings, meta=META)
        db.add_run(
            RUN_ID, status="failed", verdict="fail", artifact_id="art-1",
            counts={"total": 1, "medium": 1},
        )
        await runtime.on_run_completed(RUN_ID)
        await runtime.process_outbox_once()
        if expect_send:
            assert len(api.messages) == 1 and len(api.documents) == 1
        else:
            assert api.messages == [] and api.documents == []
            assert repo.statuses() == ["cancelled", "cancelled"]

    asyncio.run(scenario(_settings(project_filter="github.com/acme/contract"), True))
    asyncio.run(scenario(_settings(project_filter="github.com/other/"), False))
    asyncio.run(scenario(_settings(project_filter="contract-abc123.zip"), True))
    asyncio.run(scenario(_settings(branch_filter="release/1.4"), True))
    asyncio.run(scenario(_settings(branch_filter="release/*"), True))
    asyncio.run(scenario(_settings(branch_filter="main"), False))


def test_filter_without_artifact_fails_closed() -> None:
    async def scenario() -> None:
        runtime, repo, api, db = make_runtime(settings=_settings(project_filter="acme"))
        db.add_run(RUN_ID, status="failed", artifact_id=None, counts={"total": 1})
        await runtime.on_run_completed(RUN_ID)
        await runtime.process_outbox_once()
        assert api.messages == [] and api.documents == []
        assert repo.statuses() == ["cancelled", "cancelled"]

    asyncio.run(scenario())


def _force_rows_due(repo: FakeDeliveryRepo) -> None:
    for row in repo.rows_for(RUN_ID):
        row["next_attempt_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)


# --- 7. durable retry after a transient DB failure ----------------------

def test_transient_outbox_failure_recovers_via_sweep() -> None:
    async def scenario() -> None:
        runtime, repo, api, db = make_runtime()
        db.add_run(
            RUN_ID, status="failed", verdict="fail", counts={"total": 1, "medium": 1},
        )
        # Completion lands while the database refuses the outbox writes.
        repo.fail_bindings = True
        await runtime.on_run_completed(RUN_ID)
        assert repo.rows_for(RUN_ID) == []
        # The completion fact survived as a pending durable marker.
        assert repo.intents[RUN_ID]["status"] == "pending"
        assert repo.intents[RUN_ID]["attempts"] == 1  # touch recorded the failure

        await runtime.process_outbox_once()
        assert api.messages == [] and api.documents == []

        # Recovery: the background sweep replays the intent idempotently.
        repo.fail_bindings = False
        assert await runtime._sweep_delivery_intents() == 1
        rows = repo.rows_for(RUN_ID)
        assert sorted(row["kind"] for row in rows) == ["alert", "jsonc"]
        assert repo.intents[RUN_ID]["status"] == "done"

        assert await runtime.process_outbox_once() == 2
        assert len(api.messages) == 1 and len(api.documents) == 1

        # A second sweep finds nothing and never duplicates the delivery.
        assert await runtime._sweep_delivery_intents() == 0
        assert len(repo.rows_for(RUN_ID)) == 2

    asyncio.run(scenario())


def test_transient_intent_write_failure_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_module, "DELIVERY_INTENT_RECORD_DELAY_SECONDS", 0.0)

    async def scenario() -> None:
        runtime, repo, api, db = make_runtime()
        db.add_run(RUN_ID, status="succeeded", verdict="pass", counts={"total": 1, "low": 1})
        # The marker write itself fails once, then the database recovers.
        repo.fail_record_times = 1
        await runtime.on_run_completed(RUN_ID)
        assert len(repo.rows_for(RUN_ID)) == 2
        assert repo.intents[RUN_ID]["status"] == "done"

        assert await runtime.process_outbox_once() == 2
        assert len(api.messages) == 1 and len(api.documents) == 1

    asyncio.run(scenario())


def test_completion_during_stop_still_enqueues() -> None:
    async def scenario() -> None:
        runtime, repo, api, db = make_runtime()
        # stop() flipped the transport state while the run was finishing.
        runtime._started = False
        db.add_run(
            RUN_ID, status="interrupted", verdict="inconclusive",
            counts={"total": 1, "low": 1},
        )
        await runtime.on_run_completed(RUN_ID)

        rows = repo.rows_for(RUN_ID)
        assert sorted(row["kind"] for row in rows) == ["alert", "jsonc"]
        assert repo.intents[RUN_ID]["status"] == "done"
        assert api.messages == [] and api.documents == []

        # Rows are durable: the next started transport drains them.
        await runtime.process_outbox_once()
        assert len(api.messages) == 1 and len(api.documents) == 1

    asyncio.run(scenario())


# --- 8. deferrals never exhaust the retry budget ------------------------

def test_defer_never_eats_attempts() -> None:
    async def scenario() -> None:
        now = datetime.now(timezone.utc)
        settings = _settings(
            quiet_start=(now - timedelta(hours=1)).time(),
            quiet_end=(now + timedelta(hours=2)).time(),
            quiet_timezone="UTC",
        )
        runtime, repo, api, db = make_runtime(settings=settings)
        db.add_run(RUN_ID, status="succeeded", verdict="pass", counts={"total": 1, "low": 1})
        await runtime.on_run_completed(RUN_ID)

        # Four claims during the window: every deferral compensates its claim.
        for _cycle in range(4):
            _force_rows_due(repo)
            await runtime.process_outbox_once()
            for row in repo.rows_for(RUN_ID):
                assert row["status"] == "pending"
                assert row["attempts"] == 0
            assert api.messages == [] and api.documents == []

        # Window over; the first transport error must not exhaust the budget.
        runtime.repo.settings = _settings()
        _force_rows_due(repo)
        api.send_error = RuntimeError("boom")
        await runtime.process_outbox_once()
        for row in repo.rows_for(RUN_ID):
            assert row["status"] == "pending"  # not 'failed' after 4 deferrals
            assert row["attempts"] == 1

        # Transient error resolved: the same rows deliver exactly once.
        api.send_error = None
        _force_rows_due(repo)
        await runtime.process_outbox_once()
        assert repo.statuses() == ["sent", "sent"]
        assert len(api.messages) == 1 and len(api.documents) == 1

    asyncio.run(scenario())


# --- 9. DST transitions -------------------------------------------------

def test_quiet_deferral_dst_fallback_end() -> None:
    tz = "America/New_York"
    # 2026-11-01 06:00 UTC: 02:00 EDT falls back to 01:00 EST, so 01:30 local
    # happens twice (05:30 and 06:30 UTC).
    window = (time(0, 0), time(1, 30))
    # 06:15 UTC = 01:15 EST (second pass): the end must be the *next real*
    # end, 06:30 UTC — not the fold=0 05:30 UTC already in the past.
    assert quiet_deferral(
        datetime(2026, 11, 1, 6, 15, tzinfo=timezone.utc), *window, tz
    ) == datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)
    # First pass (05:15 UTC = 01:15 EDT) defers to the first 01:30 = 05:30 UTC.
    assert quiet_deferral(
        datetime(2026, 11, 1, 5, 15, tzinfo=timezone.utc), *window, tz
    ) == datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)
    # After the last 01:30 the window is over: no deferral to the past.
    assert quiet_deferral(
        datetime(2026, 11, 1, 6, 45, tzinfo=timezone.utc), *window, tz
    ) is None
    # Before the window starts: nothing to defer to.
    assert quiet_deferral(
        datetime(2026, 11, 1, 3, 30, tzinfo=timezone.utc), *window, tz
    ) is None
