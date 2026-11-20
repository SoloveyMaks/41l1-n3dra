"""Small OpenAI-compatible chat client used by the AI explanation layer.

Configuration comes only from the environment. API keys are never logged,
stored in provenance or returned to callers.

Provider selection:
  * AI_PROVIDER=reformboss|wikivibe (optional; inferred from the key below)
  * REFORMBOSS_API_KEY   -> https://api.reformboss.com/v1
  * WIKIVIBE_GPT_KEY     -> https://api.wikivibe.dev/v1

The base URL, model and limits can be overridden with AI_BASE_URL,
AI_MODEL/LLM_MODEL, AI_TIMEOUT_SECONDS, AI_MAX_TOKENS and AI_TEMPERATURE.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from backend.app.contracts.schemas import AiProvenance


class _SameHostRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse redirects that would forward the Authorization header elsewhere."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old_host = urllib.parse.urlsplit(req.full_url).netloc
        new_host = urllib.parse.urlsplit(newurl).netloc
        if old_host != new_host:
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SameHostRedirectHandler())


def open_same_host(request: urllib.request.Request, timeout: float):
    """Open a request without following redirects to another host."""
    return _OPENER.open(request, timeout=timeout)


REFORMBOSS_BASE_URL = "https://api.reformboss.com/v1"
WIKIVIBE_BASE_URL = "https://api.wikivibe.dev/v1"
REFORMBOSS_DEFAULT_MODEL = "deepseek-v4.1-flash-1017"
WIKIVIBE_DEFAULT_MODEL = "deepseek-v4.1-flash"

_SECRET_PATTERNS = (
    (re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{8,}"), "***"),
    (re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1***"),
    (re.compile(r"(?i)\b((?:api[_-]?key|token|secret|password)\s*[:=]\s*)[^\s,;\"']+"), r"\1***"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "***"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "***"),
    (re.compile(r"(?i)(://)[^@\s/]+@"), r"\1***@"),
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
        "[PRIVATE KEY REDACTED]",
    ),
)


def mask_sensitive_text(text: str) -> str:
    """Best-effort masking for anything that leaves the process."""
    masked = text or ""
    for pattern, replacement in _SECRET_PATTERNS:
        masked = pattern.sub(replacement, masked)
    return masked


class AiProviderError(RuntimeError):
    """Raised when the remote provider is unreachable or returned bad data."""


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float
    max_tokens: int
    temperature: float

    @property
    def endpoint(self) -> str:
        parts = urllib.parse.urlsplit(self.base_url)
        return f"{parts.scheme}://{parts.netloc}" if parts.scheme else ""


def _env_float(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(low, min(high, value))


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(low, min(high, value))


def resolve_provider_config() -> Optional[ProviderConfig]:
    """Return an active provider config, or None when no key is configured."""
    explicit = os.getenv("AI_PROVIDER", "").strip().lower()
    reform_key = os.getenv("REFORMBOSS_API_KEY", "").strip()
    wikivibe_key = os.getenv("WIKIVIBE_GPT_KEY", "").strip()

    if explicit == "reformboss":
        provider, key, default_base, default_model = (
            "reformboss", reform_key, REFORMBOSS_BASE_URL, REFORMBOSS_DEFAULT_MODEL
        )
    elif explicit == "wikivibe":
        provider, key, default_base, default_model = (
            "wikivibe", wikivibe_key, WIKIVIBE_BASE_URL, WIKIVIBE_DEFAULT_MODEL
        )
    elif reform_key:
        provider, key, default_base, default_model = (
            "reformboss", reform_key, REFORMBOSS_BASE_URL, REFORMBOSS_DEFAULT_MODEL
        )
    elif wikivibe_key:
        provider, key, default_base, default_model = (
            "wikivibe", wikivibe_key, WIKIVIBE_BASE_URL, WIKIVIBE_DEFAULT_MODEL
        )
    else:
        return None

    if not key:
        return None

    base_url = os.getenv("AI_BASE_URL", "").strip() or default_base
    model = os.getenv("AI_MODEL", "").strip() or os.getenv("LLM_MODEL", "").strip() or default_model
    return ProviderConfig(
        provider=provider,
        base_url=base_url.rstrip("/"),
        api_key=key,
        model=model,
        timeout_seconds=_env_float("AI_TIMEOUT_SECONDS", 30.0, 5.0, 120.0),
        max_tokens=_env_int("AI_MAX_TOKENS", 2048, 256, 8192),
        temperature=_env_float("AI_TEMPERATURE", 0.0, 0.0, 1.0),
    )


def _sanitize_error(text: str, limit: int = 400) -> str:
    cleaned = mask_sensitive_text(text or "")
    return cleaned[:limit]


def parse_json_content(content: str) -> Dict[str, Any]:
    """Parse a JSON object from plain or markdown-fenced model output."""
    if not isinstance(content, str) or not content.strip():
        raise AiProviderError("empty response content")

    text = content.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1).strip()
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise AiProviderError("response is not a JSON object")
        text = text[start:end + 1]

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AiProviderError(f"invalid JSON: {exc.msg} at char {exc.pos}") from exc
    if not isinstance(parsed, dict):
        raise AiProviderError("response JSON is not an object")
    return parsed


def complete_json(
    config: ProviderConfig,
    system_prompt: str,
    user_payload: Dict[str, Any],
) -> Tuple[Dict[str, Any], AiProvenance]:
    """Call /chat/completions and return parsed JSON plus provenance.

    Raises AiProviderError with a sanitized message on any failure.
    """
    request_body = {
        "model": config.model,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(user_payload, ensure_ascii=False, separators=(",", ":")),
            },
        ],
    }
    if os.getenv("AI_JSON_MODE", "").strip().lower() in ("1", "true", "yes"):
        request_body["response_format"] = {"type": "json_object"}

    body = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{config.base_url}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    started = time.monotonic()
    try:
        with open_same_host(request, config.timeout_seconds) as response:
            raw = response.read().decode("utf-8", errors="replace")
            request_id = (
                response.headers.get("x-request-id")
                or response.headers.get("request-id")
                or response.headers.get("cf-ray")
            )
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise AiProviderError(
            f"provider HTTP {exc.code}: {_sanitize_error(detail)}"
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AiProviderError(f"provider unreachable: {_sanitize_error(str(exc))}") from exc

    latency_ms = round((time.monotonic() - started) * 1000.0, 1)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AiProviderError("provider response is not JSON") from exc

    choices = payload.get("choices") or []
    if not choices:
        raise AiProviderError("provider response has no choices")
    choice = choices[0]
    message = choice.get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        finish = choice.get("finish_reason")
        raise AiProviderError(f"provider returned empty content (finish_reason={finish})")

    data = parse_json_content(content)
    usage = payload.get("usage") or {}
    provenance = AiProvenance(
        provider=config.provider,
        model=str(payload.get("model") or config.model),
        endpoint=config.endpoint,
        request_id=request_id,
        latency_ms=latency_ms,
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        total_tokens=usage.get("total_tokens"),
        finish_reason=choice.get("finish_reason"),
        response_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )
    return data, provenance