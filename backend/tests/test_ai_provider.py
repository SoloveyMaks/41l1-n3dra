"""Provider resolver and chat client checks against a local fake OpenAI server.

Covered here: a generic OpenAI-compatible gateway works with no ReformBoss
configuration at all; a keyless local gateway (Ollama/vLLM) is valid; missing
keys for remote providers fail closed with a bounded outcome; provenance
reports the provider that actually served the request and never a credential.

Run: PYTHONPATH=. /tmp/nedra-venv/bin/python -m pytest backend/tests/test_ai_provider.py -q
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.ai import provider as provider_module
from backend.tests.test_ai_fake_server import FakeOpenAIServer

AI_ENV_KEYS = (
    "AI_PROVIDER",
    "AI_API_KEY",
    "OPENCODE_API_KEY",
    "REFORMBOSS_API_KEY",
    "AI_BASE_URL",
    "AI_MODEL",
    "LLM_MODEL",
    "AI_JSON_MODE",
    "AI_TIMEOUT_SECONDS",
    "AI_MAX_TOKENS",
    "AI_TEMPERATURE",
    "AI_BATCH_SIZE",
)

# The legacy agent .env fallback must never leak into a test run.
_NO_AGENT_ENV = Path("/nonexistent/nedra-test/agent.env")


@contextmanager
def ai_env(**values: str) -> Iterator[None]:
    saved: Dict[str, Optional[str]] = {key: os.environ.get(key) for key in AI_ENV_KEYS}
    for key in AI_ENV_KEYS:
        os.environ.pop(key, None)
    os.environ.update(values)
    try:
        with mock.patch.object(provider_module, "AGENT_ENV_FILE", _NO_AGENT_ENV):
            yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class ProviderResolverTests(unittest.TestCase):
    def test_generic_gateway_resolves_without_reformboss(self) -> None:
        with FakeOpenAIServer() as server, ai_env(
            AI_BASE_URL=server.base_url,
            AI_MODEL="test-model",
            AI_API_KEY="test-key",
        ):
            config = provider_module.resolve_provider_config()
            self.assertIsNotNone(config)
            assert config is not None
            self.assertEqual(config.provider, provider_module.LOCAL_PROVIDER)
            self.assertEqual(config.model, "test-model")
            self.assertEqual(config.base_url, server.base_url)
            self.assertNotEqual(config.provider, provider_module.REFORMBOSS_PROVIDER)
            self.assertNotIn("reformboss", config.base_url)

    def test_keyless_local_gateway_resolves_without_any_key(self) -> None:
        with FakeOpenAIServer() as server, ai_env(
            AI_BASE_URL=server.base_url,
            AI_MODEL="llama3.1",
        ):
            config = provider_module.resolve_provider_config()
            self.assertIsNotNone(config)
            assert config is not None
            self.assertEqual(config.provider, provider_module.LOCAL_PROVIDER)
            self.assertEqual(config.api_key, "")
            self.assertEqual(config.model, "llama3.1")

    def test_explicit_keyless_local_provider_resolves(self) -> None:
        with FakeOpenAIServer() as server, ai_env(
            AI_PROVIDER="openai-compatible",
            AI_BASE_URL=server.base_url,
            AI_MODEL="qwen2.5",
        ):
            config = provider_module.resolve_provider_config()
            self.assertIsNotNone(config)
            assert config is not None
            self.assertEqual(config.provider, provider_module.LOCAL_PROVIDER)
            self.assertEqual(config.api_key, "")

    def test_declared_custom_provider_keeps_its_name(self) -> None:
        with FakeOpenAIServer() as server, ai_env(
            AI_PROVIDER="ollama",
            AI_BASE_URL=server.base_url,
            AI_MODEL="llama3.1",
        ):
            config = provider_module.resolve_provider_config()
            self.assertIsNotNone(config)
            assert config is not None
            self.assertEqual(config.provider, "ollama")
            self.assertNotEqual(config.provider, provider_module.REFORMBOSS_PROVIDER)

    def test_missing_remote_key_fails_closed(self) -> None:
        with ai_env():
            self.assertIsNone(provider_module.resolve_provider_config())
        with ai_env(AI_PROVIDER="opencode-go"):
            self.assertIsNone(provider_module.resolve_provider_config())
        with ai_env(AI_PROVIDER="reformboss"):
            self.assertIsNone(provider_module.resolve_provider_config())
        with ai_env(REFORMBOSS_API_KEY="legacy-key", AI_PROVIDER="opencode-go"):
            # A legacy key must never be borrowed by another provider.
            self.assertIsNone(provider_module.resolve_provider_config())

    def test_incomplete_gateway_config_is_not_configured(self) -> None:
        # Base URL without a model: no model may be invented for a gateway.
        with ai_env(AI_BASE_URL="http://127.0.0.1:9/v1"):
            self.assertIsNone(provider_module.resolve_provider_config())
        # Declared provider without a base URL: never reuse another default.
        with ai_env(AI_PROVIDER="ollama", AI_MODEL="llama3.1"):
            self.assertIsNone(provider_module.resolve_provider_config())

    def test_generic_key_without_base_url_stays_on_go(self) -> None:
        with ai_env(AI_API_KEY="generic-key"):
            config = provider_module.resolve_provider_config()
            self.assertIsNotNone(config)
            assert config is not None
            self.assertEqual(config.provider, provider_module.OPENCODE_GO_PROVIDER)
            self.assertEqual(config.base_url, provider_module.OPENCODE_GO_BASE_URL)

    def test_legacy_key_still_resolves_only_as_legacy(self) -> None:
        with ai_env(REFORMBOSS_API_KEY="legacy-key"):
            config = provider_module.resolve_provider_config()
            self.assertIsNotNone(config)
            assert config is not None
            self.assertEqual(config.provider, provider_module.REFORMBOSS_PROVIDER)


class CompleteJsonTests(unittest.TestCase):
    def test_generic_provider_completes_json_with_honest_provenance(self) -> None:
        with FakeOpenAIServer() as server, ai_env(
            AI_BASE_URL=server.base_url,
            AI_MODEL="test-model",
            AI_API_KEY="test-key",
        ):
            config = provider_module.resolve_provider_config()
            assert config is not None
            data, provenance = provider_module.complete_json(
                config, "system prompt", {"findingId": "f-1"}
            )
            self.assertEqual(data, json.loads(server.chat_content))
            self.assertEqual(provenance.provider, provider_module.LOCAL_PROVIDER)
            self.assertEqual(provenance.model, "test-model")
            self.assertEqual(provenance.endpoint, f"http://127.0.0.1:{server.port}")
            self.assertEqual(provenance.request_id, "req-fake-1")
            self.assertIsNone(provenance.fallback_reason)
            self.assertNotIn("reformboss", provenance.provider)
            self.assertNotIn("test-key", provenance.model_dump_json())

            request = server.requests[0]
            self.assertEqual(request["authorization"], "Bearer test-key")
            self.assertEqual(request["user_agent"], provider_module.NEDRA_USER_AGENT)
            self.assertIsNone(request["session"])  # session header is Go-only

    def test_keyless_gateway_sends_no_authorization_header(self) -> None:
        with FakeOpenAIServer() as server, ai_env(
            AI_BASE_URL=server.base_url,
            AI_MODEL="llama3.1",
        ):
            config = provider_module.resolve_provider_config()
            assert config is not None
            _, provenance = provider_module.complete_json(config, "system", {"a": 1})
            self.assertIsNone(server.requests[0]["authorization"])
            self.assertEqual(provenance.provider, provider_module.LOCAL_PROVIDER)

    def test_http_error_is_bounded_and_masked(self) -> None:
        with FakeOpenAIServer() as server, ai_env(
            AI_BASE_URL=server.base_url,
            AI_MODEL="test-model",
            AI_API_KEY="test-key",
        ):
            server.chat_status = 401
            server.error_message = "invalid api key sk-supersecret12345"
            config = provider_module.resolve_provider_config()
            assert config is not None
            with self.assertRaises(provider_module.AiProviderError) as caught:
                provider_module.complete_json(config, "system", {})
            message = str(caught.exception)
            self.assertIn("provider HTTP 401", message)
            self.assertNotIn("sk-supersecret12345", message)
            self.assertIn("***", message)

    def test_unreachable_provider_raises_bounded_error(self) -> None:
        with ai_env(
            AI_BASE_URL="http://127.0.0.1:9/v1",
            AI_MODEL="test-model",
            AI_API_KEY="test-key",
        ):
            config = provider_module.resolve_provider_config()
            assert config is not None
            with self.assertRaises(provider_module.AiProviderError) as caught:
                provider_module.complete_json(config, "system", {})
            self.assertIn("provider unreachable", str(caught.exception))

    def test_external_metadata_is_masked_and_bounded_in_provenance(self) -> None:
        with FakeOpenAIServer() as server, ai_env(
            AI_BASE_URL=server.base_url,
            AI_MODEL="test-model",
            AI_API_KEY="test-key",
        ):
            server.request_id = "req sk-chatsecret12345"
            server.chat_model = "sk-modelsecret9999"
            config = provider_module.resolve_provider_config()
            assert config is not None
            _, provenance = provider_module.complete_json(config, "system", {})
            dump = provenance.model_dump_json()
            self.assertNotIn("sk-chatsecret12345", dump)
            self.assertNotIn("sk-modelsecret9999", dump)
            self.assertIn("***", provenance.request_id or "")
            self.assertLessEqual(len(provenance.request_id or ""), 128)
            self.assertEqual(provenance.model, "***")


if __name__ == "__main__":
    unittest.main()
