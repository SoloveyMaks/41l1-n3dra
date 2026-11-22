"""POST /api/runs/{id}/cancel: остановка queued/running, идемпотентность, scope.

Покрывает контракт «Стоп»:

* без сессии — 401, чужой workspace — 404, терминальный run — 200 с текущим
  статусом и без единой записи/события;
* queued cancel: атомарный переход cancelled + finished_at + inconclusive,
  ровно одно событие run_status, executor не стартует — ни по флагу отмены,
  ни по проверке статуса до claim_run_start;
* running cancel (ручной запуск): задача гасится через реестр
  ``manual_run_tasks``, финализирует внешний CancelledError-обработчик —
  cancelled, finished_at, отчёт executionStatus cancelled, одно событие;
  повторный cancel события не дублирует;
* running cancel (cooperative, как у scheduler/repository): ответ «cancelling»,
  после снятия узла прогон сам доезжает до terminal cancelled с событием;
* реестр manual-run задач отменяет только свою задачу и не теряет остальные.

PostgreSQL подменён in-memory-пулом, который понимает ровно запросы
``runtime.runs``/``run_events``/``reports``/``node_runs``; сессии — настоящие
v3-токены, workspace задаётся окружением.

Run: PYTHONPATH=. /tmp/nedra-venv/bin/python -m pytest backend/tests/test_run_cancel.py -q
"""
from __future__ import annotations

import asyncio
import json
import re
import secrets
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.app.api import session as session_api
from backend.app.contracts.schemas import NodeKind, WorkflowDraft, WorkflowNode
from backend.app.engine.executor import WorkflowExecutor
from backend.app.engine.sse import SSEBroker
from backend.app.storage import accounts, database as database_storage

WORKSPACE = str(uuid.uuid4())
FOREIGN = str(uuid.uuid4())
RUN_ID = "run-stop-1"
FOREIGN_RUN = "run-foreign-1"
NOW = "2026-11-22T12:00:00+00:00"


class _Result:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = rows

    def fetchall(self) -> List[Dict[str, Any]]:
        return list(self._rows)

    def fetchone(self) -> Optional[Dict[str, Any]]:
        return self._rows[0] if self._rows else None


def _unwrap(value: Any) -> Any:
    """psycopg Jsonb -> обычный python-значение для in-memory строки."""
    return getattr(value, "obj", value)


class FakeRunPool:
    """Фасад пула: понимает ровно те запросы, которые делает cancel-флоу."""

    def __init__(self) -> None:
        self.runs: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.reports: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.node_runs: List[Dict[str, Any]] = []
        self.events: List[Dict[str, Any]] = []
        self.queries: List[str] = []
        self._seq = 0

    # --- facade ------------------------------------------------------------
    def connection(self) -> "FakeRunPool":
        return self

    def __enter__(self) -> "FakeRunPool":
        return self

    def __exit__(self, *exc_info: Any) -> bool:
        return False

    # --- SQL ---------------------------------------------------------------
    def execute(self, sql: str, params: Any = ()) -> _Result:
        text = " ".join(sql.split())
        bound = tuple(params)
        self.queries.append(text)

        if text.startswith("SELECT * FROM runtime.runs WHERE"):
            ws, run_id = str(bound[0]), str(bound[1])
            row = self.runs.get((ws, run_id))
            return _Result([dict(row)] if row else [])

        if "COALESCE(MAX(seq), 0) AS max_seq" in text:
            ws, run_id = str(bound[0]), str(bound[1])
            seq = max(
                (event["seq"] for event in self.events
                 if event["workspace_id"] == ws and event["run_id"] == run_id),
                default=0,
            )
            return _Result([{"max_seq": seq}])

        if text.startswith("UPDATE runtime.runs SET execution_status = 'running'"):
            started_at, ws, run_id = bound[0], str(bound[1]), str(bound[2])
            row = self.runs.get((ws, run_id))
            if not row or row["execution_status"] != "queued":
                return _Result([])
            row["execution_status"] = "running"
            row["started_at"] = started_at
            return _Result([{"id": run_id}])

        if text.startswith(
            "UPDATE runtime.runs SET execution_status = 'cancelled', "
            "security_verdict = 'inconclusive'"
        ):
            finished_at, ws, run_id = bound[0], str(bound[1]), str(bound[2])
            row = self.runs.get((ws, run_id))
            if not row or row["execution_status"] != "queued":
                return _Result([])
            row["execution_status"] = "cancelled"
            row["security_verdict"] = "inconclusive"
            row["finished_at"] = finished_at
            return _Result([{"id": run_id}])

        if text.startswith("UPDATE runtime.runs SET execution_status = %s"):
            return self._update_run_status(text, bound)

        if text.startswith("INSERT INTO runtime.run_events"):
            ws, run_id, timestamp, event_type, node_id, payload = bound
            self._seq += 1
            self.events.append({
                "seq": self._seq,
                "workspace_id": str(ws),
                "run_id": str(run_id),
                "timestamp": timestamp,
                "type": event_type,
                "node_id": node_id,
                "payload": _unwrap(payload),
            })
            return _Result([{"seq": self._seq}])

        if text.startswith("SELECT * FROM runtime.reports WHERE"):
            ws, run_id = str(bound[0]), str(bound[1])
            row = self.reports.get((ws, run_id))
            return _Result([dict(row)] if row else [])

        if text.startswith("INSERT INTO runtime.reports"):
            (ws, run_id, verdict, passport, ai_summary, ai_items, ai_status,
             readable, official, created_at) = bound
            self.reports[(str(ws), str(run_id))] = {
                "workspace_id": str(ws),
                "run_id": str(run_id),
                "verdict": verdict,
                "passport_json": _unwrap(passport),
                "ai_summary": ai_summary,
                "ai_items_json": _unwrap(ai_items),
                "ai_status": ai_status,
                "readable_markdown": readable,
                "official_json": official,
                "created_at": created_at,
            }
            return _Result([])

        if text.startswith("INSERT INTO runtime.node_runs"):
            (ws, run_id, node_run_id, node_id, kind, status, started_at,
             finished_at, duration_ms, error_message, input_json,
             output_json) = bound
            self.node_runs.append({
                "workspace_id": str(ws),
                "run_id": str(run_id),
                "id": node_run_id,
                "node_id": node_id,
                "kind": kind,
                "status": status,
                "started_at": started_at,
                "finished_at": finished_at,
                "duration_ms": duration_ms,
                "error_message": error_message,
                "input_data_json": _unwrap(input_json),
                "output_data_json": _unwrap(output_json),
            })
            return _Result([])

        if text.startswith("SELECT * FROM runtime.node_runs WHERE"):
            ws, run_id = str(bound[0]), str(bound[1])
            rows = [dict(row) for row in self.node_runs
                    if row["workspace_id"] == ws and row["run_id"] == run_id]
            return _Result(rows)

        raise AssertionError(f"запрос вне заглушки отмены запуска: {text}")

    def _update_run_status(self, text: str, bound: Tuple[Any, ...]) -> _Result:
        match = re.match(r"UPDATE runtime\.runs SET (.*) WHERE (.*)$", text)
        assert match is not None, text
        set_part, where_part = match.groups()
        # Границы выражений SET: «, ключ = » — запятая внутри NULLIF(%s, '') не рвёт.
        clauses = re.split(r", (?=[A-Za-z_][A-Za-z_0-9]* = )", set_part)
        keys = [clause.split(" = ", 1)[0] for clause in clauses]
        guarded = "OR %s = 'cancelled'" in where_part
        if guarded:
            new_status = str(bound[len(keys)])
            ws, run_id = str(bound[len(keys) + 1]), str(bound[len(keys) + 2])
        else:
            new_status = str(bound[0])
            ws, run_id = str(bound[len(keys)]), str(bound[len(keys) + 1])
        row = self.runs.get((ws, run_id))
        if not row:
            return _Result([])
        # Тот же контракт, что и в SQL: cancelled не переписывается ничем,
        # кроме cancelled.
        if guarded and row["execution_status"] == "cancelled" and new_status != "cancelled":
            return _Result([])
        for key, value in zip(keys, bound[:len(keys)]):
            row[key] = value
        return _Result([])


@pytest.fixture()
def pool(monkeypatch: pytest.MonkeyPatch) -> FakeRunPool:
    monkeypatch.setenv("CG_WORKSPACE_ID", WORKSPACE)
    monkeypatch.delenv("RUNTIME_WORKSPACE_ID", raising=False)
    fake = FakeRunPool()
    monkeypatch.setattr(database_storage, "get_pool", lambda: fake)
    return fake


@pytest.fixture()
def api(pool: FakeRunPool, monkeypatch: pytest.MonkeyPatch):
    """Реальный api_router со свежими сервисами: лок/семафор привязаны к циклу."""
    monkeypatch.setattr(database_storage, "init_db", lambda *args, **kwargs: None)
    from backend.app.api import router as api_module

    repo = database_storage.DatabaseRepository(initialize=False)
    sse = SSEBroker(repo)
    executor = WorkflowExecutor(repo, sse)
    monkeypatch.setattr(api_module, "db_repo", repo)
    monkeypatch.setattr(api_module, "sse_broker", sse)
    monkeypatch.setattr(api_module, "executor", executor)
    monkeypatch.setattr(api_module, "manual_run_tasks", api_module._ManualRunTasks())
    monkeypatch.setattr(api_module, "CANCEL_WAIT_SECONDS", 2.0)
    return api_module


@pytest.fixture()
def client(api) -> TestClient:
    app = FastAPI()
    app.include_router(api.api_router)
    return TestClient(app)


@pytest.fixture()
def issue(monkeypatch: pytest.MonkeyPatch):
    """Настоящие v3-токены: HMAC и срок проверяются штатно, store — заглушка."""
    store: Dict[str, Dict[str, Any]] = {}

    def create_session(user: Dict[str, Any], ttl_seconds: int,
                       role: Optional[str] = None) -> Dict[str, Any]:
        session_id = secrets.token_hex(16)
        issued = int(time.time())
        record = {
            "sessionId": session_id,
            "userId": str(user["userId"]),
            "workspaceId": str(user["workspaceId"]),
            "role": role or user.get("role") or "user",
            "issued": issued,
            "expires": issued + int(ttl_seconds),
        }
        store[session_id] = record
        return record

    def load_session(session_id: str) -> Optional[Dict[str, Any]]:
        record = store.get(session_id)
        if record is None:
            return None
        return {
            **record,
            "issuedAt": record["issued"],
            "expiresAt": record["expires"],
            "email": "tester@example.org",
            "displayName": "Тестер",
        }

    monkeypatch.setattr(accounts, "create_session", create_session)
    monkeypatch.setattr(accounts, "load_session", load_session)

    def issue_token(role: str = "user") -> str:
        user = {
            "userId": str(uuid.uuid4()),
            "workspaceId": WORKSPACE,
            "role": role,
            "email": "tester@example.org",
            "displayName": "Тестер",
        }
        signed, _ = session_api.issue_account_session(user, role)
        return signed

    return issue_token


# --- helpers ---------------------------------------------------------------
def seed_run(pool: FakeRunPool, run_id: str, status: str, *,
             workspace: str = WORKSPACE, verdict: str = "pending",
             started_at: Optional[str] = None,
             finished_at: Optional[str] = None) -> Dict[str, Any]:
    row = {
        "id": run_id,
        "workspace_id": workspace,
        "workflow_id": "wf-stop",
        "revision_id": "rev-stop",
        "artifact_id": None,
        "trigger_type": "manual",
        "execution_status": status,
        "security_verdict": verdict,
        "created_at": NOW,
        "started_at": started_at,
        "finished_at": finished_at,
        "total_findings": 0,
        "critical_count": 0,
        "high_count": 0,
        "medium_count": 0,
        "low_count": 0,
        "info_count": 0,
    }
    pool.runs[(workspace, run_id)] = row
    return row


def seed_report(pool: FakeRunPool, run_id: str, *, workspace: str = WORKSPACE,
                execution_status: str = "running") -> None:
    """Отчёт, который BUILD_REPORT пишет до финализации прогона."""
    pool.reports[(workspace, run_id)] = {
        "workspace_id": workspace,
        "run_id": run_id,
        "verdict": "pass",
        "passport_json": {"summary": "seed"},
        "ai_summary": "",
        "ai_items_json": [],
        "ai_status": "disabled",
        "readable_markdown": "# Отчёт\n\n**Статус:** выполняется  \n",
        "official_json": json.dumps(
            {"metadata": {"executionStatus": execution_status}},
            ensure_ascii=False, indent=2,
        ),
        "created_at": NOW,
    }


def cancelled_events(pool: FakeRunPool) -> List[Dict[str, Any]]:
    return [
        event for event in pool.events
        if event["type"] == "run_status"
        and isinstance(event["payload"], dict)
        and event["payload"].get("status") == "cancelled"
    ]


def cancel_request(token: Optional[str] = None) -> Request:
    headers = (
        [(b"cookie", f"cg_session={token}".encode("ascii"))] if token else []
    )
    return Request({
        "type": "http",
        "method": "POST",
        "path": f"/api/runs/{RUN_ID}/cancel",
        "raw_path": f"/api/runs/{RUN_ID}/cancel".encode(),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 4242),
        "scheme": "http",
        "server": ("testserver", 80),
        "root_path": "",
    })


def cookie(token: str) -> Dict[str, str]:
    return {"cookie": f"cg_session={token}"}


def draft() -> WorkflowDraft:
    return WorkflowDraft(
        schemaVersion=1,
        name="Stop scenario",
        nodes=[WorkflowNode(id="trigger", kind=NodeKind.MANUAL_TRIGGER, title="Start")],
        edges=[],
    )


# --- HTTP contract ---------------------------------------------------------
def test_cancel_requires_session(client: TestClient) -> None:
    response = client.post(f"/api/runs/{RUN_ID}/cancel")

    assert response.status_code == 401
    assert response.json()["detail"] == "Требуется вход"


def test_foreign_workspace_run_is_not_found(
    client: TestClient, pool: FakeRunPool, issue,
) -> None:
    seed_run(pool, FOREIGN_RUN, "running", workspace=FOREIGN)

    response = client.post(f"/api/runs/{FOREIGN_RUN}/cancel", headers=cookie(issue()))

    assert response.status_code == 404
    assert response.json()["detail"] == "Запуск не найден"
    assert pool.runs[(FOREIGN, FOREIGN_RUN)]["execution_status"] == "running"
    assert pool.events == []


def test_terminal_run_answers_current_status_unchanged(
    client: TestClient, pool: FakeRunPool, issue,
) -> None:
    seed_run(pool, RUN_ID, "succeeded", verdict="pass",
             started_at=NOW, finished_at=NOW)

    response = client.post(f"/api/runs/{RUN_ID}/cancel", headers=cookie(issue()))

    assert response.status_code == 200
    assert response.json() == {"runId": RUN_ID, "status": "succeeded"}
    row = pool.runs[(WORKSPACE, RUN_ID)]
    assert row["security_verdict"] == "pass"
    assert row["finished_at"] == NOW
    assert pool.events == []


# --- queued ----------------------------------------------------------------
def test_queued_cancel_stops_executor_before_start(
    api, pool: FakeRunPool, issue,
) -> None:
    async def scenario() -> None:
        seed_run(pool, RUN_ID, "queued")
        token = issue()

        answer = await api.cancel_run(RUN_ID, cancel_request(token))
        assert answer == {"runId": RUN_ID, "status": "cancelled"}
        row = pool.runs[(WORKSPACE, RUN_ID)]
        assert row["execution_status"] == "cancelled"
        assert row["security_verdict"] == "inconclusive"
        assert row["finished_at"]
        assert len(cancelled_events(pool)) == 1

        # Поздний подхват прогона: флаг отмены не даёт стартовать.
        await api.executor.execute_run(RUN_ID, draft(), None, trigger_type="manual")
        row = pool.runs[(WORKSPACE, RUN_ID)]
        assert row["execution_status"] == "cancelled"
        assert row["started_at"] is None
        assert pool.node_runs == []
        assert len(cancelled_events(pool)) == 1
        assert not any(
            event["type"] == "run_status"
            and event["payload"].get("status") == "running"
            for event in pool.events
        )
        # execute_run снял флаг в своём finally — защита статусом остаётся.
        assert not api.executor.is_cancel_requested(RUN_ID)

        # Даже без флага проверка статуса до claim_run_start держит cancelled.
        await api.executor.execute_run(RUN_ID, draft(), None, trigger_type="manual")
        row = pool.runs[(WORKSPACE, RUN_ID)]
        assert row["started_at"] is None
        assert pool.node_runs == []
        assert len(cancelled_events(pool)) == 1

    asyncio.run(scenario())


def test_repeated_cancel_does_not_duplicate_event(
    api, pool: FakeRunPool, issue,
) -> None:
    async def scenario() -> None:
        seed_run(pool, RUN_ID, "queued")
        token = issue()

        first = await api.cancel_run(RUN_ID, cancel_request(token))
        second = await api.cancel_run(RUN_ID, cancel_request(token))

        assert first["status"] == "cancelled"
        assert second["status"] == "cancelled"
        assert len(pool.events) == 1
        assert len(cancelled_events(pool)) == 1

    asyncio.run(scenario())


# --- running: manual registry --------------------------------------------
def test_running_cancel_settles_cancelled_through_manual_registry(
    api, pool: FakeRunPool, issue,
) -> None:
    async def scenario() -> None:
        seed_run(pool, RUN_ID, "queued")
        seed_report(pool, RUN_ID)
        token = issue()
        started = asyncio.Event()
        executor = api.executor

        async def blocking_node(run_id: str, *_rest: Any) -> Tuple[Dict[str, Any], None]:
            # Долгий узел: завершается только после запроса отмены.
            started.set()
            while not executor.is_cancel_requested(run_id):
                await asyncio.sleep(0.01)
            return {}, None

        executor._run_node_logic = blocking_node
        task = api.manual_run_tasks.spawn(
            executor.execute_run(RUN_ID, draft(), None, trigger_type="manual"),
            name=f"manual-run-{RUN_ID}",
        )
        await started.wait()
        assert pool.runs[(WORKSPACE, RUN_ID)]["execution_status"] == "running"

        answer = await api.cancel_run(RUN_ID, cancel_request(token))
        assert answer == {"runId": RUN_ID, "status": "cancelled"}
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)  # done-коллбэк реестра убирает задачу

        row = pool.runs[(WORKSPACE, RUN_ID)]
        assert row["execution_status"] == "cancelled"
        assert row["finished_at"]
        report = pool.reports[(WORKSPACE, RUN_ID)]
        official = json.loads(report["official_json"])
        assert official["metadata"]["executionStatus"] == "cancelled"
        assert "**Статус:** отменён" in report["readable_markdown"]
        assert len(cancelled_events(pool)) == 1
        assert api.manual_run_tasks._tasks == set()

        # Повторный «Стоп» на остановленном run отвечает тем же статусом
        # и не добавляет второго события.
        again = await api.cancel_run(RUN_ID, cancel_request(token))
        assert again == {"runId": RUN_ID, "status": "cancelled"}
        assert len(cancelled_events(pool)) == 1

    asyncio.run(scenario())


# --- running: cooperative (scheduler/repository shape) ---------------------
def test_running_cancel_answers_cancelling_then_settles_cooperatively(
    api, pool: FakeRunPool, issue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api, "CANCEL_WAIT_SECONDS", 0.2)

    async def scenario() -> None:
        seed_run(pool, RUN_ID, "queued")
        seed_report(pool, RUN_ID)
        token = issue()
        started = asyncio.Event()
        release = asyncio.Event()
        executor = api.executor

        async def slow_node(_run_id: str, *_rest: Any) -> Tuple[Dict[str, Any], None]:
            # Узел не замечает флаг — как долгий сканер вне реестра задач.
            started.set()
            await release.wait()
            return {}, None

        executor._run_node_logic = slow_node
        run_task = asyncio.create_task(
            executor.execute_run(RUN_ID, draft(), None, trigger_type="schedule"),
        )
        await started.wait()

        answer = await api.cancel_run(RUN_ID, cancel_request(token))
        assert answer == {"runId": RUN_ID, "status": "cancelling"}
        assert pool.runs[(WORKSPACE, RUN_ID)]["execution_status"] == "running"

        release.set()
        await run_task

        row = pool.runs[(WORKSPACE, RUN_ID)]
        assert row["execution_status"] == "cancelled"
        assert row["finished_at"]
        report = pool.reports[(WORKSPACE, RUN_ID)]
        official = json.loads(report["official_json"])
        assert official["metadata"]["executionStatus"] == "cancelled"
        assert len(cancelled_events(pool)) == 1

    asyncio.run(scenario())


# --- registry --------------------------------------------------------------
def test_manual_registry_cancels_only_matching_task(api) -> None:
    async def scenario() -> None:
        registry = api.manual_run_tasks

        async def hold() -> None:
            await asyncio.Event().wait()

        task_a = registry.spawn(hold(), name=f"manual-run-{RUN_ID}")
        task_b = registry.spawn(hold(), name="manual-run-other")

        assert registry.cancel(RUN_ID) is True
        await asyncio.gather(task_a, return_exceptions=True)
        await asyncio.sleep(0)

        assert task_a.cancelled()
        assert not task_b.done()
        assert task_b in registry._tasks
        assert registry.cancel("no-such-run") is False

        await registry.aclose()
        assert registry._tasks == set()

    asyncio.run(scenario())
