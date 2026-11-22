"""Secrets at rest: AES-GCM конверт, режимы, отказы и проводка в хранилище.

Без сети и без PostgreSQL: запись/чтение ревизий идёт через фиктивный pool,
который запоминает ровно тот JSONb, что ушёл бы в ``draft_json``.

Run: /tmp/nedra-venv/bin/python -m pytest backend/tests -q
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.contracts.schemas import WorkflowDraft, WorkflowNode
from backend.app.security.secrets import (
    KEY_ENV,
    MODE_ENV,
    MODE_OPTIONAL,
    MODE_REQUIRED,
    MissingSecretsKeyError,
    ReservedEnvelopeError,
    SecretDecryptionError,
    SecretsConfigError,
    decrypt_secrets,
    encrypt_secrets,
    is_encrypted,
    load_key,
    secrets_mode,
)

# Фиктивные значения: только для тестов, секретами не являются.
KEY_A = base64.b64encode(b"A" * 32).decode("ascii")
KEY_B = base64.b64encode(b"B" * 32).decode("ascii")
TOKEN = "sk-test-token-0123456789abcdef"
WEBHOOK = "whsec_test-abcdef0123456789"
SNAKE = "tok_nested_9876543210"


def sample_draft() -> dict:
    """Вложенный черновик: секреты в dict, list и на разной глубине."""
    return {
        "name": "PR gate",
        "description": "",
        "nodes": [
            {
                "id": "n1",
                "kind": "webhook",
                "config": {
                    "webhookUrl": "https://hooks.example/test",
                    "secretToken": TOKEN,
                    "webhookSecret": WEBHOOK,
                },
            },
            {
                "id": "n2",
                "kind": "ai_scan",
                "config": {"apiKey": "plain-keep-me", "secret_token": None},
            },
        ],
        "edges": [{"from": "n1", "to": "n2"}],
        "meta": {"groups": [{"name": "g", "config": {"secret_token": SNAKE}}]},
    }


@contextlib.contextmanager
def secret_env(*, key: str | None = None, mode: str | None = None):
    """Временно выставить/снять NEDRA_SECRET_KEY и NEDRA_SECRETS_MODE."""
    names = (KEY_ENV, MODE_ENV)
    saved = {name: os.environ.get(name) for name in names}
    try:
        for name in names:
            os.environ.pop(name, None)
        if key is not None:
            os.environ[KEY_ENV] = key
        if mode is not None:
            os.environ[MODE_ENV] = mode
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class EnvelopeTests(unittest.TestCase):
    def test_round_trip_nested_draft(self):
        draft = sample_draft()
        with secret_env(key=KEY_A):
            sealed = encrypt_secrets(draft)
            self.assertNotIn(TOKEN, json.dumps(sealed, ensure_ascii=False))
            self.assertNotIn(WEBHOOK, json.dumps(sealed, ensure_ascii=False))
            self.assertNotIn(SNAKE, json.dumps(sealed, ensure_ascii=False))
            config = sealed["nodes"][0]["config"]
            self.assertTrue(is_encrypted(config["secretToken"]))
            self.assertTrue(config["secretToken"].startswith("enc:v1:"))
            # Несекретные поля не меняются.
            self.assertEqual(sealed["name"], draft["name"])
            self.assertEqual(sealed["nodes"][1]["config"], draft["nodes"][1]["config"])
            self.assertEqual(sealed["edges"], draft["edges"])
            self.assertEqual(decrypt_secrets(sealed), draft)

    def test_nonce_is_fresh_per_value(self):
        with secret_env(key=KEY_A):
            first = encrypt_secrets({"secretToken": TOKEN})
            second = encrypt_secrets({"secretToken": TOKEN})
            self.assertNotEqual(first["secretToken"], second["secretToken"])
            self.assertEqual(
                decrypt_secrets(first)["secretToken"],
                decrypt_secrets(second)["secretToken"],
            )

    def test_legacy_plaintext_reads_without_loss(self):
        draft = sample_draft()
        # Без ключа: и запись, и чтение работают как раньше.
        self.assertEqual(decrypt_secrets(draft), draft)
        with secret_env(key=KEY_A):
            self.assertEqual(decrypt_secrets(draft), draft)

    def test_non_string_secret_and_non_secret_untouched(self):
        draft = sample_draft()
        with secret_env(key=KEY_A):
            sealed = encrypt_secrets(draft)
        self.assertIsNone(sealed["nodes"][1]["config"]["secret_token"])
        self.assertEqual(sealed["nodes"][1]["config"]["apiKey"], "plain-keep-me")

    def test_reencrypt_is_idempotent(self):
        with secret_env(key=KEY_A):
            once = encrypt_secrets({"secretToken": TOKEN})
            twice = encrypt_secrets(once)
        self.assertEqual(once, twice)

    def test_wrong_key_is_explicit_error_not_plaintext(self):
        draft = sample_draft()
        with secret_env(key=KEY_A):
            sealed = encrypt_secrets(draft)
        with secret_env(key=KEY_B):
            with self.assertRaises(SecretDecryptionError) as ctx:
                decrypt_secrets(sealed)
        message = str(ctx.exception)
        self.assertNotIn(TOKEN, message)
        self.assertIn(KEY_ENV, message)

    def test_tampered_ciphertext_is_explicit_error(self):
        with secret_env(key=KEY_A):
            sealed = encrypt_secrets({"secretToken": TOKEN})
        _, _, nonce, sealed_b64 = sealed["secretToken"].split(":")
        raw = bytearray(base64.b64decode(sealed_b64))
        raw[0] ^= 0xFF
        tampered = f"enc:v1:{nonce}:{base64.b64encode(bytes(raw)).decode('ascii')}"
        with secret_env(key=KEY_A):
            with self.assertRaises(SecretDecryptionError):
                decrypt_secrets({"secretToken": tampered})

    def test_swapped_field_fails_via_aad(self):
        with secret_env(key=KEY_A):
            sealed = encrypt_secrets({"secretToken": TOKEN})
        swapped = {"webhookSecret": sealed["secretToken"]}
        with secret_env(key=KEY_A):
            with self.assertRaises(SecretDecryptionError):
                decrypt_secrets(swapped)

    def test_missing_key_optional_passes_through_with_warning(self):
        draft = sample_draft()
        with secret_env(mode=MODE_OPTIONAL):
            with self.assertLogs("SecuritySecrets", level="WARNING") as logs:
                stored = encrypt_secrets(draft)
        self.assertEqual(stored, draft)
        text = "\n".join(logs.output)
        self.assertNotIn(TOKEN, text)
        self.assertNotIn(WEBHOOK, text)

    def test_missing_key_required_refuses_write(self):
        with secret_env(mode=MODE_REQUIRED):
            with self.assertRaises(MissingSecretsKeyError):
                encrypt_secrets(sample_draft())

    def test_missing_key_read_of_envelope_refuses_in_both_modes(self):
        with secret_env(key=KEY_A):
            sealed = encrypt_secrets(sample_draft())
        for mode in (MODE_OPTIONAL, MODE_REQUIRED):
            with self.subTest(mode=mode), secret_env(mode=mode):
                with self.assertRaises(MissingSecretsKeyError):
                    decrypt_secrets(sealed)

    def test_broken_config_is_explicit_error(self):
        with secret_env(key=base64.b64encode(b"short").decode("ascii")):
            with self.assertRaises(SecretsConfigError):
                load_key()
        with secret_env(key="не-base64!!"):
            with self.assertRaises(SecretsConfigError):
                load_key()
        with secret_env(key=KEY_A, mode="always"):
            with self.assertRaises(SecretsConfigError):
                secrets_mode()

    def test_default_mode_is_optional_and_empty_key_is_none(self):
        with secret_env():
            self.assertEqual(secrets_mode(), MODE_OPTIONAL)
            self.assertIsNone(load_key())


class ReservedEnvelopeTests(unittest.TestCase):
    """enc:v1:* из пользовательского ввода не доходит до БД: 422 на модели."""

    @staticmethod
    def _payload(secret: str) -> dict:
        return {
            "name": "PR gate",
            "description": "",
            "nodes": [
                {
                    "id": "n1",
                    "kind": "notify",
                    "config": {"secretToken": secret, "webhookUrl": "https://hooks.example/t"},
                },
            ],
            "edges": [],
        }

    def test_model_rejects_reserved_on_create_and_update(self):
        for secret in ("enc:v1:AAAA:AAAA", "enc:v1:garbage!", "enc:v1:"):
            with self.subTest(secret=secret):
                with self.assertRaises(ValidationError) as ctx:
                    WorkflowDraft(**self._payload(secret))
                self.assertIn("enc:v1:", str(ctx.exception))
                # Update — та же модель: прочитанный черновик + подменённый секрет.
                with self.assertRaises(ValidationError):
                    WorkflowDraft(**{**self._payload("sk-plain"), **self._payload(secret)})
        with self.assertRaises(ValidationError):
            WorkflowNode(id="n1", kind="notify", config={"webhookSecret": "enc:v1:AAAA"})

    def test_fastapi_maps_reserved_secret_to_422(self):
        app = FastAPI()

        @app.post("/workflows")
        def create(draft: WorkflowDraft):
            return {"ok": True}

        @app.put("/workflows/{workflow_id}")
        def update(workflow_id: str, draft: WorkflowDraft):
            return {"ok": True}

        client = TestClient(app)
        reserved = self._payload("enc:v1:AAAA:AAAA")
        responses = (
            ("POST", client.post("/workflows", json=reserved)),
            ("PUT", client.put("/workflows/wf1", json=reserved)),
        )
        for method, response in responses:
            with self.subTest(method=method):
                self.assertEqual(response.status_code, 422)
                detail = json.dumps(response.json()["detail"], ensure_ascii=False)
                self.assertIn("enc:v1:", detail)
        # Обычный секрет по-прежнему принимается.
        valid = self._payload("sk-plain-0123456789")
        self.assertEqual(client.post("/workflows", json=valid).status_code, 200)

    def test_valid_secret_still_accepted_and_encrypted(self):
        draft = WorkflowDraft(**self._payload("sk-plain-0123456789"))
        with secret_env(key=KEY_A):
            sealed = encrypt_secrets(draft.model_dump())
            self.assertTrue(is_encrypted(sealed["nodes"][0]["config"]["secretToken"]))
            self.assertEqual(decrypt_secrets(sealed), draft.model_dump())

    def test_storage_refuses_reserved_prefix_with_key(self):
        with secret_env(key=KEY_A):
            with self.assertRaises(SecretDecryptionError):
                encrypt_secrets({"secretToken": "enc:v1:AAAA:AAAA"})
            with self.assertRaises(ReservedEnvelopeError):
                encrypt_secrets({"secretToken": "enc:v1:garbage!"})
            with self.assertRaises(ReservedEnvelopeError):
                encrypt_secrets({"meta": [{"config": {"webhookSecret": "enc:v1:x"}}]})

    def test_storage_refuses_reserved_prefix_without_key(self):
        with secret_env(mode=MODE_OPTIONAL):
            with self.assertRaises(MissingSecretsKeyError):
                encrypt_secrets({"secretToken": "enc:v1:AAAA:AAAA"})
            with self.assertRaises(ReservedEnvelopeError):
                encrypt_secrets({"secretToken": "enc:v1:garbage!"})


# --- Проводка в backend.app.storage.database ---------------------------------

class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, store):
        self._store = store

    def execute(self, sql, params=None):  # noqa: ANN001 - psycopg-совместимая сигнатура
        store = self._store
        if "INSERT INTO runtime.workflows" in sql:
            ws, wf_id, name, description = params[0], params[1], params[2], params[3]
            row = store.workflows.get(wf_id)
            if row is None:
                store.workflows[wf_id] = {
                    "workspace_id": ws, "id": wf_id, "name": name,
                    "description": description, "latest_revision_id": None,
                }
            else:
                row.update({"name": name, "description": description})
            return _FakeCursor([])
        if "SELECT 1 FROM runtime.workflows" in sql:
            return _FakeCursor([{"?column?": 1}])
        if "SELECT latest_revision_id FROM runtime.workflows" in sql:
            row = store.workflows.get(params[1])
            return _FakeCursor([{"latest_revision_id": row["latest_revision_id"]}] if row else [])
        if "MAX(revision_num)" in sql:
            wf_id = params[1]
            count = sum(1 for rev in store.revisions.values() if rev["workflow_id"] == wf_id)
            return _FakeCursor([{"max_rev": count}])
        if "INSERT INTO runtime.workflow_revisions" in sql:
            rev_id, wf_id, draft_json = params[1], params[2], params[4]
            draft = getattr(draft_json, "obj", draft_json)
            store.revisions[rev_id] = {"workflow_id": wf_id, "draft": draft}
            return _FakeCursor([])
        if "UPDATE runtime.workflows" in sql:
            # save_workflow: (rev_id, ws, wf_id); save_workflow_if_latest: (name, desc, now, rev_id, ws, wf_id)
            rev_id = params[0] if params[0].startswith("rev_") else params[3]
            store.workflows[params[-1]]["latest_revision_id"] = rev_id
            return _FakeCursor([])
        if "SELECT draft_json FROM runtime.workflow_revisions" in sql:
            rev = store.revisions.get(params[1])
            return _FakeCursor([{"draft_json": rev["draft"]}] if rev else [])
        if "SELECT w.*, r.draft_json AS draft" in sql:
            rows = []
            for row in store.workflows.values():
                latest = row["latest_revision_id"]
                rev = store.revisions.get(latest) if latest else None
                rows.append({
                    "id": row["id"], "name": row["name"],
                    "latest_revision_id": latest,
                    "draft": rev["draft"] if rev else None,
                })
            return _FakeCursor(rows)
        if "SELECT * FROM runtime.workflow_revisions" in sql:
            rev = store.revisions.get(params[1])
            return _FakeCursor([
                {"workspace_id": params[0], "id": params[1],
                 "workflow_id": rev["workflow_id"], "revision_num": 1,
                 "draft_json": rev["draft"], "created_at": "2026-11-22T00:00:00+00:00"}
            ] if rev else [])
        if "SELECT * FROM runtime.workflows" in sql:
            row = store.workflows.get(params[1])
            return _FakeCursor([dict(row)] if row else [])
        raise AssertionError(f"неожиданный SQL в фиктивном pool: {sql}")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, store):
        self._store = store

    def connection(self):
        return _FakeConn(self._store)


class StorageWiringTests(unittest.TestCase):
    """Запись шифрует, чтение возвращает прежний plaintext-контракт."""

    def setUp(self):
        from backend.app.storage import database as db_module

        self.db_module = db_module
        self.store = type("Store", (), {"workflows": {}, "revisions": {}})()
        self.repo = db_module.DatabaseRepository(initialize=False)
        self._pool_patch = mock.patch.object(
            db_module, "get_pool", return_value=_FakePool(self.store)
        )
        self._pool_patch.start()
        self.addCleanup(self._pool_patch.stop)

    def test_save_stores_envelopes_and_reads_return_plaintext(self):
        draft = sample_draft()
        with secret_env(key=KEY_A):
            rev_id = self.repo.save_workflow("wf1", "PR gate", "", draft)
            stored = self.store.revisions[rev_id]["draft"]
            self.assertNotIn(TOKEN, json.dumps(stored, ensure_ascii=False))
            self.assertTrue(is_encrypted(stored["nodes"][0]["config"]["secretToken"]))
            self.assertTrue(is_encrypted(stored["meta"]["groups"][0]["config"]["secret_token"]))
            # Прод-контракт чтения: get_workflow отдаёт plaintext-черновик.
            self.assertEqual(self.repo.get_workflow("wf1")["draft"], draft)
            self.assertEqual(self.repo.list_workflows()[0]["draft"], draft)
            revision = self.repo.get_workflow_revision(rev_id)
            self.assertEqual(revision["draft"], draft)
            self.assertEqual(json.loads(revision["draft_json"]), draft)

    def test_legacy_plaintext_rows_read_without_loss(self):
        # Строка, записанная до включения шифрования, читается как раньше.
        draft = sample_draft()
        with secret_env():
            self.repo.save_workflow("wf_legacy", "legacy", "", draft)
        with secret_env(key=KEY_A):
            self.assertEqual(self.repo.get_workflow("wf_legacy")["draft"], draft)
            self.assertEqual(self.repo.list_workflows()[0]["draft"], draft)

    def test_optional_without_key_stores_plaintext_as_before(self):
        draft = sample_draft()
        with secret_env(mode=MODE_OPTIONAL):
            with self.assertLogs("SecuritySecrets", level="WARNING"):
                rev_id = self.repo.save_workflow("wf_plain", "plain", "", draft)
        self.assertEqual(self.store.revisions[rev_id]["draft"], draft)
        self.assertEqual(self.repo.get_workflow("wf_plain")["draft"], draft)

    def test_required_without_key_refuses_revision_write(self):
        with secret_env(mode=MODE_REQUIRED):
            with self.assertRaises(MissingSecretsKeyError):
                self.repo.save_workflow("wf_req", "req", "", sample_draft())
        self.assertEqual(self.store.revisions, {})

    def test_conflict_path_returns_decrypted_winner(self):
        draft = sample_draft()
        with secret_env(key=KEY_A):
            rev_id = self.repo.save_workflow("wf2", "PR gate", "", draft)
            conflict = self.repo.save_workflow_if_latest(
                "wf2", "rev_does_not_match", "PR gate", "", sample_draft(),
            )
        self.assertIsNone(conflict["revision_id"])
        self.assertEqual(conflict["latest_revision_id"], rev_id)
        self.assertEqual(conflict["draft"], draft)

    def test_tampered_row_fails_read_explicitly(self):
        draft = sample_draft()
        with secret_env(key=KEY_A):
            rev_id = self.repo.save_workflow("wf3", "PR gate", "", draft)
            stored = self.store.revisions[rev_id]["draft"]
            secret = stored["nodes"][0]["config"]["secretToken"]
            _, _, nonce, sealed_b64 = secret.split(":")
            raw = bytearray(base64.b64decode(sealed_b64))
            raw[-1] ^= 0xFF
            stored["nodes"][0]["config"]["secretToken"] = (
                f"enc:v1:{nonce}:{base64.b64encode(bytes(raw)).decode('ascii')}"
            )
            with self.assertRaises(SecretDecryptionError):
                self.repo.get_workflow("wf3")

    def test_reserved_envelope_never_reaches_storage(self):
        # Обход модели (внутренний путь) тоже не кладёт закрафченный конверт.
        draft = sample_draft()
        draft["nodes"][0]["config"]["secretToken"] = "enc:v1:AAAA:AAAA"
        with secret_env(key=KEY_A):
            with self.assertRaises(SecretDecryptionError):
                self.repo.save_workflow("wf_bad", "bad", "", draft)
        self.assertEqual(self.store.revisions, {})


if __name__ == "__main__":
    unittest.main()
