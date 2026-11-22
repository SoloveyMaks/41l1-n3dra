"""Vetted-path vector runtime checks: honest status, spec/dimension refusal.

Covered here: with the runtime off the status stays ``disabled`` and nothing
is invented or fetched; the embeddings adapter only returns a vector that
matches the configured spec (model, dimension, finite values) and carries
provenance; any incompatibility becomes typed ``unavailable``, never a
success; a reachable embeddings endpoint without a database is still
``unavailable``, not ``ok``.

Run: PYTHONPATH=. /tmp/nedra-venv/bin/python -m pytest backend/tests/test_vector_runtime.py -q
"""
from __future__ import annotations

import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.ai.vector_runtime import (
    EmbeddingsAdapter,
    VectorConfig,
    VectorRuntime,
    resolve_vector_config,
)
from backend.app.codefacts.identity import reset_workspace_scope, set_workspace_scope
from backend.app.contracts.schemas import Confidence, Finding, Location, Severity
from backend.tests.test_ai_fake_server import FakeOpenAIServer

WORKSPACE_ID = "887e5059-1fbe-5571-a037-601a3cd67744"
DSN = "postgresql://nedra:secret@127.0.0.1:9/nedra"

VECTOR_ENV_KEYS = (
    "VECTOR_RETRIEVAL_ENABLED",
    "VECTOR_RETRIEVAL_DSN",
    "DATABASE_URL",
    "VECTOR_WORKSPACE_ID",
    "VECTOR_EMBEDDING_SPEC_ID",
    "VECTOR_EMBEDDING_PROVIDER",
    "VECTOR_EMBEDDING_MODEL",
    "VECTOR_EMBEDDING_VERSION",
    "VECTOR_EMBEDDING_DIMENSION",
    "VECTOR_EMBEDDING_METRIC",
    "VECTOR_EMBEDDING_QUERY_PREPARATION_VERSION",
    "VECTOR_EMBEDDING_DOCUMENT_PREPARATION_VERSION",
    "VECTOR_EMBEDDING_REDACTION_VERSION",
    "VECTOR_EMBEDDING_BASE_URL",
    "VECTOR_EMBEDDING_API_KEY",
    "VECTOR_EMBEDDING_TIMEOUT_SECONDS",
    "VECTOR_RETRIEVAL_STATUS",
    "VECTOR_RETRIEVAL_DEMO_FIXTURE",
    "VECTOR_RULE_FAMILY_MAP",
    "VECTOR_RETRIEVAL_LIMIT",
    "VECTOR_RETRIEVAL_MAX_FINDINGS",
    "VECTOR_RETRIEVAL_LANGUAGE",
    "VECTOR_RETRIEVAL_FRAMEWORK",
)


def configured_env(**overrides: str) -> Dict[str, str]:
    """A complete, valid vetted-path configuration (endpoint overridden per test)."""
    env = {
        "VECTOR_RETRIEVAL_ENABLED": "1",
        "VECTOR_RETRIEVAL_DSN": DSN,
        "VECTOR_WORKSPACE_ID": WORKSPACE_ID,
        "VECTOR_EMBEDDING_SPEC_ID": "spec-1",
        "VECTOR_EMBEDDING_PROVIDER": "openai-compatible",
        "VECTOR_EMBEDDING_MODEL": "text-embedding-test",
        "VECTOR_EMBEDDING_VERSION": "1",
        "VECTOR_EMBEDDING_DIMENSION": "4",
        "VECTOR_EMBEDDING_METRIC": "cosine",
        "VECTOR_EMBEDDING_QUERY_PREPARATION_VERSION": "q1",
        "VECTOR_EMBEDDING_DOCUMENT_PREPARATION_VERSION": "d1",
        "VECTOR_EMBEDDING_REDACTION_VERSION": "r1",
        "VECTOR_EMBEDDING_BASE_URL": "http://127.0.0.1:9/v1",
        "VECTOR_EMBEDDING_API_KEY": "embed-key",
        "VECTOR_RETRIEVAL_STATUS": "vetted",
    }
    env.update(overrides)
    return env


@contextmanager
def vector_env(**values: str) -> Iterator[None]:
    saved: Dict[str, Optional[str]] = {key: os.environ.get(key) for key in VECTOR_ENV_KEYS}
    for key in VECTOR_ENV_KEYS:
        os.environ.pop(key, None)
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def scoped_workspace() -> Iterator[None]:
    token = set_workspace_scope(WORKSPACE_ID)
    try:
        yield
    finally:
        reset_workspace_scope(token)


def make_finding() -> Finding:
    return Finding(
        id="f-1",
        fingerprint="a" * 40,
        analyzer="sca",
        ruleId="KOTLIN-SQL-INJECTION",
        severity=Severity.HIGH,
        severityBasis="test",
        confidence=Confidence.CONFIRMED,
        confidenceBasis="test",
        primaryLocation=Location(
            path="src/Main.kt",
            startLine=1,
            endLine=2,
            startColumn=1,
            endColumn=2,
        ),
        redactedEvidence="redacted evidence",
    )


class DisabledStatusTests(unittest.TestCase):
    def test_disabled_without_config_invents_nothing(self) -> None:
        with vector_env():
            runtime = VectorRuntime()
            info = runtime.status()
            self.assertEqual(info.status, "disabled")
            self.assertIn("VECTOR_RETRIEVAL_ENABLED=0", info.reason)
            self.assertEqual(info.embeddingModel, "")
            self.assertEqual(info.embeddingVersion, "")
            self.assertIsNone(info.dimension)
            self.assertEqual(info.metric, "")
            self.assertIsNone(info.embeddingSpecId)

    def test_disabled_with_full_config_stays_disabled_and_offline(self) -> None:
        with FakeOpenAIServer() as server, vector_env(
            **configured_env(
                VECTOR_RETRIEVAL_ENABLED="0",
                VECTOR_EMBEDDING_BASE_URL=server.base_url,
            )
        ):
            runtime = VectorRuntime()
            info, examples = runtime.retrieve_examples_for_findings([make_finding()])
            self.assertEqual(info.status, "disabled")
            self.assertIn("VECTOR_RETRIEVAL_ENABLED=0", info.reason)
            self.assertEqual(examples, [])
            self.assertEqual(server.requests, [])


class SpecConfigTests(unittest.TestCase):
    def test_invalid_metric_is_unavailable_not_ready(self) -> None:
        with vector_env(**configured_env(VECTOR_EMBEDDING_METRIC="manhattan")):
            info = VectorRuntime().status()
            self.assertEqual(info.status, "unavailable")
            self.assertIn("VECTOR_EMBEDDING_METRIC", info.reason)
            self.assertNotIn(info.status, ("ready", "ok"))

    def test_missing_dimension_is_unavailable_not_ready(self) -> None:
        with vector_env(**configured_env(VECTOR_EMBEDDING_DIMENSION="")):
            info = VectorRuntime().status()
            self.assertEqual(info.status, "unavailable")
            self.assertIn("VECTOR_EMBEDDING_DIMENSION", info.reason)

    def test_invalid_status_value_is_unavailable(self) -> None:
        with vector_env(**configured_env(VECTOR_RETRIEVAL_STATUS="approved")):
            info = VectorRuntime().status()
            self.assertEqual(info.status, "unavailable")
            self.assertIn("VECTOR_RETRIEVAL_STATUS", info.reason)


class EmbeddingsAdapterTests(unittest.TestCase):
    def test_matching_spec_returns_vector_and_provenance(self) -> None:
        with FakeOpenAIServer() as server, vector_env(
            **configured_env(VECTOR_EMBEDDING_BASE_URL=server.base_url)
        ):
            config = resolve_vector_config()
            vector, provenance = EmbeddingsAdapter(config).embed("query text")
            self.assertEqual(len(vector), 4)
            self.assertEqual(provenance.provider, "openai-compatible")
            self.assertEqual(provenance.model, "text-embedding-test")
            self.assertEqual(provenance.dimension, 4)
            self.assertEqual(provenance.metric, "cosine")
            self.assertEqual(provenance.endpoint, server.base_url)
            self.assertEqual(provenance.request_id, "req-fake-1")
            self.assertGreaterEqual(provenance.latency_ms, 0.0)
            summary = provenance.summary()
            self.assertIn("openai-compatible/text-embedding-test", summary)
            self.assertIn("dim=4", summary)
            self.assertNotIn("embed-key", summary)
            self.assertEqual(server.requests[0]["authorization"], "Bearer embed-key")

    def test_keyless_config_omits_authorization_header(self) -> None:
        with FakeOpenAIServer() as server:
            config = VectorConfig(
                enabled=True,
                dsn=DSN,
                workspace_id=WORKSPACE_ID,
                embedding_spec_id="spec-1",
                provider="openai-compatible",
                model="local-model",
                version="1",
                dimension=4,
                metric="cosine",
                query_preparation_version="q1",
                document_preparation_version="d1",
                redaction_version="r1",
                embedding_base_url=server.base_url,
                embedding_api_key="",
            )
            EmbeddingsAdapter(config).embed("query")
            self.assertIsNone(server.requests[0]["authorization"])

    def test_wrong_dimension_is_refused(self) -> None:
        with FakeOpenAIServer() as server, vector_env(
            **configured_env(VECTOR_EMBEDDING_BASE_URL=server.base_url)
        ):
            server.embedding_dimension = 3
            config = resolve_vector_config()
            with self.assertRaises(RuntimeError) as caught:
                EmbeddingsAdapter(config).embed("query")
            self.assertIn("dimension 3 does not match configured 4", str(caught.exception))

    def test_wrong_model_is_refused(self) -> None:
        with FakeOpenAIServer() as server, vector_env(
            **configured_env(VECTOR_EMBEDDING_BASE_URL=server.base_url)
        ):
            server.embedding_model = "some-other-model"
            config = resolve_vector_config()
            with self.assertRaises(RuntimeError) as caught:
                EmbeddingsAdapter(config).embed("query")
            self.assertIn("does not match configured", str(caught.exception))

    def test_unreachable_endpoint_is_a_bounded_error(self) -> None:
        with vector_env(**configured_env()):
            config = resolve_vector_config()
            with self.assertRaises(RuntimeError) as caught:
                EmbeddingsAdapter(config).embed("query")
            self.assertIn("embedding endpoint unreachable", str(caught.exception))


class ExactRetrievalRefusalTests(unittest.TestCase):
    def test_dimension_mismatch_is_unavailable_not_success(self) -> None:
        with FakeOpenAIServer() as server, vector_env(
            **configured_env(VECTOR_EMBEDDING_BASE_URL=server.base_url)
        ), scoped_workspace():
            server.embedding_dimension = 3
            runtime = VectorRuntime()
            info, examples = runtime.retrieve_examples_for_findings([make_finding()])
            self.assertEqual(info.status, "unavailable")
            self.assertIn("embedding unavailable", info.reason)
            self.assertIn("dimension", info.reason)
            self.assertEqual(examples, [])
            self.assertNotIn("exact retrieval completed", info.reason)

    def test_model_mismatch_is_unavailable_not_success(self) -> None:
        with FakeOpenAIServer() as server, vector_env(
            **configured_env(VECTOR_EMBEDDING_BASE_URL=server.base_url)
        ), scoped_workspace():
            server.embedding_model = "other-model"
            runtime = VectorRuntime()
            info, examples = runtime.retrieve_examples_for_findings([make_finding()])
            self.assertEqual(info.status, "unavailable")
            self.assertIn("does not match configured", info.reason)
            self.assertEqual(examples, [])

    def test_missing_workspace_scope_is_unavailable_before_any_request(self) -> None:
        with FakeOpenAIServer() as server, vector_env(
            **configured_env(VECTOR_EMBEDDING_BASE_URL=server.base_url)
        ):
            runtime = VectorRuntime()
            info, examples = runtime.retrieve_examples_for_findings([make_finding()])
            self.assertEqual(info.status, "unavailable")
            self.assertIn("workspace scope", info.reason)
            self.assertEqual(examples, [])
            self.assertEqual(server.requests, [])

    def test_embeddings_ok_without_database_is_still_unavailable(self) -> None:
        with FakeOpenAIServer() as server, vector_env(
            **configured_env(VECTOR_EMBEDDING_BASE_URL=server.base_url)
        ), scoped_workspace():
            runtime = VectorRuntime()
            info, examples = runtime.retrieve_examples_for_findings([make_finding()])
            self.assertEqual(info.status, "unavailable")
            self.assertIn("database unavailable", info.reason)
            self.assertEqual(examples, [])
            self.assertEqual(len(server.requests), 1)


if __name__ == "__main__":
    unittest.main()
