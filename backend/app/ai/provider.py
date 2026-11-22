"""Small OpenAI-compatible chat client shared by all AI features.

The default provider is OpenCode Go. An explicit ``AI_PROVIDER=opencode-go``
uses ``OPENCODE_API_KEY`` or the generic ``AI_API_KEY`` and the Go endpoint;
it never falls back to a legacy ReformBoss key or endpoint.

A generic OpenAI-compatible gateway is described only by ``AI_BASE_URL``,
``AI_MODEL`` (or ``LLM_MODEL``) and an optional ``AI_API_KEY``: a local
Ollama/vLLM needs no external credential, and such a gateway never inherits a
ReformBoss key, endpoint or model name. Remote providers (Go, legacy) require
a key; without one resolution fails closed with ``None``. Provenance always
reports the provider that actually served the request. Legacy ReformBoss
remains available only when selected or when only its key is configured.
Keys are never logged, stored in provenance or returned to callers. The
product never opens an external credential database; only process environment
is read, plus the existing legacy agent .env fallback for
``REFORMBOSS_API_KEY`` alone.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
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


OPENCODE_GO_PROVIDER = "opencode-go"
OPENCODE_GO_BASE_URL = "https://opencode.ai/inference/go/v1"
OPENCODE_GO_DEFAULT_MODEL = "deepseek-v4.1-flash"
REFORMBOSS_PROVIDER = "reformboss"
REFORMBOSS_BASE_URL = "https://api.reformboss.com/v1"
REFORMBOSS_DEFAULT_MODEL = "deepseek-v4.1-flash-1017"
LOCAL_PROVIDER = "openai-compatible"
NEDRA_USER_AGENT = "NEDRA/0.1 (+https://nedra.soloveyka.bond)"
OPENCODE_SESSION_HEADER = "x-opencode-session"
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


_YAML_BLOCK_KEY_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<key>[^:#\s][^:#]*)\s*:\s*(?P<style>[|>])[+-]?\d*\s*(?:#.*)?$"
)
_YAML_SECRET_WORD_RE = re.compile(
    r"(?i)(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)"
)


def _indent_width(line: str) -> int:
    width = 0
    for char in line:
        if char == " ":
            width += 1
        elif char == "\t":
            width += 8
        else:
            break
    return width


def mask_yaml_block_scalars(text: str) -> str:
    """Mask YAML block scalars under secret-looking keys.

    ``password: |-`` keeps its value in the following indented lines, which the
    line-oriented secret patterns never see. Mask the whole block up to the
    next line at the same or lower indentation.
    """
    lines = text.split("\n")
    out = []
    index = 0
    while index < len(lines):
        line = lines[index]
        match = _YAML_BLOCK_KEY_RE.match(line)
        if not match or not _YAML_SECRET_WORD_RE.search(match.group("key")):
            out.append(line)
            index += 1
            continue
        indent = _indent_width(match.group("indent"))
        out.append(f'{match.group("indent")}{match.group("key")}: "***"')
        index += 1
        while index < len(lines):
            candidate = lines[index]
            if candidate.strip() and _indent_width(candidate) <= indent:
                break
            out.append("" if not candidate.strip() else f"{match.group('indent')}  ***")
            index += 1
    return "\n".join(out)


def mask_sensitive_text(text: str) -> str:
    """Best-effort masking for anything that leaves the process."""
    masked = mask_yaml_block_scalars(text or "")
    masked = mask_uri_credentials(masked)
    for pattern, replacement in _SECRET_PATTERNS:
        masked = pattern.sub(replacement, masked)
    return masked


def safe_metadata(value: Any, limit: int = 128) -> str:
    """Mask and bound external response metadata before it is published.

    Request ids, model names and finish reasons come from the remote answer,
    so they are masked like everything else that leaves the process and cut
    to a sane length: a hostile endpoint must not be able to smuggle data
    into provenance, reasons or the official JSON.
    """
    masked = mask_sensitive_text(str(value) if value is not None else "")
    return masked.strip()[:limit]


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
    """Legacy-only fallback from ~/.omp/agent/.env.

    Only ``REFORMBOSS_API_KEY`` is looked up here. OpenCode Go and local keys
    are process environment only; no credential database or auth store is
    opened by the product.
    """
    if name != "REFORMBOSS_API_KEY":
        return ""
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


def _provider_alias(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")


def new_session_id(scope: str = "ai", *parts: str) -> str:
    """Return an opaque stable session id for one logical AI operation.

    Callers create it once and pass it to every batch/retry of that operation.
    Stable parts (for example a run UUID) are hashed; no user identity or
    credential is put into the header. With no parts the id is random.
    """
    safe_scope = _provider_alias(scope) or "ai"
    if parts:
        digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
        return f"nedra-{safe_scope}-{digest}"
    return f"nedra-{safe_scope}-{secrets.token_hex(12)}"


def _model_override() -> str:
    return os.getenv("AI_MODEL", "").strip() or os.getenv("LLM_MODEL", "").strip()


def _base_override() -> str:
    return os.getenv("AI_BASE_URL", "").strip().rstrip("/")


def _is_reformboss_endpoint(url: str) -> bool:
    try:
        host = urllib.parse.urlsplit(url).hostname
    except ValueError:
        return False
    return (host or "").lower() == "api.reformboss.com"


def resolve_provider_config() -> Optional[ProviderConfig]:
    """Resolve an explicit provider, OpenCode Go, a generic OpenAI-compatible
    gateway or legacy ReformBoss.

    Explicit ``AI_PROVIDER`` wins. A generic gateway is described only by
    ``AI_BASE_URL`` + ``AI_MODEL``/``LLM_MODEL`` with an optional
    ``AI_API_KEY``: a keyless local Ollama/vLLM resolves as
    ``openai-compatible`` and never borrows a ReformBoss key, endpoint or
    model name. Remote providers (``opencode-go``, ``reformboss``) require a
    key; without one this returns ``None`` instead of guessing another
    provider's secret. With no provider selection, a Go credential wins, then
    a generic key (with ``AI_BASE_URL`` a generic gateway, otherwise Go), then
    a legacy key, then a keyless generic gateway when both ``AI_BASE_URL``
    and ``AI_MODEL``/``LLM_MODEL`` are set.
    """
    generic_key = os.getenv("AI_API_KEY", "").strip()
    opencode_key = os.getenv("OPENCODE_API_KEY", "").strip()
    reform_key = os.getenv("REFORMBOSS_API_KEY", "").strip()
    explicit = _provider_alias(os.getenv("AI_PROVIDER", ""))
    base_override = _base_override()
    model_override = _model_override()

    go_key = generic_key or opencode_key
    # The generic key is an explicit override for legacy too; the provider
    # specific legacy key remains the backward-compatible fallback.
    legacy_key = generic_key or reform_key or _read_agent_env_value("REFORMBOSS_API_KEY")

    go_aliases = {"opencode-go", "opencode", "go"}
    legacy_aliases = {"reformboss", "reform-boss", "legacy"}
    local_aliases = {"openai-compatible", "openai", "local", "custom", "openai-api"}

    provider = ""
    key = ""
    base_url = ""
    default_model = ""

    if explicit:
        if explicit in go_aliases:
            provider, key, default_model = OPENCODE_GO_PROVIDER, go_key, OPENCODE_GO_DEFAULT_MODEL
            base_url = base_override or OPENCODE_GO_BASE_URL
        elif explicit in legacy_aliases:
            provider, key, default_model = REFORMBOSS_PROVIDER, legacy_key, REFORMBOSS_DEFAULT_MODEL
            base_url = base_override or REFORMBOSS_BASE_URL
        else:
            # Any other declared name (openai-compatible, ollama, vllm, ...)
            # is an operator-declared gateway: base URL and model must be
            # given explicitly, the key is optional (local gateways often need
            # none) and no other provider's endpoint, model or credential is
            # ever reused.
            provider = LOCAL_PROVIDER if explicit in local_aliases else explicit
            key, base_url, default_model = generic_key, base_override, ""
    elif opencode_key:
        provider, key, default_model = OPENCODE_GO_PROVIDER, go_key, OPENCODE_GO_DEFAULT_MODEL
        base_url = base_override or OPENCODE_GO_BASE_URL
    elif generic_key and base_override:
        provider, key, base_url, default_model = (
            LOCAL_PROVIDER, generic_key, base_override, "",
        )
    elif generic_key:
        provider, key, default_model = OPENCODE_GO_PROVIDER, generic_key, OPENCODE_GO_DEFAULT_MODEL
        base_url = base_override or OPENCODE_GO_BASE_URL
    elif legacy_key:
        provider, key, default_model = REFORMBOSS_PROVIDER, legacy_key, REFORMBOSS_DEFAULT_MODEL
        base_url = base_override or REFORMBOSS_BASE_URL
    elif base_override and model_override:
        # Keyless local gateway (Ollama/vLLM): no external credential needed.
        provider, key, base_url, default_model = (
            LOCAL_PROVIDER, "", base_override, "",
        )

    if not provider or not base_url:
        return None

    # Remote providers fail closed without a credential: never reuse another
    # provider's key and never claim success with an unauthenticated guess.
    if provider in (OPENCODE_GO_PROVIDER, REFORMBOSS_PROVIDER) and not key:
        return None

    # Explicit Go must not inherit a legacy ReformBoss endpoint from a stale
    # AI_BASE_URL. A different local/proxy override remains allowed.
    if provider == OPENCODE_GO_PROVIDER and _is_reformboss_endpoint(base_url):
        base_url = OPENCODE_GO_BASE_URL

    # A generic/local OpenAI-compatible gateway has no safe built-in model.
    # Require AI_MODEL/LLM_MODEL there; Go and legacy keep their defaults.
    known_default = provider in {OPENCODE_GO_PROVIDER, REFORMBOSS_PROVIDER}
    model = model_override or (default_model if known_default else "")
    if not model:
        return None

    return ProviderConfig(
        provider=provider,
        base_url=base_url,
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
    *,
    session_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], AiProvenance]:
    """Call /chat/completions and return parsed JSON plus provenance.

    Go requests carry NEDRA's own User-Agent and a stable opaque
    x-opencode-session header. Callers should create one session_id per
    logical operation and reuse it for all batches/retries.
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
    session = session_id or new_session_id("ai")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": NEDRA_USER_AGENT,
    }
    if config.api_key:
        # A keyless local gateway (Ollama/vLLM) is a valid configuration:
        # no Authorization header is sent instead of an empty Bearer token.
        headers["Authorization"] = f"Bearer {config.api_key}"
    if config.provider == OPENCODE_GO_PROVIDER:
        headers[OPENCODE_SESSION_HEADER] = session
    request = urllib.request.Request(
        f"{config.base_url}/chat/completions",
        data=body,
        headers=headers,
        method="POST",
    )

    started = time.monotonic()
    try:
        with open_same_host(request, config.timeout_seconds) as response:
            raw = response.read().decode("utf-8", errors="replace")
            request_id = safe_metadata(
                response.headers.get("x-request-id")
                or response.headers.get("request-id")
                or response.headers.get("cf-ray")
            ) or None
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        latency_ms = round((time.monotonic() - started) * 1000.0, 1)
        error_request_id = safe_metadata(
            exc.headers.get("x-request-id") if exc.headers else None
        ) or None
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
    finish_reason = safe_metadata(choice.get("finish_reason"), 64) or None
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
        model=safe_metadata(payload.get("model") or config.model),
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
