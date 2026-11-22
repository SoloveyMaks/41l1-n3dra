"""Optional exact pgvector retrieval for vetted remediation examples.

The SQL contract lives in db/optional/003_exact_vector.up.sql. This module is
deliberately fail-open: any missing config, missing driver, provider failure or
database error becomes a typed `unavailable` result and never raises into the
main analysis.

The only network hop is ``EmbeddingsAdapter`` (``POST /embeddings``): it
returns a vector solely when the answer provably belongs to the configured
spec — same model, configured dimension, finite values — and carries
request-level provenance (provider, model, dimension, metric, route, latency).
A mismatch is a typed error, never a close-enough vector and never a silent
success. With ``VECTOR_RETRIEVAL_ENABLED=0`` nothing is executed and the
status stays ``disabled`` with no invented embedding facts.

Configuration is env-only (see docs/ai-runtime.md). The code never logs the
query text, the embedding vector or the API key.
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from backend.app.ai import vector_demo
from backend.app.ai.provider import (
    effective_route,
    mask_sensitive_text,
    open_same_host,
    safe_metadata,
)
from backend.app.ai.vector_result import VectorExample
from backend.app.codefacts.identity import current_workspace_scope
from backend.app.contracts.schemas import Finding, RetrievalInfo, SimilarFix

_ALLOWED_METRICS = {"cosine", "l2", "inner_product"}


def _workspace_key(value: str) -> str:
    """Canonical UUID text for comparison; empty when the value is not a UUID."""
    try:
        return str(uuid.UUID(str(value).strip()))
    except (ValueError, AttributeError, TypeError):
        return ""


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(low, min(high, value))


def _env_optional_int(name: str, low: int, high: int) -> Optional[int]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    if value < low or value > high:
        return None
    return value


def _env_float(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(low, min(high, value))


def _env_json_map(name: str) -> Dict[str, str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return {}
    try:
        if not raw.startswith("{"):
            return {}
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): str(v) for k, v in parsed.items()}


@dataclass(frozen=True)
class VectorConfig:
    enabled: bool = False
    dsn: str = ""
    workspace_id: str = ""
    embedding_spec_id: str = ""
    provider: str = ""
    model: str = ""
    version: str = ""
    dimension: Optional[int] = None
    metric: str = ""
    query_preparation_version: str = ""
    document_preparation_version: str = ""
    redaction_version: str = ""
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_timeout_seconds: float = 15.0
    limit: int = 3
    max_findings: int = 3
    language: str = "kotlin"
    framework: str = "spring-boot"
    rule_family_map: Dict[str, str] = field(default_factory=dict)
    status: str = "vetted"
    demo_fixture: bool = False

    @property
    def missing(self) -> List[str]:
        required = {
            "VECTOR_RETRIEVAL_DSN": self.dsn,
            "VECTOR_WORKSPACE_ID": self.workspace_id,
            "VECTOR_EMBEDDING_SPEC_ID": self.embedding_spec_id,
            "VECTOR_EMBEDDING_PROVIDER": self.provider,
            "VECTOR_EMBEDDING_MODEL": self.model,
            "VECTOR_EMBEDDING_VERSION": self.version,
            "VECTOR_EMBEDDING_QUERY_PREPARATION_VERSION": self.query_preparation_version,
            "VECTOR_EMBEDDING_DOCUMENT_PREPARATION_VERSION": self.document_preparation_version,
            "VECTOR_EMBEDDING_REDACTION_VERSION": self.redaction_version,
        }
        if not self.demo_fixture:
            required["VECTOR_EMBEDDING_BASE_URL"] = self.embedding_base_url
            required["VECTOR_EMBEDDING_API_KEY"] = self.embedding_api_key
        missing = [name for name, value in required.items() if not value]
        if self.metric not in _ALLOWED_METRICS:
            missing.append("VECTOR_EMBEDDING_METRIC")
        if self.dimension is None or self.dimension <= 0:
            missing.append("VECTOR_EMBEDDING_DIMENSION")
        if self.status not in ("vetted", "draft"):
            missing.append("VECTOR_RETRIEVAL_STATUS")
        elif self.demo_fixture:
            if self.status != "draft":
                missing.append("VECTOR_RETRIEVAL_STATUS=draft")
            missing.extend(vector_demo.spec_mismatches(self))
        elif self.status != "vetted":
            missing.append("VECTOR_RETRIEVAL_STATUS=vetted")
        return list(dict.fromkeys(missing))


def resolve_vector_config() -> VectorConfig:
    return VectorConfig(
        enabled=_env_bool("VECTOR_RETRIEVAL_ENABLED", False),
        dsn=(os.getenv("VECTOR_RETRIEVAL_DSN") or os.getenv("DATABASE_URL") or "").strip(),
        workspace_id=os.getenv("VECTOR_WORKSPACE_ID", "").strip(),
        embedding_spec_id=os.getenv("VECTOR_EMBEDDING_SPEC_ID", "").strip(),
        provider=os.getenv("VECTOR_EMBEDDING_PROVIDER", "").strip(),
        model=os.getenv("VECTOR_EMBEDDING_MODEL", "").strip(),
        version=os.getenv("VECTOR_EMBEDDING_VERSION", "").strip(),
        dimension=_env_optional_int("VECTOR_EMBEDDING_DIMENSION", 1, 8192),
        metric=os.getenv("VECTOR_EMBEDDING_METRIC", "").strip().lower(),
        query_preparation_version=os.getenv("VECTOR_EMBEDDING_QUERY_PREPARATION_VERSION", "").strip(),
        document_preparation_version=os.getenv("VECTOR_EMBEDDING_DOCUMENT_PREPARATION_VERSION", "").strip(),
        redaction_version=os.getenv("VECTOR_EMBEDDING_REDACTION_VERSION", "").strip(),
        embedding_base_url=os.getenv("VECTOR_EMBEDDING_BASE_URL", "").strip().rstrip("/"),
        embedding_api_key=os.getenv("VECTOR_EMBEDDING_API_KEY", "").strip(),
        embedding_timeout_seconds=_env_float("VECTOR_EMBEDDING_TIMEOUT_SECONDS", 15.0, 3.0, 120.0),
        limit=_env_int("VECTOR_RETRIEVAL_LIMIT", 3, 1, 20),
        max_findings=_env_int("VECTOR_RETRIEVAL_MAX_FINDINGS", 3, 1, 50),
        language=os.getenv("VECTOR_RETRIEVAL_LANGUAGE", "kotlin").strip() or "kotlin",
        framework=os.getenv("VECTOR_RETRIEVAL_FRAMEWORK", "spring-boot").strip() or "spring-boot",
        rule_family_map=_env_json_map("VECTOR_RULE_FAMILY_MAP"),
        status=os.getenv("VECTOR_RETRIEVAL_STATUS", "vetted").strip().lower() or "vetted",
        demo_fixture=_env_bool("VECTOR_RETRIEVAL_DEMO_FIXTURE", False),
    )


def _sanitize(text: str, limit: int = 300) -> str:
    return mask_sensitive_text(str(text or ""))[:limit]


@dataclass(frozen=True)
class EmbeddingProvenance:
    """Request-level provenance of a vector that actually came from the endpoint."""

    provider: str
    model: str
    dimension: int
    metric: str
    endpoint: str
    latency_ms: float
    request_id: Optional[str] = None

    def summary(self) -> str:
        text = (
            f"embedding {self.provider}/{self.model} dim={self.dimension} "
            f"metric={self.metric} via {self.endpoint} in {self.latency_ms}ms"
        )
        if self.request_id:
            text += f" request_id={self.request_id}"
        return text


class EmbeddingsAdapter:
    """OpenAI-compatible ``/embeddings`` client bound to one embedding spec.

    This is the only place the vetted path talks to an embedding endpoint.
    The configured spec (provider, model, dimension, metric) is the claim; a
    vector is returned only when the response provably belongs to it: same
    model, exactly the configured dimension, finite values. A mismatch raises
    a bounded, masked error that becomes retrieval ``unavailable`` — never a
    close-enough vector and never a silent success. The API key is optional
    only in the sense that an empty configured key simply omits the
    ``Authorization`` header; the caller decides whether a key is required.
    """

    def __init__(self, config: VectorConfig):
        self.config = config

    def embed(self, text: str) -> Tuple[List[float], EmbeddingProvenance]:
        cfg = self.config
        if not cfg.embedding_base_url:
            raise RuntimeError("embedding endpoint is not configured")
        if not cfg.model:
            raise RuntimeError("embedding model is not configured")
        if cfg.dimension is None or cfg.dimension <= 0:
            raise RuntimeError("embedding dimension is not configured")

        body = json.dumps({"model": cfg.model, "input": text}, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if cfg.embedding_api_key:
            headers["Authorization"] = f"Bearer {cfg.embedding_api_key}"
        request = urllib.request.Request(
            f"{cfg.embedding_base_url}/embeddings",
            data=body,
            headers=headers,
            method="POST",
        )

        started = time.monotonic()
        try:
            with open_same_host(request, cfg.embedding_timeout_seconds) as response:
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
            raise RuntimeError(f"embedding HTTP {exc.code}: {_sanitize(detail)}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"embedding endpoint unreachable: {_sanitize(exc)}") from exc

        latency_ms = round((time.monotonic() - started) * 1000.0, 1)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("embedding response is not JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("embedding response is not a JSON object")

        data = payload.get("data")
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            raise RuntimeError("embedding response has no data")
        embedding = data[0].get("embedding")
        if not isinstance(embedding, list) or not embedding:
            raise RuntimeError("embedding response has no vector")

        returned_model = payload.get("model")
        if not isinstance(returned_model, str) or not returned_model.strip():
            # No model in the answer means the vector cannot be attributed to
            # the configured spec; provenance must stay unconfirmed.
            raise RuntimeError("embedding response has no model")
        if returned_model != cfg.model:
            raise RuntimeError(
                f"embedding response model {safe_metadata(returned_model, 80)!r} does not "
                f"match configured {safe_metadata(cfg.model, 80)!r}"
            )
        try:
            vector = [float(value) for value in embedding]
        except (TypeError, ValueError) as exc:
            raise RuntimeError("embedding response contains a non-numeric value") from exc
        if not all(math.isfinite(value) for value in vector):
            raise RuntimeError("embedding response contains a non-finite value")
        if len(vector) != cfg.dimension:
            raise RuntimeError(
                f"embedding dimension {len(vector)} does not match configured {cfg.dimension}"
            )

        provenance = EmbeddingProvenance(
            provider=cfg.provider,
            model=cfg.model,
            dimension=len(vector),
            metric=cfg.metric,
            endpoint=effective_route(cfg.embedding_base_url),
            latency_ms=latency_ms,
            request_id=request_id,
        )
        return vector, provenance


def _provenance_text(provenances: List[EmbeddingProvenance]) -> str:
    return "; ".join(dict.fromkeys(provenance.summary() for provenance in provenances))


class VectorRuntime:
    """Thin, fail-open wrapper around exact_similar_fixes()."""

    def __init__(self, config: Optional[VectorConfig] = None):
        self.config = config or resolve_vector_config()
        self._embeddings = EmbeddingsAdapter(self.config)

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def status(self) -> RetrievalInfo:
        cfg = self.config
        info = RetrievalInfo(
            status="disabled",
            reason="optional exact retrieval is off (VECTOR_RETRIEVAL_ENABLED=0)",
            embeddingModel=cfg.model,
            embeddingVersion=cfg.version,
            dimension=cfg.dimension,
            metric=cfg.metric,
            embeddingSpecId=cfg.embedding_spec_id or None,
        )
        if not cfg.enabled:
            return info
        missing = cfg.missing
        if missing:
            info.status = "unavailable"
            info.reason = "retrieval unavailable: missing or invalid " + ", ".join(missing)
            return info
        scope_violation = self._scope_violation()
        if scope_violation:
            info.status = "unavailable"
            info.reason = scope_violation
            return info
        if cfg.demo_fixture:
            info.status = "ready"
            info.reason = (
                "demo fixture configured (synthetic hash vectors, draft-only, not vetted); "
                "retrieval not executed"
            )
            return info
        info.status = "ready"
        info.reason = f"retrieval configured (not executed); embedding provider {cfg.provider}"
        return info

    def _scope_violation(self) -> Optional[str]:
        """Return why the configured corpus is not authorized for this request.

        Retrieval has no independent authorization: it only runs inside an
        explicit request/background workspace scope, and the configured corpus
        workspace must be that exact workspace. A process-wide env workspace is
        configuration, never proof of session ownership.
        """
        scoped = current_workspace_scope()
        if not scoped:
            return "retrieval unavailable: no authorized workspace scope for this request"
        scoped_key = _workspace_key(scoped)
        configured_key = _workspace_key(self.config.workspace_id)
        if not scoped_key:
            return "retrieval unavailable: authorized workspace scope is not a UUID"
        if not configured_key:
            return "retrieval unavailable: configured corpus workspace is not a UUID"
        if scoped_key != configured_key:
            return (
                "retrieval unavailable: configured corpus workspace does not match "
                "the authorized request workspace"
            )
        return None

    def _scoped_workspace(self) -> str:
        scoped = current_workspace_scope()
        return _workspace_key(scoped or "")

    def retrieve_for_findings(
        self,
        findings: List[Finding],
        limit: Optional[int] = None,
    ) -> Tuple[RetrievalInfo, List[SimilarFix]]:
        """Legacy DTO for existing callers. Never raises."""
        info, examples = self._collect(findings, limit)
        return info, [self._as_similar_fix(example) for example in examples]

    def retrieve_examples_for_findings(
        self,
        findings: List[Finding],
        limit: Optional[int] = None,
    ) -> Tuple[RetrievalInfo, List[VectorExample]]:
        """Full row: source, redacted before/fix, rule version, status, limits."""
        return self._collect(findings, limit)

    def _collect(
        self,
        findings: List[Finding],
        limit: Optional[int],
    ) -> Tuple[RetrievalInfo, List[VectorExample]]:
        """Return a typed status and any matching examples. Never raises."""
        info = self.status()
        if info.status in ("disabled", "unavailable"):
            return info, []
        workspace = self._scoped_workspace()
        if not workspace or workspace != _workspace_key(self.config.workspace_id):
            info.status = "unavailable"
            info.reason = self._scope_violation() or "retrieval unavailable: workspace scope changed"
            return info, []
        if _load_pg_driver() is None:
            info.status = "unavailable"
            info.reason = "retrieval unavailable: psycopg/psycopg2 is not installed"
            return info, []
        if not findings:
            info.status = "unavailable"
            info.reason = "retrieval skipped: no findings"
            return info, []

        result_limit = limit if limit is not None else self.config.limit
        collected: List[VectorExample] = []
        failures: List[str] = []
        provenances: List[EmbeddingProvenance] = []
        successes = 0

        for finding in findings[: self.config.max_findings]:
            try:
                status, items, reason, provenance = self._retrieve_one(
                    finding, result_limit, workspace
                )
            except Exception as exc:  # fail-open by design
                status, items, reason, provenance = (
                    "unavailable", [], f"retrieval error: {_sanitize(exc)}", None,
                )
            if status == "ok":
                successes += 1
                collected.extend(items)
                if provenance is not None:
                    provenances.append(provenance)
            else:
                failures.append(reason or "retrieval unavailable")

        if successes == 0:
            info.status = "unavailable"
            info.reason = "; ".join(dict.fromkeys(failures))[:600] or "retrieval unavailable"
            return info, []
        if failures:
            info.status = "partial"
            prefix = (
                "demo fixture (synthetic vectors, draft-only, not vetted): "
                if self.config.demo_fixture
                else (
                    "draft-only retrieval (examples are not vetted): "
                    if self.config.status == "draft"
                    else ""
                )
            )
            info.reason = (prefix + "; ".join(dict.fromkeys(failures)))[:600]
        else:
            info.status = "ok"
            if self.config.demo_fixture:
                detail = (
                    f"{len(collected)} matching demo examples"
                    if collected
                    else "no matching demo examples"
                )
                info.reason = (
                    "demo fixture (synthetic vectors, draft-only, not vetted): " + detail
                )
            elif self.config.status == "draft":
                detail = (
                    f"{len(collected)} matching draft examples (not vetted)"
                    if collected
                    else "no matching draft examples"
                )
                info.reason = "draft-only retrieval: " + detail
            else:
                info.reason = "exact retrieval completed" if collected else "no matching vetted examples"
        if provenances:
            info.reason = f"{info.reason}; {_provenance_text(provenances)}"
        return info, collected

    def _retrieve_one(
        self,
        finding: Finding,
        limit: int,
        workspace: str,
    ) -> Tuple[str, List[VectorExample], str, Optional[EmbeddingProvenance]]:
        cfg = self.config
        query_text = self._query_text(finding)
        if not query_text:
            return "unavailable", [], "empty query text", None

        provenance: Optional[EmbeddingProvenance] = None
        try:
            if cfg.demo_fixture:
                vector = vector_demo.embed_text(query_text)
            else:
                vector, provenance = self._embeddings.embed(query_text)
        except Exception as exc:
            return "unavailable", [], f"embedding unavailable: {_sanitize(exc)}", None

        if cfg.dimension is None or len(vector) != cfg.dimension:
            return (
                "unavailable",
                [],
                f"query dimension {len(vector)} does not match configured {cfg.dimension}",
                None,
            )

        rule_family = cfg.rule_family_map.get(finding.ruleId, finding.ruleId)
        function = "exact_demo_fixes" if cfg.demo_fixture else "exact_similar_fixes"
        try:
            rows = self._query_db(workspace, vector, rule_family, limit, function)
        except Exception as exc:
            return "unavailable", [], f"database unavailable: {_sanitize(exc)}", None

        fixes: List[VectorExample] = []
        for row in rows:
            try:
                limitations = row[6]
                if isinstance(limitations, str):
                    limitations = [limitations]
                elif limitations is None:
                    limitations = []
                example_status = str(row[7])
                if example_status not in ("draft", "vetted"):
                    return (
                        "unavailable",
                        [],
                        f"unexpected example status {example_status!r}",
                        None,
                    )
                fixes.append(
                    VectorExample(
                        findingId=finding.id,
                        exampleId=str(row[0]),
                        sourceRef=str(row[1]),
                        redactedBefore=str(row[2]),
                        redactedFix=str(row[3]),
                        ruleVersion=str(row[4]),
                        verificationBasis=str(row[5] or ""),
                        limitations=[str(x) for x in limitations],
                        status=example_status,
                        distance=float(row[8]),
                        metric=str(row[9]),
                        embeddingSpecId=cfg.embedding_spec_id,
                    )
                )
            except (TypeError, ValueError, IndexError) as exc:
                return "unavailable", [], f"unexpected retrieval row: {_sanitize(exc)}", None
        return "ok", fixes, "", provenance

    @staticmethod
    def _as_similar_fix(example: VectorExample) -> SimilarFix:
        """Map the full row onto the shared DTO without dropping or inferring status."""
        return SimilarFix(
            findingId=example.findingId,
            exampleId=example.exampleId,
            sourceRef=example.sourceRef,
            redactedBefore=example.redactedBefore,
            redactedFix=example.redactedFix,
            ruleVersion=example.ruleVersion,
            verificationBasis=example.verificationBasis,
            limitations=list(example.limitations),
            status=example.status,
            distance=example.distance,
            metric=example.metric,
            embeddingSpecId=example.embeddingSpecId,
        )

    def _query_text(self, finding: Finding) -> str:
        evidence = mask_sensitive_text(finding.redactedEvidence or "")
        parts = [finding.ruleId, finding.severity.value, evidence]
        return " ".join(part for part in parts if part).strip()[:2000]

    def _query_db(
        self,
        workspace: str,
        vector: List[float],
        rule_family: str,
        limit: int,
        function: str,
    ) -> List[Tuple[Any, ...]]:
        cfg = self.config
        if function not in ("exact_similar_fixes", "exact_demo_fixes"):
            raise RuntimeError("unsupported retrieval function")
        driver = _load_pg_driver()
        if driver is None:
            raise RuntimeError("psycopg/psycopg2 is not installed")
        vector_literal = "[" + ",".join(f"{value:.17g}" for value in vector) + "]"
        sql = (
            "SELECT example_id, source_ref, redacted_before, redacted_fix, "
            "rule_version, verification_basis, limitations, example_status, "
            "distance, metric "
            f"FROM data_contract.{function}("
            "%s, %s, %s::public.vector, %s, %s, %s, %s, %s, %s, %s, "
            "%s, %s, %s, %s, %s)"
        )
        params = (
            workspace,
            cfg.embedding_spec_id,
            vector_literal,
            cfg.provider,
            cfg.model,
            cfg.version,
            cfg.metric,
            cfg.query_preparation_version,
            cfg.document_preparation_version,
            cfg.redaction_version,
            cfg.language,
            cfg.framework,
            rule_family,
            cfg.status,
            limit,
        )

        if driver == "psycopg":
            import psycopg  # type: ignore

            conn = psycopg.connect(cfg.dsn, connect_timeout=5)
        else:
            import psycopg2  # type: ignore

            conn = psycopg2.connect(cfg.dsn, connect_timeout=5)

        try:
            try:
                conn.autocommit = True
            except Exception:
                pass
            with conn.cursor() as cursor:
                cursor.execute("SET statement_timeout = 5000")
                cursor.execute("SET default_transaction_read_only = on")
                cursor.execute(sql, params)
                return list(cursor.fetchall())
        finally:
            try:
                conn.close()
            except Exception:
                pass


def _load_pg_driver() -> Optional[str]:
    try:
        import psycopg  # noqa: F401

        return "psycopg"
    except ImportError:
        pass
    try:
        import psycopg2  # noqa: F401

        return "psycopg2"
    except ImportError:
        return None
