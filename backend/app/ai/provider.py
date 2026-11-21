"""Small OpenAI-compatible chat client used by the AI explanation layer.

The only provider is ReformBoss. The key is read from REFORMBOSS_API_KEY in
the process environment, or from ~/.omp/agent/.env when the variable is unset.
The key is never logged, stored in provenance or returned to callers.

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
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from backend.app.contracts.schemas import AiProvenance
from backend.app.security.redaction import mask_uri_credentials


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
REFORMBOSS_DEFAULT_MODEL = "deepseek-v4.1-flash-1017"
AGENT_ENV_FILE = Path.home() / ".omp" / "agent" / ".env"

_SECRET_PATTERNS = (
    (re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{8,}"), "***"),
    (re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1***"),
    (re.compile(r'''(?i)\b([\w.-]*(?:key|token|secret|password|passwd|pwd)[\w.-]*\s*(?::\s*[\w.<>?]+\s*)?[:=]\s*)(\"\"\"|\x27\x27\x27)(.*?)\2''', re.S),
     lambda match: match.group(1) + match.group(2) + "***" + "\n" * match.group(3).count("\n") + match.group(2)),
    (re.compile(r"(?i)\b([\w.-]*(?:api[_-]?key|access[_-]?key|token|secret|password|passwd|pwd|db_pass|private[_-]?key)[\w.-]*(?:[\"'])?\s*(?::\s*[\w.<>?]+\s*)?[:=]\s*)([\"'])([^\r\n]*?)\2"), r"\1\2***\2"),
    (re.compile(r"(?i)\b([\w.-]*(?:api[_-]?key|access[_-]?key|token|secret|password|passwd|pwd|db_pass)[\w.-]*\s*[:=]\s*)[^\s,;\"']+"), r"\1***"),
    (re.compile(r"(\$\{[A-Za-z_][A-Za-z0-9_]*:)[^}]+\}"), r"\1****}"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "***"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "***"),
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
        lambda match: "[PRIVATE KEY REDACTED]" + "\n" * match.group(0).count("\n"),
    ),
)


def mask_sensitive_text(text: str) -> str:
    """Best-effort masking for anything that leaves the process."""
    masked = mask_uri_credentials(text or "")
    for pattern, replacement in _SECRET_PATTERNS:
        masked = pattern.sub(replacement, masked)
    return masked


class AiProviderError(RuntimeError):
    """Raised when the remote provider is unreachable or returned bad data."""

    def __init__(
        self,
        message: str,
        request_id: Optional[str] = None,
        latency_ms: float = 0.0,
        finish_reason: Optional[str] = None,
    ):
        super().__init__(message)
        self.request_id = request_id
        self.latency_ms = latency_ms
        self.finish_reason = finish_reason


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


def effective_route(base_url: str) -> str:
    """Normalized non-secret request route: no userinfo, query or fragment."""
    try:
        parts = urllib.parse.urlsplit(base_url or "")
        if not parts.scheme or not parts.hostname:
            return ""
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        return ""
    path = parts.path.rstrip("/")
    return f"{parts.scheme.lower()}://{parts.hostname.lower()}{port}{path}"


def json_mode_enabled() -> bool:
    return os.getenv("AI_JSON_MODE", "").strip().lower() in ("1", "true", "yes")


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


def _read_agent_env_value(name: str) -> str:
    """Read one value from ~/.omp/agent/.env without exposing the file."""
    try:
        lines = AGENT_ENV_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() != name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        return value.strip()
    return ""


def resolve_provider_config() -> Optional[ProviderConfig]:
    """Return ReformBoss config, or None when REFORMBOSS_API_KEY is absent."""
    key = os.getenv("REFORMBOSS_API_KEY", "").strip() or _read_agent_env_value("REFORMBOSS_API_KEY")
    if not key:
        return None

    base_url = os.getenv("AI_BASE_URL", "").strip() or REFORMBOSS_BASE_URL
    model = (
        os.getenv("AI_MODEL", "").strip()
        or os.getenv("LLM_MODEL", "").strip()
        or REFORMBOSS_DEFAULT_MODEL
    )
    return ProviderConfig(
        provider="reformboss",
        base_url=base_url.rstrip("/"),
        api_key=key,
        model=model,
        timeout_seconds=_env_float("AI_TIMEOUT_SECONDS", 30.0, 5.0, 120.0),
        max_tokens=_env_int("AI_MAX_TOKENS", 4096, 256, 8192),
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
    max_tokens: Optional[int] = None,
) -> Tuple[Dict[str, Any], AiProvenance]:
    """Call /chat/completions and return parsed JSON plus provenance.

    Raises AiProviderError with a sanitized message on any failure.
    """
    request_body = {
        "model": config.model,
        "temperature": config.temperature,
        "max_tokens": max_tokens if max_tokens is not None else config.max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(user_payload, ensure_ascii=False, separators=(",", ":")),
            },
        ],
    }
    if json_mode_enabled():
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
        latency_ms = round((time.monotonic() - started) * 1000.0, 1)
        error_request_id = (
            exc.headers.get("x-request-id") if exc.headers else None
        )
        raise AiProviderError(
            f"provider HTTP {exc.code}: {_sanitize_error(detail)}",
            request_id=error_request_id,
            latency_ms=latency_ms,
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        latency_ms = round((time.monotonic() - started) * 1000.0, 1)
        raise AiProviderError(
            f"provider unreachable: {_sanitize_error(str(exc))}",
            latency_ms=latency_ms,
        ) from exc

    latency_ms = round((time.monotonic() - started) * 1000.0, 1)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AiProviderError(
            "provider response is not JSON",
            request_id=request_id,
            latency_ms=latency_ms,
        ) from exc

    choices = payload.get("choices") or []
    if not choices:
        raise AiProviderError(
            "provider response has no choices",
            request_id=request_id,
            latency_ms=latency_ms,
        )
    choice = choices[0]
    finish_reason = choice.get("finish_reason")
    if finish_reason == "length":
        raise AiProviderError(
            f"provider output truncated (finish_reason=length, request_id={request_id})",
            request_id=request_id,
            latency_ms=latency_ms,
            finish_reason=finish_reason,
        )
    message = choice.get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise AiProviderError(
            f"provider returned empty content (finish_reason={finish_reason})",
            request_id=request_id,
            latency_ms=latency_ms,
            finish_reason=finish_reason,
        )

    try:
        data = parse_json_content(content)
    except AiProviderError as exc:
        exc.request_id = request_id
        exc.latency_ms = latency_ms
        exc.finish_reason = finish_reason
        raise
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
        finish_reason=finish_reason,
        response_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )
    return data, provenance
