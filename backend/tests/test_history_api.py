"""Серверные фильтры, пагинация и изоляция истории запусков.

Покрывает ``GET /api/auth/history`` и ``GET /api/profile/history``: фильтры
проект/сценарий/статус, окно LIMIT/OFFSET, пустую выборку и недоступность
чужого workspace. PostgreSQL подменён локальной заглушкой, которая понимает
только запросы ``history_page``; сеть и внешние сервисы не используются.

Run: PYTHONPATH=. /tmp/nedra-venv/bin/python -m pytest backend/tests/test_history_api.py -q
"""
from __future__ import annotations

import secrets
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import session as session_api
from backend.app.api.auth import create_auth_router
from backend.app.api.profile import create_profile_router
from backend.app.storage import accounts, profile as profile_storage

MINE = str(uuid.uuid4())
FOREIGN = str(uuid.uuid4())
ART_SITE = str(uuid.uuid4())
ART_API = str(uuid.uuid4())
ART_FOREIGN = str(uuid.uuid4())
WF_PR = str(uuid.uuid4())
WF_DEPS = str(uuid.uuid4())
WF_FOREIGN = str(uuid.uuid4())
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)

WORKFLOWS: Dict[Any, Optional[str]] = {
    WF_PR: "Проверка PR",
    WF_DEPS: "Скан зависимостей",
    WF_FOREIGN: "Чужой сценарий",
}
ARTIFACTS: Dict[Any, Optional[str]] = {
    ART_SITE: "site.zip",
    ART_API: "api.zip",
    ART_FOREIGN: "foreign.zip",
}


def make_run(run_id: str, workspace: str, *, minutes: int = 0, status: str = "succeeded",
             workflow: Optional[str] = None, artifact: Optional[str] = None) -> Dict[str, Any]:
    created = NOW - timedelta(minutes=minutes)
    return {
        "id": run_id,
        "workflow_id": workflow,
        "revision_id": f"rev-{run_id}",
        "artifact_id": artifact,
        "trigger_type": "manual",
        "execution_status": status,
        "security_verdict": "pass",
        "total_findings": 0,
        "critical_count": 0,
        "high_count": 0,
        "medium_count": 0,
        "low_count": 0,
        "info_count": 0,
        "started_at": created,
        "finished_at": created,
        "created_at": created,
        "workspace_id": workspace,
    }


def base_runs() -> List[Dict[str, Any]]:
    """Четыре запуска моего workspace и один чужой, самый свежий — первый."""
    return [
        make_run("r-pr-failed", MINE, minutes=1, status="failed", workflow=WF_PR, artifact=ART_SITE),
        make_run("r-deps-ok", MINE, minutes=2, status="succeeded", workflow=WF_DEPS, artifact=ART_SITE),
        make_run("r-no-project", MINE, minutes=3, status="succeeded", workflow=WF_PR),
        make_run("r-api-failed", MINE, minutes=4, status="failed", artifact=ART_API),
        make_run("r-foreign", FOREIGN, minutes=1, status="failed", workflow=WF_FOREIGN, artifact=ART_FOREIGN),
    ]


class _Result:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows

    def fetchall(self) -> List[Dict[str, Any]]:
        return list(self._rows)

    def fetchone(self) -> Optional[Dict[str, Any]]:
        return self._rows[0] if self._rows else None


class FakeHistoryDB:
    """Ин-мемори ``runtime.runs``, понимающий ровно запросы ``history_page``."""

    def __init__(self, runs: List[Dict[str, Any]],
                 workflows: Optional[Dict[Any, Optional[str]]] = None,
                 artifacts: Optional[Dict[Any, Optional[str]]] = None) -> None:
        self.runs = [dict(run) for run in runs]
        self.workflows = dict(WORKFLOWS if workflows is None else workflows)
        self.artifacts = dict(ARTIFACTS if artifacts is None else artifacts)
        self.queries: List[Tuple[str, Tuple[Any, ...]]] = []

    # Фасад пула: get_pool().connection() -> контекст -> сам объект.
    def connection(self) -> "FakeHistoryDB":
        return self

    def __enter__(self) -> "FakeHistoryDB":
        return self

    def __exit__(self, *exc_info: Any) -> bool:
        return False

    def execute(self, sql: str, params: Any = ()) -> _Result:
        text = " ".join(sql.split())
        bound = tuple(params)
        self.queries.append((text, bound))
        if text.startswith("SET TRANSACTION"):
            return _Result([])
        where = text.split(" WHERE ", 1)[1].split(" ORDER BY ", 1)[0]
        conditions = [part.strip() for part in where.split(" AND ")]
        if "count(*) AS total" in text:
            return _Result([{"total": len(self._select(conditions, bound))}])
        if "AS workflow_name" in text:
            limit, offset = bound[-2], bound[-1]
            rows = self._select(conditions, bound[:-2])
            rows.sort(key=lambda row: str(row["id"]))
            rows.sort(key=lambda row: row["created_at"], reverse=True)
            return _Result([self._joined(row) for row in rows[offset:offset + limit]])
        if "r.artifact_id AS id" in text:
            return _Result(self._options(conditions, bound, "artifact_id", self.artifacts))
        if "r.workflow_id AS id" in text:
            return _Result(self._options(conditions, bound, "workflow_id", self.workflows))
        if "DISTINCT execution_status" in text:
            values = sorted({row["execution_status"] for row in self._select(conditions, bound)})
            return _Result([{"execution_status": value} for value in values])
        raise AssertionError(f"запрос вне заглушки истории: {text}")

    def _select(self, conditions: List[str], params: Tuple[Any, ...]) -> List[Dict[str, Any]]:
        rows = list(self.runs)
        index = 0
        for condition in conditions:
            if condition in ("r.workspace_id = %s", "workspace_id = %s"):
                expected = str(params[index])
                index += 1
                rows = [row for row in rows if str(row["workspace_id"]) == expected]
            elif condition == "r.artifact_id IS NULL":
                rows = [row for row in rows if row.get("artifact_id") is None]
            elif condition.endswith((">= %s", "< %s")):
                key = condition.split()[0].split(".", 1)[-1]
                bound_value = params[index]
                index += 1
                if condition.endswith(">= %s"):
                    rows = [row for row in rows if row[key] >= bound_value]
                else:
                    rows = [row for row in rows if row[key] < bound_value]
            elif condition.endswith("= %s"):
                key = condition.split()[0].split(".", 1)[-1]
                expected = params[index]
                index += 1
                rows = [row for row in rows
                        if row.get(key) is not None and str(row[key]) == str(expected)]
            else:
                raise AssertionError(f"условие вне заглушки истории: {condition}")
        assert index == len(params), f"не израсходованы параметры {params[index:]} в {conditions}"
        return rows

    def _options(self, conditions: List[str], params: Tuple[Any, ...], key: str,
                 names: Dict[Any, Optional[str]]) -> List[Dict[str, Any]]:
        # Опции всегда описывают весь workspace, а не текущую фильтрацию.
        pairs = {(row.get(key), names.get(row.get(key))) for row in self._select(conditions, params)}
        options = [{"id": item_id, "name": name} for item_id, name in pairs]
        options.sort(key=lambda item: (item["name"] is None, item["name"] or "",
                                       item["id"] is None, str(item["id"])))
        return options

    def _joined(self, row: Dict[str, Any]) -> Dict[str, Any]:
        joined = dict(row)
        joined["workflow_name"] = self.workflows.get(row.get("workflow_id"))
        joined["project_name"] = self.artifacts.get(row.get("artifact_id"))
        return joined


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(create_auth_router())
    app.include_router(create_profile_router())
    return TestClient(app)


@pytest.fixture()
def db(monkeypatch: pytest.MonkeyPatch):
    """Ставит заглушку БД в storage.profile и отдаёт фабрику сидов."""

    def install(runs: List[Dict[str, Any]]) -> FakeHistoryDB:
        fake = FakeHistoryDB(runs)
        monkeypatch.setattr(profile_storage, "get_pool", lambda: fake)
        return fake

    return install


@pytest.fixture()
def token(monkeypatch: pytest.MonkeyPatch):
    """Настоящие v3-токены: HMAC и срок проверяются штатно, строка — заглушка."""
    store: Dict[str, Dict[str, Any]] = {}

    def create_session(user: Dict[str, Any], ttl_seconds: int, role: Optional[str] = None) -> Dict[str, Any]:
        session_id = secrets.token_hex(16)
        issued = int(time.time())
        record = {
            "sessionId": session_id,
            "userId": str(user["userId"]),
            "workspaceId": str(user["workspaceId"]),
            "role": role or user.get("role") or "user",
            "issued": issued,
            "expires": issued + max(1, int(ttl_seconds)),
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

    def issue(user: Dict[str, Any], role: Optional[str] = None) -> str:
        signed, _ = session_api.issue_account_session(user, role)
        return signed

    return issue


def user_for(workspace: str) -> Dict[str, str]:
    return {
        "userId": str(uuid.uuid4()),
        "workspaceId": workspace,
        "role": "user",
        "email": "tester@example.org",
        "displayName": "Тестер",
    }


def get_history(client: TestClient, signed: str, path: str = "/auth/history", **params: Any) -> Any:
    query = f"?{urlencode(params)}" if params else ""
    return client.get(f"{path}{query}", headers={"cookie": f"cg_session={signed}"})


def test_history_requires_session(client: TestClient) -> None:
    assert client.get("/auth/history").status_code == 401


def test_filters_narrow_history(client: TestClient, db, token) -> None:
    db(base_runs())
    signed = token(user_for(MINE))

    by_project = get_history(client, signed, project=ART_SITE).json()
    assert [item["id"] for item in by_project["items"]] == ["r-pr-failed", "r-deps-ok"]
    assert by_project["total"] == 2

    without_project = get_history(client, signed, project="__none__").json()
    assert [item["id"] for item in without_project["items"]] == ["r-no-project"]
    assert without_project["total"] == 1

    by_workflow = get_history(client, signed, workflow=WF_PR).json()
    assert [item["id"] for item in by_workflow["items"]] == ["r-pr-failed", "r-no-project"]
    assert by_workflow["total"] == 2

    by_status = get_history(client, signed, status="failed").json()
    assert [item["id"] for item in by_status["items"]] == ["r-pr-failed", "r-api-failed"]
    assert by_status["total"] == 2

    combined = get_history(client, signed, status="failed", project=ART_SITE).json()
    assert [item["id"] for item in combined["items"]] == ["r-pr-failed"]
    assert combined["total"] == 1

    # Сценарий чужого workspace не находит и моих запусков.
    foreign_workflow = get_history(client, signed, workflow=WF_FOREIGN).json()
    assert foreign_workflow["items"] == []
    assert foreign_workflow["total"] == 0


def test_filter_matching_nothing(client: TestClient, db, token) -> None:
    db(base_runs())
    body = get_history(client, token(user_for(MINE)), status="cancelled").json()
    assert body["items"] == []
    assert body["total"] == 0


def test_filter_options_are_workspace_scoped(client: TestClient, db, token) -> None:
    db(base_runs())
    body = get_history(client, token(user_for(MINE))).json()
    assert {item["id"] for item in body["projects"]} == {ART_SITE, ART_API, None}
    assert {item["id"] for item in body["workflows"]} == {WF_PR, WF_DEPS, None}
    assert body["statuses"] == ["failed", "succeeded"]


def test_pagination_walks_pages(client: TestClient, db, token) -> None:
    db([make_run(f"r{index}", MINE, minutes=index) for index in range(5)])
    signed = token(user_for(MINE))

    first = get_history(client, signed, limit=2, offset=0).json()
    assert [item["id"] for item in first["items"]] == ["r0", "r1"]
    assert (first["total"], first["limit"], first["offset"]) == (5, 2, 0)

    middle = get_history(client, signed, limit=2, offset=2).json()
    assert [item["id"] for item in middle["items"]] == ["r2", "r3"]
    assert middle["total"] == 5

    last = get_history(client, signed, limit=2, offset=4).json()
    assert [item["id"] for item in last["items"]] == ["r4"]
    assert last["total"] == 5

    beyond = get_history(client, signed, limit=2, offset=10).json()
    assert beyond["items"] == []
    assert beyond["total"] == 5


def test_pagination_bounds_are_rejected(client: TestClient, db, token) -> None:
    db([make_run("r0", MINE)])
    signed = token(user_for(MINE))
    assert get_history(client, signed, limit=0).status_code == 422
    assert get_history(client, signed, limit=101).status_code == 422
    assert get_history(client, signed, offset=-1).status_code == 422


def test_foreign_workspace_is_unreachable(client: TestClient, db, token) -> None:
    fake = db(base_runs())

    # Чужая сессия подставляет в запросы только свой workspace,
    # даже если клиент передаёт workspaceId и проект первого.
    foreign_signed = token(user_for(FOREIGN))
    foreign = get_history(client, foreign_signed, workspaceId=MINE, project=ART_SITE).json()
    assert foreign["items"] == []
    assert foreign["total"] == 0
    assert {item["id"] for item in get_history(client, foreign_signed).json()["items"]} == {"r-foreign"}
    assert {item["id"] for item in get_history(client, foreign_signed, path="/profile/history").json()["items"]} == {"r-foreign"}
    assert all(str(params[0]) == FOREIGN for _, params in fake.queries if params)

    # Своя сессия с чужим workspaceId в запросе всё равно видит только своё.
    mine_signed = token(user_for(MINE))
    mine = get_history(client, mine_signed, workspaceId=FOREIGN).json()
    assert {item["id"] for item in mine["items"]} == {
        "r-pr-failed", "r-deps-ok", "r-no-project", "r-api-failed",
    }
    assert all("workspace_id" not in item for item in mine["items"])


def test_empty_selection(client: TestClient, db, token) -> None:
    db([make_run("r-foreign", FOREIGN)])
    signed = token(user_for(MINE))

    empty = get_history(client, signed).json()
    assert empty["items"] == []
    assert empty["total"] == 0
    assert empty["projects"] == []
    assert empty["workflows"] == []
    assert empty["statuses"] == []

    filtered = get_history(client, signed, status="failed", project=ART_SITE).json()
    assert filtered["items"] == []
    assert filtered["total"] == 0


def test_profile_history_filters_and_paginates(client: TestClient, db, token) -> None:
    db(base_runs())
    signed = token(user_for(MINE))

    page = get_history(client, signed, path="/profile/history",
                       limit=2, offset=1, status="succeeded").json()
    assert [item["id"] for item in page["items"]] == ["r-no-project"]
    assert page["total"] == 2

    nothing = get_history(client, token(user_for(FOREIGN)),
                          path="/profile/history", status="succeeded").json()
    assert nothing["items"] == []
    assert nothing["total"] == 0


def test_profile_history_date_window(client: TestClient, db, token) -> None:
    db(base_runs())
    signed = token(user_for(MINE))

    window = get_history(client, signed, path="/profile/history",
                         since="2026-11-22T11:59:00Z", until="2026-11-22T12:00:00Z").json()
    assert [item["id"] for item in window["items"]] == ["r-pr-failed"]
    assert window["total"] == 1

    naive = get_history(client, signed, path="/profile/history", since="2026-11-22T11:59:00")
    assert naive.status_code == 422
