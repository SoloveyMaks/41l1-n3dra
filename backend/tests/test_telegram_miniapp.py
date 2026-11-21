"""Tests for Telegram Mini App auth and HTTP surface.

Pure crypto tests always run. The integration class runs only when
``NEDRA_TEST_DATABASE_URL`` points at a disposable PostgreSQL with all
migrations; it never touches the network and never calls a real bot.

Run:

    NEDRA_TEST_DATABASE_URL='postgresql://...' \
    TELEGRAM_BOT_TOKEN='123456789:AAfakefakefakefakefake123' \
        /tmp/nedra-venv/bin/python -m unittest backend.tests.test_telegram_miniapp -v
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib
import json
import os
import tempfile
import time
import unittest
import urllib.parse
import uuid
from typing import Any, Dict, Optional

from backend.app.telegram.miniapp_auth import (
    InitDataError,
    MiniAppAuth,
    MiniConfigError,
    MiniIdentity,
    MiniTokenError,
    MiniTokenRevoked,
    configured_token_secret,
    init_data_secret,
    mint_mini_token,
    parse_init_data,
    verify_init_data,
    verify_mini_token,
)

FAKE_BOT_TOKEN = "123456789:AAfakefakefakefakefake123"
FAKE_SECRET = b"0123456789abcdef0123456789abcdef"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def sign_init_data(
    user_id: int,
    *,
    bot_token: str = FAKE_BOT_TOKEN,
    auth_date: Optional[int] = None,
    first_name: str = "Иван",
    chat_type: Optional[str] = None,
    extra: Optional[Dict[str, str]] = None,
) -> str:
    values = {
        "auth_date": str(int(time.time()) if auth_date is None else auth_date),
        "query_id": "AAHtest-query",
        "user": json.dumps(
            {"id": user_id, "first_name": first_name, "username": "ivan"},
            ensure_ascii=False,
        ),
    }
    if chat_type is not None:
        values["chat_type"] = chat_type
    values.update(extra or {})
    check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    digest = hmac.new(
        init_data_secret(bot_token), check.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return urllib.parse.urlencode({**values, "hash": digest})


def _identity(*, epoch: int = 1) -> MiniIdentity:
    return MiniIdentity(
        chat_id=777001,
        user_id=str(uuid.uuid4()),
        workspace_id=str(uuid.uuid4()),
        binding_epoch=epoch,
        display_name="Тест",
    )


class InitDataTests(unittest.TestCase):
    def test_valid_signature_and_user(self) -> None:
        raw = sign_init_data(777001)
        user = verify_init_data(raw, FAKE_BOT_TOKEN)
        self.assertEqual(user.id, 777001)
        self.assertEqual(user.first_name, "Иван")

    def test_tampered_user_is_rejected(self) -> None:
        raw = sign_init_data(777001)
        tampered = raw.replace("ivan", "evil")
        self.assertNotEqual(raw, tampered)
        with self.assertRaises(InitDataError):
            verify_init_data(tampered, FAKE_BOT_TOKEN)

    def test_duplicate_keys_are_rejected(self) -> None:
        raw = sign_init_data(777001) + "&auth_date=1"
        with self.assertRaises(InitDataError):
            verify_init_data(raw, FAKE_BOT_TOKEN)

    def test_expired_and_future_auth_date(self) -> None:
        now = int(time.time())
        with self.assertRaises(InitDataError):
            verify_init_data(
                sign_init_data(1, auth_date=now - 7200),
                FAKE_BOT_TOKEN,
                now=now,
                max_age=3600,
            )
        with self.assertRaises(InitDataError):
            verify_init_data(
                sign_init_data(1, auth_date=now + 300),
                FAKE_BOT_TOKEN,
                now=now,
            )

    def test_wrong_bot_token_and_non_private_chat(self) -> None:
        with self.assertRaises(InitDataError):
            verify_init_data(
                sign_init_data(1, bot_token="987654321:AAotherfakefakefakefake12"),
                FAKE_BOT_TOKEN,
            )
        with self.assertRaises(InitDataError):
            verify_init_data(sign_init_data(1, chat_type="group"), FAKE_BOT_TOKEN)

    def test_malformed_hash_and_size_cap(self) -> None:
        with self.assertRaises(InitDataError):
            verify_init_data("auth_date=1&user=%7B%22id%22%3A1%7D&hash=zz", FAKE_BOT_TOKEN)
        with self.assertRaises(InitDataError):
            parse_init_data("a=" + "x" * 9000)

    def test_parse_rejects_non_fields(self) -> None:
        with self.assertRaises(InitDataError):
            parse_init_data("auth_date")


def _sign_claims(claims: Dict[str, Any], secret: bytes = FAKE_SECRET) -> str:
    body = _b64(json.dumps(claims, separators=(",", ":")).encode())
    signed = f"v1.{body}"
    sig = _b64(hmac.new(secret, signed.encode(), hashlib.sha256).digest())
    return f"{signed}.{sig}"


class TokenTests(unittest.TestCase):
    def test_roundtrip(self) -> None:
        identity = _identity(epoch=4)
        token, expires = mint_mini_token(identity, FAKE_SECRET, now=1_700_000_000)
        claims = verify_mini_token(token, FAKE_SECRET, now=1_700_000_001)
        self.assertEqual(claims.chat_id, identity.chat_id)
        self.assertEqual(claims.workspace_id, identity.workspace_id)
        self.assertEqual(claims.binding_epoch, 4)
        self.assertEqual(claims.expires_at, expires)

    def test_tamper_expiry_and_wrong_secret(self) -> None:
        token, _ = mint_mini_token(_identity(), FAKE_SECRET, now=1_700_000_000)
        head, payload, signature = token.split(".")
        with self.assertRaises(MiniTokenError):
            verify_mini_token(f"{head}.{payload}.{signature[:-2]}AA", FAKE_SECRET, now=1_700_000_001)
        with self.assertRaises(MiniTokenError):
            verify_mini_token(token, b"different-secret-0123456789", now=1_700_000_001)
        with self.assertRaises(MiniTokenError):
            verify_mini_token(token, FAKE_SECRET, now=1_700_000_000 + 901)

    def test_admin_role_and_overlong_ttl_are_rejected(self) -> None:
        now = 1_700_000_000
        base = {
            "v": 1, "cid": 1, "uid": str(uuid.uuid4()), "wid": str(uuid.uuid4()),
            "ep": 1, "iat": now, "exp": now + 900,
        }
        with self.assertRaises(MiniTokenError):
            verify_mini_token(_sign_claims({**base, "role": "admin"}), FAKE_SECRET, now=now)
        with self.assertRaises(MiniTokenError):
            verify_mini_token(_sign_claims({**base, "role": "user", "exp": now + 901}), FAKE_SECRET, now=now)

    def test_short_secret_config_is_refused(self) -> None:
        with self.assertRaises(MiniConfigError):
            mint_mini_token(_identity(), b"short", now=1_700_000_000)


TEST_DSN = os.getenv("NEDRA_TEST_DATABASE_URL", "").strip()


@unittest.skipUnless(TEST_DSN, "NEDRA_TEST_DATABASE_URL is not set")
class MiniAppHttpTests(unittest.IsolatedAsyncioTestCase):
    """Real PostgreSQL + ASGI. Uses synthetic initData and a fake bot token."""

    app: Any = None
    db: Any = None
    pool: Any = None

    @classmethod
    def setUpClass(cls) -> None:
        os.environ["DATABASE_URL"] = TEST_DSN
        os.environ["TELEGRAM_BOT_TOKEN"] = FAKE_BOT_TOKEN
        os.environ["TELEGRAM_MINIAPP_TOKEN_SECRET"] = FAKE_SECRET.decode()
        os.environ["TELEGRAM_ENABLED"] = "0"
        os.environ["CG_SESSION_SECRET"] = "miniapp-test-session-secret"
        os.environ["CG_DATA_DIR"] = tempfile.mkdtemp(prefix="nedra-miniapp-test-")
        os.environ.pop("CG_PUBLIC_DEMO", None)
        os.environ.pop("CG_ALLOWED_ORIGINS", None)
        module = importlib.import_module("backend.app.main")
        cls.main = module
        cls.app = module.app
        cls.db = module.db_repo
        from backend.app.storage.database import get_pool
        cls.pool = get_pool()

    def _provision(
        self,
        *,
        bind: bool = True,
        scenario: bool = True,
        scenario_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        from backend.app.codefacts.identity import workspace_scope
        from backend.app.demo.default_workflow import get_default_workflow_draft

        user_id = uuid.uuid4()
        workspace_id = uuid.uuid4()
        chat_id = 700_000_000 + int(uuid.uuid4().int % 100_000_000)
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO data_contract.workspaces (workspace_id, name) VALUES (%s, %s)",
                (workspace_id, f"test:{workspace_id}"),
            )
            conn.execute(
                "INSERT INTO account.users "
                "(user_id, email, password_hash, display_name, role, workspace_id) "
                "VALUES (%s, %s, 'scrypt$test', %s, 'user', %s)",
                (user_id, f"{user_id}@test.local", "Тестовый Пользователь", workspace_id),
            )
            if bind:
                conn.execute(
                    "INSERT INTO telegram.bindings "
                    "(chat_id, user_id, workspace_id, telegram_username, binding_epoch) "
                    "VALUES (%s, %s, %s, 'tester', 1)",
                    (chat_id, user_id, workspace_id),
                )
                conn.execute(
                    "INSERT INTO telegram.settings (chat_id, delivery_mode, scenario) "
                    "VALUES (%s, 'both', '')",
                    (chat_id,),
                )
        workflow_id = scenario_id
        if scenario and workflow_id is None:
            workflow_id = f"wf_mini_{uuid.uuid4().hex[:8]}"
        if workflow_id is not None:
            draft = get_default_workflow_draft()
            with workspace_scope(str(workspace_id)):
                self.db.save_workflow(
                    workflow_id, f"Сценарий {workflow_id}", "", draft.model_dump()
                )
            with self.pool.connection() as conn:
                conn.execute(
                    "UPDATE telegram.settings SET scenario = %s WHERE chat_id = %s",
                    (workflow_id, chat_id),
                )
        return {
            "user_id": str(user_id),
            "workspace_id": str(workspace_id),
            "chat_id": chat_id,
            "workflow_id": workflow_id,
        }

    async def _client(self):
        import httpx
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )

    async def _token(self, client, chat_id: int) -> str:
        response = await client.post(
            "/api/telegram/mini/auth",
            json={"initData": sign_init_data(chat_id)},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["token"]

    async def test_no_cookie_session_and_live_bootstrap(self) -> None:
        ctx = self._provision()
        async with await self._client() as client:
            response = await client.post(
                "/api/telegram/mini/auth",
                json={"initData": sign_init_data(ctx["chat_id"])},
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn("set-cookie", response.headers)
            self.assertEqual(response.headers.get("cache-control"), "no-store")
            payload = response.json()
            self.assertGreater(payload["expiresAt"], int(time.time()))
            self.assertEqual(set(payload), {"token", "expiresAt"})
            token = payload["token"]

            response = await client.get(
                "/api/telegram/mini/bootstrap",
                headers={"Authorization": f"Bearer {token}"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual(body["user"]["displayName"], "Тестовый Пользователь")
            self.assertEqual(body["settings"]["deliveryMode"], "both")
            self.assertEqual(body["settings"]["scenario"], ctx["workflow_id"])
            self.assertEqual([s["id"] for s in body["scenarios"]], [ctx["workflow_id"]])
            self.assertEqual(body["runs"], [])
            self.assertNotIn("workspace_id", response.text)
            self.assertNotIn("chat_id", response.text)
            self.assertNotIn("test.local", response.text)

            response = await client.get(
                "/api/telegram/mini/bootstrap",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Origin": "https://evil.example",
                },
            )
            self.assertEqual(response.status_code, 403, response.text)

            response = await client.get("/api/telegram/mini/bootstrap")
            self.assertEqual(response.status_code, 401, response.text)
            self.assertEqual(response.json()["detail"]["code"], "AUTH_REQUIRED")

    async def test_unbound_is_409_but_bad_signature_is_401(self) -> None:
        ctx = self._provision(bind=False)
        async with await self._client() as client:
            response = await client.post(
                "/api/telegram/mini/auth",
                json={"initData": sign_init_data(ctx["chat_id"])},
            )
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["detail"]["code"], "TELEGRAM_NOT_BOUND")

            good = sign_init_data(ctx["chat_id"])
            response = await client.post(
                "/api/telegram/mini/auth",
                json={"initData": good.replace("ivan", "evil")},
            )
            self.assertEqual(response.status_code, 401, response.text)
            self.assertEqual(response.json()["detail"]["code"], "INVALID_INIT_DATA")

    async def test_rebind_and_unbind_revoke_token(self) -> None:
        ctx = self._provision()
        async with await self._client() as client:
            token = await self._token(client, ctx["chat_id"])
            headers = {"Authorization": f"Bearer {token}"}
            with self.pool.connection() as conn:
                conn.execute(
                    "UPDATE telegram.bindings SET binding_epoch = binding_epoch + 1 "
                    "WHERE chat_id = %s",
                    (ctx["chat_id"],),
                )
            response = await client.get("/api/telegram/mini/bootstrap", headers=headers)
            self.assertEqual(response.status_code, 401, response.text)
            self.assertEqual(response.json()["detail"]["code"], "TOKEN_REVOKED")

            ctx2 = self._provision()
            token2 = await self._token(client, ctx2["chat_id"])
            with self.pool.connection() as conn:
                conn.execute("DELETE FROM telegram.bindings WHERE chat_id = %s", (ctx2["chat_id"],))
            response = await client.get(
                "/api/telegram/mini/bootstrap",
                headers={"Authorization": f"Bearer {token2}"},
            )
            self.assertEqual(response.status_code, 401, response.text)
            self.assertEqual(response.json()["detail"]["code"], "INVALID_TOKEN")

    async def test_settings_ownership_and_foreign_scenario(self) -> None:
        ctx = self._provision()
        foreign = self._provision(scenario=True)
        async with await self._client() as client:
            token = await self._token(client, ctx["chat_id"])
            headers = {"Authorization": f"Bearer {token}"}
            response = await client.put(
                "/api/telegram/mini/settings",
                headers=headers,
                json={"deliveryMode": "alerts", "scenario": ctx["workflow_id"]},
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {"deliveryMode": "alerts", "scenario": ctx["workflow_id"]})

            response = await client.put(
                "/api/telegram/mini/settings",
                headers=headers,
                json={"scenario": foreign["workflow_id"]},
            )
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(response.json()["detail"]["code"], "SCENARIO_NOT_FOUND")

            response = await client.put(
                "/api/telegram/mini/settings", headers=headers, json={"scenario": None}
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIsNone(response.json()["scenario"])

    async def test_run_detail_masks_and_hides_foreign_workspace(self) -> None:
        from backend.app.codefacts.identity import workspace_scope

        ctx = self._provision()
        run_id = f"run_mini_{uuid.uuid4().hex[:8]}"
        foreign_run = f"run_foreign_{uuid.uuid4().hex[:8]}"
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO runtime.runs "
                "(workspace_id, id, workflow_id, revision_id, artifact_id, trigger_type, "
                " execution_status, security_verdict, total_findings, critical_count, "
                " high_count, medium_count, low_count, info_count, created_at) "
                "VALUES (%s, %s, %s, 'rev_x', NULL, 'miniapp', 'succeeded', 'fail', 1, 0, 1, 0, 0, 0, now())",
                (uuid.UUID(ctx["workspace_id"]), run_id, ctx["workflow_id"]),
            )
        with workspace_scope(ctx["workspace_id"]):
            self.db.save_findings(
                run_id,
                [{
                    "id": "fnd_1",
                    "fingerprint": "fp_1",
                    "analyzer": "secret_scan",
                    "ruleId": "SEC001",
                    "ruleVersion": "1.0.0",
                    "severity": "high",
                    "severityBasis": "rule",
                    "confidence": "high",
                    "confidenceBasis": "deterministic",
                    "status": "confirmed",
                    "primaryLocation": {"path": "src/A.kt", "startLine": 1, "endLine": 1,
                                        "startColumn": 1, "endColumn": 5, "byteRange": [0, 4]},
                    "relatedLocations": [],
                    "trace": [],
                    "redactedEvidence": 'password = "hunter2"',
                    "advisoryIds": [],
                    "coverageWarnings": [],
                }],
            )
        foreign_workspace = uuid.uuid4()
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO data_contract.workspaces (workspace_id, name) VALUES (%s, 'foreign')",
                (foreign_workspace,),
            )
            conn.execute(
                "INSERT INTO runtime.runs "
                "(workspace_id, id, workflow_id, revision_id, artifact_id, trigger_type, "
                " execution_status, security_verdict, created_at) "
                "VALUES (%s, %s, %s, 'rev_x', NULL, 'manual', 'succeeded', 'pass', now())",
                (foreign_workspace, foreign_run, ctx["workflow_id"]),
            )
        async with await self._client() as client:
            token = await self._token(client, ctx["chat_id"])
            headers = {"Authorization": f"Bearer {token}"}
            response = await client.get(f"/api/telegram/mini/runs/{run_id}", headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual(body["run"]["id"], run_id)
            self.assertEqual(body["run"]["execution_status"], "succeeded")
            self.assertEqual(body["run"]["security_verdict"], "fail")
            self.assertEqual(body["run"]["high_findings"], 1)
            self.assertEqual(len(body["findings"]), 1)
            self.assertNotIn("hunter2", response.text)
            self.assertNotIn("workspace_id", response.text)

            response = await client.get(
                f"/api/telegram/mini/runs/{foreign_run}", headers=headers
            )
            self.assertEqual(response.status_code, 404, response.text)

    async def test_report_requires_saved_report_and_returns_saved_status(self) -> None:
        ctx = self._provision()
        run_id = f"run_report_{uuid.uuid4().hex[:8]}"
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO runtime.runs "
                "(workspace_id, id, workflow_id, revision_id, artifact_id, trigger_type, "
                " execution_status, security_verdict, created_at) "
                "VALUES (%s, %s, %s, 'rev_x', NULL, 'miniapp', 'queued', 'pending', now())",
                (uuid.UUID(ctx["workspace_id"]), run_id, ctx["workflow_id"]),
            )
        async with await self._client() as client:
            token = await self._token(client, ctx["chat_id"])
            headers = {"Authorization": f"Bearer {token}"}
            response = await client.get(
                f"/api/telegram/mini/runs/{run_id}/report.jsonc", headers=headers
            )
            self.assertEqual(response.status_code, 404, response.text)
            self.assertEqual(response.json()["detail"]["code"], "REPORT_NOT_FOUND")

            with self.pool.connection() as conn:
                conn.execute(
                    "INSERT INTO runtime.reports "
                    "(workspace_id, run_id, verdict, passport_json, ai_summary, ai_items_json, "
                    " ai_status, readable_markdown, official_json, created_at) "
                    "VALUES (%s, %s, 'pending', '{}'::jsonb, '', '[]'::jsonb, "
                    "'disabled', '', '{}', now())",
                    (uuid.UUID(ctx["workspace_id"]), run_id),
                )
            response = await client.get(
                f"/api/telegram/mini/runs/{run_id}/report.jsonc", headers=headers
            )
            self.assertEqual(response.status_code, 200, response.text)
            document = json.loads(response.text)
            self.assertEqual(document["run"]["id"], run_id)
            self.assertEqual(document["run"]["executionStatus"], "queued")
            self.assertEqual(document["run"]["securityVerdict"], "pending")

    async def test_runs_requires_scenario_and_https_without_import(self) -> None:
        ctx = self._provision(scenario=False)
        async with await self._client() as client:
            token = await self._token(client, ctx["chat_id"])
            headers = {"Authorization": f"Bearer {token}"}
            response = await client.post(
                "/api/telegram/mini/runs",
                headers=headers,
                json={"repositoryUrl": "https://github.com/example/repo"},
            )
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["detail"]["code"], "SCENARIO_REQUIRED")

            response = await client.post(
                "/api/telegram/mini/runs",
                headers=headers,
                json={"repositoryUrl": "http://github.com/example/repo",
                      "scenarioId": "wf_any"},
            )
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(response.json()["detail"]["code"], "INVALID_REPOSITORY_URL")


@unittest.skipUnless(TEST_DSN, "NEDRA_TEST_DATABASE_URL is not set")
class RunStartTests(unittest.IsolatedAsyncioTestCase):
    """POST /runs re-checks the binding after the network import."""

    @classmethod
    def setUpClass(cls) -> None:
        os.environ["DATABASE_URL"] = TEST_DSN
        os.environ["TELEGRAM_BOT_TOKEN"] = FAKE_BOT_TOKEN
        os.environ["TELEGRAM_MINIAPP_TOKEN_SECRET"] = FAKE_SECRET.decode()
        os.environ["TELEGRAM_ENABLED"] = "0"
        os.environ.setdefault("CG_SESSION_SECRET", "miniapp-test-session-secret")
        os.environ.setdefault("CG_DATA_DIR", tempfile.mkdtemp(prefix="nedra-miniapp-test-"))
        from backend.app.storage.database import DatabaseRepository, get_pool
        from backend.app.storage.telegram import TelegramRepository
        from backend.app.telegram.miniapp import create_miniapp_router

        cls.pool = get_pool()
        cls.db = DatabaseRepository()
        cls.telegram_repo = TelegramRepository()
        cls.create_miniapp_router = staticmethod(create_miniapp_router)

    def _provision(self) -> Dict[str, Any]:
        from backend.app.codefacts.identity import workspace_scope
        from backend.app.demo.default_workflow import get_default_workflow_draft

        user_id = uuid.uuid4()
        workspace_id = uuid.uuid4()
        chat_id = 600_000_000 + int(uuid.uuid4().int % 100_000_000)
        workflow_id = f"wf_run_{uuid.uuid4().hex[:8]}"
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO data_contract.workspaces (workspace_id, name) VALUES (%s, %s)",
                (workspace_id, f"test:{workspace_id}"),
            )
            conn.execute(
                "INSERT INTO account.users "
                "(user_id, email, password_hash, display_name, role, workspace_id) "
                "VALUES (%s, %s, 'scrypt$test', 'Run Tester', 'user', %s)",
                (user_id, f"{user_id}@test.local", workspace_id),
            )
            conn.execute(
                "INSERT INTO telegram.bindings "
                "(chat_id, user_id, workspace_id, telegram_username, binding_epoch) "
                "VALUES (%s, %s, %s, 'tester', 1)",
                (chat_id, user_id, workspace_id),
            )
            conn.execute(
                "INSERT INTO telegram.settings (chat_id, delivery_mode, scenario) "
                "VALUES (%s, 'both', %s)",
                (chat_id, workflow_id),
            )
        draft = get_default_workflow_draft()
        with workspace_scope(str(workspace_id)):
            self.db.save_workflow(
                workflow_id, f"Run scenario {workflow_id}", "", draft.model_dump()
            )
        return {
            "user_id": str(user_id),
            "workspace_id": str(workspace_id),
            "chat_id": chat_id,
            "workflow_id": workflow_id,
        }

    def _app(self, importer, executor):
        from fastapi import FastAPI
        from backend.app.telegram.miniapp_auth import MiniAppAuth

        auth = MiniAppAuth(
            self.telegram_repo,
            bot_token_provider=lambda: FAKE_BOT_TOKEN,
            token_secret=FAKE_SECRET,
        )
        app = FastAPI()
        app.include_router(
            self.create_miniapp_router(
                self.db, executor, importer, repository=self.telegram_repo, auth=auth
            )
        )
        return app

    async def _token(self, client, chat_id: int) -> str:
        response = await client.post(
            "/api/telegram/mini/auth", json={"initData": sign_init_data(chat_id)}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["token"]

    async def test_rebind_during_import_prevents_run(self) -> None:
        import httpx

        ctx = self._provision()
        calls = {"import": 0, "execute": 0}

        async def importer(url: str) -> Dict[str, Any]:
            calls["import"] += 1
            with self.pool.connection() as conn:
                conn.execute(
                    "DELETE FROM telegram.bindings WHERE chat_id = %s", (ctx["chat_id"],)
                )
            return {"id": "art_never_saved"}

        class Executor:
            async def execute_run(self, **kwargs: Any) -> None:
                calls["execute"] += 1

        app = self._app(importer, Executor())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            token = await self._token(client, ctx["chat_id"])
            response = await client.post(
                "/api/telegram/mini/runs",
                headers={"Authorization": f"Bearer {token}"},
                json={"repositoryUrl": "https://github.com/example/repo"},
            )
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.json()["detail"]["code"], "INVALID_TOKEN")
        self.assertEqual(calls, {"import": 1, "execute": 0})
        with self.pool.connection() as conn:
            count = conn.execute(
                "SELECT count(*) AS n FROM runtime.runs WHERE workspace_id = %s",
                (uuid.UUID(ctx["workspace_id"]),),
            ).fetchone()["n"]
        self.assertEqual(count, 0)

    async def test_successful_start_persists_artifact_and_calls_executor(self) -> None:
        import asyncio
        import httpx

        ctx = self._provision()
        manifest = {
            "id": f"art_mini_{uuid.uuid4().hex[:8]}",
            "filename": "repo.zip",
            "hash_sha256": "a" * 64,
            "size_bytes": 1,
            "uncompressed_size_bytes": 1,
            "file_count": 1,
            "kotlin_file_count": 0,
            "gradle_file_count": 0,
            "extracted_path": "/tmp/never-used",
            "created_at": "2026-01-01T00:00:00+00:00",
        }

        async def importer(url: str) -> Dict[str, Any]:
            return dict(manifest)

        class Executor:
            def __init__(self) -> None:
                self.calls = []
                self.done = asyncio.Event()

            async def execute_run(self, **kwargs: Any) -> None:
                self.calls.append(kwargs)
                self.done.set()

        executor = Executor()
        app = self._app(importer, executor)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            token = await self._token(client, ctx["chat_id"])
            response = await client.post(
                "/api/telegram/mini/runs",
                headers={"Authorization": f"Bearer {token}"},
                json={"repositoryUrl": "https://github.com/example/repo",
                      "scenarioId": ctx["workflow_id"]},
            )
        self.assertEqual(response.status_code, 202, response.text)
        run_id = response.json()["runId"]
        await asyncio.wait_for(executor.done.wait(), timeout=3)
        self.assertEqual(executor.calls[0]["run_id"], run_id)
        self.assertEqual(executor.calls[0]["trigger_type"], "miniapp")
        self.assertEqual(executor.calls[0]["workflow_id"], ctx["workflow_id"])
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT trigger_type, execution_status, artifact_id FROM runtime.runs "
                "WHERE workspace_id = %s AND id = %s",
                (uuid.UUID(ctx["workspace_id"]), run_id),
            ).fetchone()
            artifact = conn.execute(
                "SELECT id FROM runtime.artifacts WHERE workspace_id = %s AND id = %s",
                (uuid.UUID(ctx["workspace_id"]), manifest["id"]),
            ).fetchone()
        self.assertEqual(row, {"trigger_type": "miniapp", "execution_status": "queued",
                               "artifact_id": manifest["id"]})
        self.assertIsNotNone(artifact)


if __name__ == "__main__":
    unittest.main()
