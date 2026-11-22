"""``POST /api/webhook-test`` закрыт admin-гардом (A-2 аудита).

Приёмник пишет в общий in-memory лог вебхуков, который читает только admin
(``GET /api/webhook-test/logs``), поэтому и запись туда — admin-действие:
admin-сессия проходит, обычный аккаунт получает 403, без сессии — 401.
Негативные ответы не оставляют записи в логе.

Импорт ``backend.app.api.router`` строит ``DatabaseRepository``, которому нужен
``DATABASE_URL``; сами тесты к БД не обращаются, поэтому ``init_db`` глушится
до импорта.

Run: PYTHONPATH=. /tmp/nedra-venv/bin/python -m pytest backend/tests/test_webhook_test_guard.py -q
"""
from __future__ import annotations

import secrets
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import session as session_api
from backend.app.storage import accounts, database as database_storage

WORKSPACE = str(uuid.uuid4())


@pytest.fixture()
def api(monkeypatch: pytest.MonkeyPatch):
    """Реальный ``api_router``: ``init_db`` не зовётся, БД не нужна."""
    monkeypatch.setattr(database_storage, "init_db", lambda *args, **kwargs: None)
    from backend.app.api import router as api_router_module

    api_router_module.received_webhooks.clear()
    return api_router_module


@pytest.fixture()
def client(api) -> TestClient:
    app = FastAPI()
    app.include_router(api.api_router)
    return TestClient(app)


@pytest.fixture()
def issue(monkeypatch: pytest.MonkeyPatch):
    """Настоящие v3-токены: HMAC и срок проверяются штатно, store — заглушка."""
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


def post_webhook(client: TestClient, token: Optional[str] = None) -> Any:
    headers = {"cookie": f"cg_session={token}"} if token else {}
    return client.post("/api/webhook-test", json={"runId": "run-1"}, headers=headers)


def test_admin_can_write_test_webhook(client: TestClient, issue, api) -> None:
    token = issue(role="admin")

    response = post_webhook(client, token)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert len(api.received_webhooks) == 1

    logs = client.get("/api/webhook-test/logs", headers={"cookie": f"cg_session={token}"})
    assert logs.status_code == 200
    assert len(logs.json()) == 1


def test_regular_account_cannot_write_test_webhook(client: TestClient, issue, api) -> None:
    response = post_webhook(client, issue(role="user"))

    assert response.status_code == 403
    assert response.json()["detail"] == "Требуется административный доступ"
    assert api.received_webhooks == []


def test_missing_session_is_unauthorized(client: TestClient, api) -> None:
    response = post_webhook(client)

    assert response.status_code == 401
    assert response.json()["detail"] == "Требуется вход"
    assert api.received_webhooks == []
