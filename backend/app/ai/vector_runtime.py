"""Optional exact pgvector retrieval for vetted remediation examples.

The SQL contract lives in db/optional/003_exact_vector.up.sql. This module is
deliberately fail-open: any missing config, missing driver, provider failure or
database error becomes a typed `unavailable` result and never raises into the
main analysis.

Configuration is env-only (see .env.example and docs/ai-runtime.md). The code
never logs the query text, the embedding vector or the API key.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from backend.app.ai.provider import mask_sensitive_text, open_same_host
from backend.app.contracts.schemas import Finding, RetrievalInfo, SimilarFix

_ALLOWED_METRICS = {"cosine", "l2", "inner_product"}
_DEFAULT_MODEL = "text-embedding-3-small"
_DEFAULT_VERSION = "2024-02"
_DEFAULT_DIMENSION = 1536
_DEFAULT_METRIC = "cosine"


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
    model: str = _DEFAULT_MODEL
    version: str = _DEFAULT_VERSION
    dimension: int = _DEFAULT_DIMENSION
    metric: str = _DEFAULT_METRIC
    query_preparation_version: str = "1"
    document_preparation_version: str = "1"
    redaction_version: str = "1"
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_timeout_seconds: float = 15.0
    limit: int = 3
    max_findings: int = 3
    language: str = "kotlin"
    framework: str = "spring-boot"
    rule_family_map: Dict[str, str] = field(default_factory=dict)

    @property
    def missing(self) -> List[str]:
        required = {
            "VECTOR_RETRIEVAL_DSN": self.dsn,
            "VECTOR_WORKSPACE_ID": self.workspace_id,
            "VECTOR_EMBEDDING_SPEC_ID": self.embedding_spec_id,
            "VECTOR_EMBEDDING_BASE_URL": self.embedding_base_url,
            "VECTOR_EMBEDDING_API_KEY": self.embedding_api_key,
        }
        missing = [name for name, value in required.items() if not value]
        if self.metric not in _ALLOWED_METRICS:
            missing.append("VECTOR_EMBEDDING_METRIC")
        if self.dimension <= 0:
            missing.append("VECTOR_EMBEDDING_DIMENSION")
        return missing


def resolve_vector_config() -> VectorConfig:
    return VectorConfig(
        enabled=_env_bool("VECTOR_RETRIEVAL_ENABLED", False),
        dsn=(os.getenv("VECTOR_RETRIEVAL_DSN") or os.getenv("DATABASE_URL") or "").strip(),
        workspace_id=os.getenv("VECTOR_WORKSPACE_ID", "").strip(),
        embedding_spec_id=os.getenv("VECTOR_EMBEDDING_SPEC_ID", "").strip(),
        model=os.getenv("VECTOR_EMBEDDING_MODEL", "").strip() or _DEFAULT_MODEL,
        version=os.getenv("VECTOR_EMBEDDING_VERSION", "").strip() or _DEFAULT_VERSION,
        dimension=_env_int("VECTOR_EMBEDDING_DIMENSION", _DEFAULT_DIMENSION, 1, 8192),
        metric=os.getenv("VECTOR_EMBEDDING_METRIC", "").strip().lower() or _DEFAULT_METRIC,
        query_preparation_version=os.getenv("VECTOR_EMBEDDING_QUERY_PREPARATION_VERSION", "1").strip() or "1",
        document_preparation_version=os.getenv("VECTOR_EMBEDDING_DOCUMENT_PREPARATION_VERSION", "1").strip() or "1",
        redaction_version=os.getenv("VECTOR_EMBEDDING_REDACTION_VERSION", "1").strip() or "1",
        embedding_base_url=os.getenv("VECTOR_EMBEDDING_BASE_URL", "").strip().rstrip("/"),
        embedding_api_key=os.getenv("VECTOR_EMBEDDING_API_KEY", "").strip(),
        embedding_timeout_seconds=_env_float("VECTOR_EMBEDDING_TIMEOUT_SECONDS", 15.0, 3.0, 120.0),
        limit=_env_int("VECTOR_RETRIEVAL_LIMIT", 3, 1, 20),
        max_findings=_env_int("VECTOR_RETRIEVAL_MAX_FINDINGS", 3, 1, 50),
        language=os.getenv("VECTOR_RETRIEVAL_LANGUAGE", "kotlin").strip() or "kotlin",
        framework=os.getenv("VECTOR_RETRIEVAL_FRAMEWORK", "spring-boot").strip() or "spring-boot",
        rule_family_map=_env_json_map("VECTOR_RULE_FAMILY_MAP"),
    )


def _sanitize(text: str, limit: int = 300) -> str:
    return mask_sensitive_text(str(text or ""))[:limit]


class VectorRuntime:
    """Thin, fail-open wrapper around exact_similar_fixes()."""

    def __init__(self, config: Optional[VectorConfig] = None):
        self.config = config or resolve_vector_config()

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
        info.status = "ready"
        info.reason = "retrieval configured (not executed)"
        return info

    def retrieve_for_findings(
        self,
        findings: List[Finding],
        limit: Optional[int] = None,
    ) -> Tuple[RetrievalInfo, List[SimilarFix]]:
        """Return a typed status and any similar fixes. Never raises."""
        info = self.status()
        if info.status in ("disabled", "unavailable"):
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
        collected: List[SimilarFix] = []
        failures: List[str] = []
        successes = 0

        for finding in findings[: self.config.max_findings]:
            try:
                status, items, reason = self._retrieve_one(finding, result_limit)
            except Exception as exc:  # fail-open by design
                status, items, reason = "unavailable", [], f"retrieval error: {_sanitize(exc)}"
            if status == "ok":
                successes += 1
                collected.extend(items)
            else:
                failures.append(reason or "retrieval unavailable")

        if successes == 0:
            info.status = "unavailable"
            info.reason = "; ".join(dict.fromkeys(failures))[:600] or "retrieval unavailable"
            return info, []
        if failures:
            info.status = "partial"
            info.reason = "; ".join(dict.fromkeys(failures))[:600]
        else:
            info.status = "ok"
            info.reason = "exact retrieval completed" if collected else "no matching vetted examples"
        return info, collected

    def _retrieve_one(
        self,
        finding: Finding,
        limit: int,
    ) -> Tuple[str, List[SimilarFix], str]:
        cfg = self.config
        query_text = self._query_text(finding)
        if not query_text:
            return "unavailable", [], "empty query text"

        try:
            vector = self._embed_query(query_text)
        except Exception as exc:
            return "unavailable", [], f"embedding unavailable: {_sanitize(exc)}"

        if len(vector) != cfg.dimension:
            return (
                "unavailable",
                [],
                f"query dimension {len(vector)} does not match configured {cfg.dimension}",
            )

        rule_family = cfg.rule_family_map.get(finding.ruleId, finding.ruleId)
        try:
            rows = self._query_db(vector, rule_family, limit)
        except Exception as exc:
            return "unavailable", [], f"database unavailable: {_sanitize(exc)}"

        fixes: List[SimilarFix] = []
        for row in rows:
            try:
                limitations = row[3]
                if isinstance(limitations, str):
                    limitations = [limitations]
                elif limitations is None:
                    limitations = []
                fixes.append(
                    SimilarFix(
                        findingId=finding.id,
                        exampleId=str(row[0]),
                        sourceRef=str(row[1]),
                        verificationBasis=str(row[2]),
                        limitations=[str(x) for x in limitations],
                        redactedFix=str(row[4]),
                        distance=float(row[5]),
                        metric=str(row[6]),
                        embeddingSpecId=cfg.embedding_spec_id,
                    )
                )
            except (TypeError, ValueError, IndexError) as exc:
                return "unavailable", [], f"unexpected retrieval row: {_sanitize(exc)}"
        return "ok", fixes, ""

    def _query_text(self, finding: Finding) -> str:
        evidence = mask_sensitive_text(finding.redactedEvidence or "")
        parts = [finding.ruleId, finding.severity.value, evidence]
        return " ".join(part for part in parts if part).strip()[:2000]

    def _embed_query(self, text: str) -> List[float]:
        cfg = self.config
        body = json.dumps({"model": cfg.model, "input": text}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{cfg.embedding_base_url}/embeddings",
            data=body,
            headers={
                "Authorization": f"Bearer {cfg.embedding_api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with open_same_host(request, cfg.embedding_timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise RuntimeError(f"embedding HTTP {exc.code}: {_sanitize(detail)}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"embedding endpoint unreachable: {_sanitize(exc)}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError("embedding response is not JSON") from exc

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or not data:
            raise RuntimeError("embedding response has no data")
        embedding = data[0].get("embedding") if isinstance(data[0], dict) else None
        if not isinstance(embedding, list) or not embedding:
            raise RuntimeError("embedding response has no vector")
        return [float(value) for value in embedding]

    def _query_db(self, vector: List[float], rule_family: str, limit: int) -> List[Tuple[Any, ...]]:
        cfg = self.config
        driver = _load_pg_driver()
        if driver is None:
            raise RuntimeError("psycopg/psycopg2 is not installed")
        vector_literal = "[" + ",".join(f"{value:.9g}" for value in vector) + "]"
        sql = (
            "SELECT example_id, source_ref, verification_basis, limitations, "
            "redacted_fix, distance, metric "
            "FROM data_contract.exact_similar_fixes("
            "%s, %s, %s::public.vector, %s, %s, %s, %s)"
        )
        params = (
            cfg.workspace_id,
            cfg.embedding_spec_id,
            vector_literal,
            cfg.language,
            cfg.framework,
            rule_family,
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