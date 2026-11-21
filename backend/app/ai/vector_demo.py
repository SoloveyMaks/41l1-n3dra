"""Synthetic demo/fixture support for the optional pgvector retrieval path.

There is no confirmed embedding provider, model, version or dimension in this
project. The demo fixture exists only to prove that storage plus exact
pgvector mechanics work end to end while the real path honestly stays
``unavailable``. It is not a model choice, not scanner provenance, not a
vetted example and not evidence of retrieval quality.

Everything here is explicit:

* one fixed demo spec (provider ``demo-fixture``, model
  ``synthetic-hashed-tokens``, version ``demo/1``, 16 dimensions, cosine);
* a deterministic token-hash vectorizer used for queries and for the stored
  fixture vectors (no network, no external model);
* five draft-only examples under ``demo/fixture:`` source refs.

The SQL side lives in ``db/optional/015_vector_demo_fixture.up.sql``. The
fixture JSON lives in ``db/optional/fixtures/vector_demo_examples.json``.

CLI:
    python -m backend.app.ai.vector_demo --seed --dsn-stdin
    python -m backend.app.ai.vector_demo --verify
    python -m backend.app.ai.vector_demo --verify --dsn-stdin

The DSN is read from ``DATABASE_URL`` / ``VECTOR_RETRIEVAL_DSN`` or from stdin
with ``--dsn-stdin``; it is never accepted as a command-line value and never
printed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

DEMO_WORKSPACE_ID = "887e5059-1fbe-5571-a037-601a3cd67744"
DEMO_SPEC_ID = "84983e62-ec64-5d00-8636-688ab339da82"
DEMO_PROVIDER = "demo-fixture"
DEMO_MODEL = "synthetic-hashed-tokens"
DEMO_MODEL_VERSION = "demo/1"
DEMO_DIMENSION = 16
DEMO_METRIC = "cosine"
DEMO_QUERY_PREPARATION_VERSION = "demo/query/1"
DEMO_DOCUMENT_PREPARATION_VERSION = "demo/document/1"
DEMO_REDACTION_VERSION = "demo/redaction/1"

FIXTURE_PATH = (
    Path(__file__).resolve().parents[3]
    / "db"
    / "optional"
    / "fixtures"
    / "vector_demo_examples.json"
)

_SPEC_KEYS = {
    "embeddingSpecId": DEMO_SPEC_ID,
    "provider": DEMO_PROVIDER,
    "model": DEMO_MODEL,
    "modelVersion": DEMO_MODEL_VERSION,
    "dimension": DEMO_DIMENSION,
    "metric": DEMO_METRIC,
    "queryPreparationVersion": DEMO_QUERY_PREPARATION_VERSION,
    "documentPreparationVersion": DEMO_DOCUMENT_PREPARATION_VERSION,
    "redactionVersion": DEMO_REDACTION_VERSION,
}


class DemoFixtureError(RuntimeError):
    pass


def demo_spec() -> Dict[str, Any]:
    """Return the one demo spec as an explicit dict."""
    return {"workspaceId": DEMO_WORKSPACE_ID, **_SPEC_KEYS}


def embed_text(text: str) -> List[float]:
    """Deterministic 16-dim token-hash vector for the demo fixture only.

    Tokens are lower-cased alphanumeric runs. Each token adds +1 or -1 to one
    hash bucket; the vector is L2-normalised. This is a lexical bucket sketch,
    not a semantic embedding model. It is deliberately boring so the demo
    cannot be mistaken for a provider evaluation.
    """
    buckets = [0.0] * DEMO_DIMENSION
    for token in re.findall(r"[a-z0-9]+", str(text or "").lower()):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % DEMO_DIMENSION
        buckets[index] += 1.0 if digest[4] & 1 else -1.0
    norm = math.sqrt(sum(value * value for value in buckets))
    if norm == 0.0:
        return buckets
    return [value / norm for value in buckets]


def vector_literal(vector: Sequence[float]) -> str:
    if not vector or len(vector) != DEMO_DIMENSION:
        raise DemoFixtureError(
            f"demo query vector must have {DEMO_DIMENSION} dimensions, got {len(vector) if vector else 0}"
        )
    if not all(math.isfinite(float(value)) for value in vector):
        raise DemoFixtureError("demo query vector contains a non-finite value")
    return "[" + ",".join(f"{float(value):.17g}" for value in vector) + "]"


def _norm_uuid(value: Any) -> str:
    return str(value or "").strip().lower()


def spec_mismatches(config: Any) -> List[str]:
    """Return env/config names that do not match the one demo spec exactly."""
    checks: List[Tuple[str, Any, Any]] = [
        ("VECTOR_WORKSPACE_ID", _norm_uuid(config.workspace_id), DEMO_WORKSPACE_ID),
        ("VECTOR_EMBEDDING_SPEC_ID", _norm_uuid(config.embedding_spec_id), DEMO_SPEC_ID),
        ("VECTOR_EMBEDDING_PROVIDER", config.provider, DEMO_PROVIDER),
        ("VECTOR_EMBEDDING_MODEL", config.model, DEMO_MODEL),
        ("VECTOR_EMBEDDING_VERSION", config.version, DEMO_MODEL_VERSION),
        ("VECTOR_EMBEDDING_DIMENSION", config.dimension, DEMO_DIMENSION),
        ("VECTOR_EMBEDDING_METRIC", str(config.metric or "").lower(), DEMO_METRIC),
        (
            "VECTOR_EMBEDDING_QUERY_PREPARATION_VERSION",
            config.query_preparation_version,
            DEMO_QUERY_PREPARATION_VERSION,
        ),
        (
            "VECTOR_EMBEDDING_DOCUMENT_PREPARATION_VERSION",
            config.document_preparation_version,
            DEMO_DOCUMENT_PREPARATION_VERSION,
        ),
        ("VECTOR_EMBEDDING_REDACTION_VERSION", config.redaction_version, DEMO_REDACTION_VERSION),
    ]
    return [name for name, actual, expected in checks if actual != expected]


def load_fixture(path: Optional[Path] = None) -> Dict[str, Any]:
    fixture_path = Path(path) if path is not None else FIXTURE_PATH
    try:
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DemoFixtureError(f"demo fixture not found: {fixture_path}") from exc
    except json.JSONDecodeError as exc:
        raise DemoFixtureError(f"demo fixture is not valid JSON: {exc}") from exc
    validate_fixture(fixture)
    return fixture


def validate_fixture(fixture: Mapping[str, Any]) -> None:
    """Reject a fixture that is not clearly synthetic, draft-only and compatible."""
    if fixture.get("schemaVersion") != "vector-demo/1":
        raise DemoFixtureError("unsupported demo fixture schemaVersion")
    if _norm_uuid(fixture.get("workspaceId")) != DEMO_WORKSPACE_ID:
        raise DemoFixtureError("demo fixture workspace does not match the demo workspace")
    spec = fixture.get("spec")
    if not isinstance(spec, dict):
        raise DemoFixtureError("demo fixture spec is missing")
    for key, expected in _SPEC_KEYS.items():
        actual = spec.get(key)
        if key == "embeddingSpecId":
            actual = _norm_uuid(actual)
            expected = expected.lower()
        if actual != expected:
            raise DemoFixtureError(f"demo fixture spec.{key} must be {expected!r}")

    examples = fixture.get("examples")
    if not isinstance(examples, list) or not (3 <= len(examples) <= 5):
        raise DemoFixtureError("demo fixture must contain 3-5 examples")
    seen: set[str] = set()
    for example in examples:
        if not isinstance(example, dict):
            raise DemoFixtureError("demo fixture example is not an object")
        example_id = _norm_uuid(example.get("exampleId"))
        if not example_id or example_id in seen:
            raise DemoFixtureError("demo fixture example ids must be unique")
        seen.add(example_id)
        if example.get("status") != "draft":
            raise DemoFixtureError("demo fixture examples must be draft-only")
        if not str(example.get("sourceRef", "")).startswith("demo/fixture:"):
            raise DemoFixtureError("demo fixture sourceRef must start with demo/fixture:")
        if not str(example.get("ruleVersion", "")).startswith("demo/"):
            raise DemoFixtureError("demo fixture ruleVersion must start with demo/")
        for key in ("language", "framework", "ruleFamily", "redactedBefore", "redactedFix", "verificationBasis", "documentText"):
            if not str(example.get(key, "")).strip():
                raise DemoFixtureError(f"demo fixture example.{key} must be non-empty")
        limitations = example.get("limitations")
        if not isinstance(limitations, list) or not limitations or not all(str(item).strip() for item in limitations):
            raise DemoFixtureError("demo fixture example.limitations must be a non-empty list")
        embedding = example.get("embedding")
        if not isinstance(embedding, list) or len(embedding) != DEMO_DIMENSION:
            raise DemoFixtureError(f"demo fixture example.embedding must have {DEMO_DIMENSION} dimensions")
        if not all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in embedding):
            raise DemoFixtureError("demo fixture example.embedding contains a non-finite value")
        expected_vector = embed_text(str(example["documentText"]))
        if any(abs(float(a) - b) > 1e-9 for a, b in zip(embedding, expected_vector)):
            raise DemoFixtureError(
                "demo fixture example.embedding does not match embed_text(documentText); regenerate the fixture"
            )


def _connect(dsn: str):
    try:
        import psycopg  # type: ignore
    except ImportError as exc:
        raise DemoFixtureError("psycopg is required for the demo fixture CLI") from exc
    try:
        return psycopg.connect(dsn, connect_timeout=5)
    except Exception as exc:  # never echo the DSN
        raise DemoFixtureError(f"database unavailable ({type(exc).__name__})") from None


def seed(dsn: str, fixture: Mapping[str, Any]) -> Dict[str, Any]:
    """Replace the demo spec/examples with the checked fixture. Demo tables only."""
    spec = fixture["spec"]
    conn = _connect(dsn)
    try:
        conn.autocommit = False
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM data_contract.vector_demo_examples WHERE workspace_id = %s",
                (DEMO_WORKSPACE_ID,),
            )
            cursor.execute(
                "DELETE FROM data_contract.vector_demo_specs WHERE workspace_id = %s",
                (DEMO_WORKSPACE_ID,),
            )
            cursor.execute(
                """
                INSERT INTO data_contract.vector_demo_specs (
                  workspace_id, embedding_spec_id, provider, model, model_version,
                  dimension, metric, query_preparation_version,
                  document_preparation_version, redaction_version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    DEMO_WORKSPACE_ID,
                    DEMO_SPEC_ID,
                    spec["provider"],
                    spec["model"],
                    spec["modelVersion"],
                    spec["dimension"],
                    spec["metric"],
                    spec["queryPreparationVersion"],
                    spec["documentPreparationVersion"],
                    spec["redactionVersion"],
                ),
            )
            for example in fixture["examples"]:
                cursor.execute(
                    """
                    INSERT INTO data_contract.vector_demo_examples (
                      workspace_id, example_id, source_ref, language, framework,
                      rule_family, rule_version, redacted_before, redacted_fix,
                      verification_basis, limitations, status, dimension, embedding
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'draft', %s, %s::public.vector)
                    """,
                    (
                        DEMO_WORKSPACE_ID,
                        _norm_uuid(example["exampleId"]),
                        example["sourceRef"],
                        example["language"],
                        example["framework"],
                        example["ruleFamily"],
                        example["ruleVersion"],
                        example["redactedBefore"],
                        example["redactedFix"],
                        example["verificationBasis"],
                        list(example["limitations"]),
                        DEMO_DIMENSION,
                        vector_literal(example["embedding"]),
                    ),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {
        "status": "seeded",
        "workspaceId": DEMO_WORKSPACE_ID,
        "embeddingSpecId": DEMO_SPEC_ID,
        "examples": len(fixture["examples"]),
        "note": "demo/fixture only; draft-only; not vetted; synthetic vectors",
    }


def _query_demo(
    dsn: str,
    vector: Sequence[float],
    rule_family: str,
    language: str,
    framework: str,
    limit: int = 5,
    status: str = "draft",
) -> List[Tuple[Any, ...]]:
    conn = _connect(dsn)
    try:
        conn.autocommit = True
        with conn.cursor() as cursor:
            cursor.execute("SET statement_timeout = 5000")
            cursor.execute("SET default_transaction_read_only = on")
            cursor.execute(
                """
                SELECT example_id, source_ref, redacted_before, redacted_fix,
                       rule_version, verification_basis, limitations,
                       example_status, distance, metric
                FROM data_contract.exact_demo_fixes(
                  %s, %s, %s::public.vector,
                  %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s)
                """,
                (
                    DEMO_WORKSPACE_ID,
                    DEMO_SPEC_ID,
                    vector_literal(vector),
                    DEMO_PROVIDER,
                    DEMO_MODEL,
                    DEMO_MODEL_VERSION,
                    DEMO_METRIC,
                    DEMO_QUERY_PREPARATION_VERSION,
                    DEMO_DOCUMENT_PREPARATION_VERSION,
                    DEMO_REDACTION_VERSION,
                    language,
                    framework,
                    rule_family,
                    status,
                    limit,
                ),
            )
            return list(cursor.fetchall())
    finally:
        conn.close()


def verify(dsn: str, fixture: Mapping[str, Any]) -> Dict[str, Any]:
    """Run live exact searches for every fixture text and for negative filters."""
    results = []
    failures: List[str] = []
    for example in fixture["examples"]:
        rows = _query_demo(
            dsn,
            embed_text(example["documentText"]),
            example["ruleFamily"],
            example["language"],
            example["framework"],
            limit=3,
        )
        if not rows:
            failures.append(f"{example['sourceRef']}: no rows")
            continue
        first = rows[0]
        if str(first[0]) != _norm_uuid(example["exampleId"]):
            failures.append(f"{example['sourceRef']}: self-query returned {first[0]}")
        if float(first[8]) > 1e-9:
            failures.append(f"{example['sourceRef']}: self-query distance {first[8]} > 1e-9")
        if str(first[7]) != "draft":
            failures.append(f"{example['sourceRef']}: status {first[7]} is not draft")
        results.append(
            {
                "exampleId": str(first[0]),
                "sourceRef": str(first[1]),
                "ruleVersion": str(first[4]),
                "status": str(first[7]),
                "distance": float(first[8]),
                "metric": str(first[9]),
                "limitations": list(first[6] or []),
            }
        )

    # Filters must be real filters, not decoration.
    tls = next(item for item in fixture["examples"] if item["framework"] == "jvm")
    if _query_demo(dsn, embed_text(tls["documentText"]), tls["ruleFamily"], tls["language"], "spring-boot"):
        failures.append("framework filter did not exclude the jvm demo example")
    if _query_demo(dsn, embed_text(tls["documentText"]), "sql-injection", tls["language"], tls["framework"]):
        failures.append("rule-family filter did not exclude the demo example")

    # Vetted must be refused: the demo storage is draft-only.
    vetted_refused = False
    try:
        _query_demo(dsn, embed_text(tls["documentText"]), tls["ruleFamily"], tls["language"], tls["framework"], status="vetted")
    except Exception:
        vetted_refused = True
    if not vetted_refused:
        failures.append("demo retrieval accepted status=vetted")

    return {
        "status": "ok" if not failures else "failed",
        "note": "demo/fixture only; synthetic vectors; not vetted; no retrieval-quality claim",
        "examples": results,
        "failures": failures,
    }


def _dsn(read_stdin: bool) -> str:
    if read_stdin:
        dsn = sys.stdin.readline().strip()
    else:
        dsn = (os.environ.get("VECTOR_RETRIEVAL_DSN") or os.environ.get("DATABASE_URL") or "").strip()
    if not dsn:
        raise DemoFixtureError("DSN is empty; set DATABASE_URL/VECTOR_RETRIEVAL_DSN or pass --dsn-stdin")
    return dsn


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Synthetic pgvector demo fixture (draft-only).")
    parser.add_argument("--seed", action="store_true", help="replace demo tables with the checked fixture")
    parser.add_argument("--verify", action="store_true", help="run live exact searches against the demo tables")
    parser.add_argument("--fixture", type=Path, default=None, help="fixture JSON path")
    parser.add_argument("--dsn-stdin", action="store_true", help="read the DSN from stdin, not argv")
    args = parser.parse_args(argv)
    if not args.seed and not args.verify:
        parser.error("choose --seed and/or --verify")

    try:
        fixture = load_fixture(args.fixture)
        output: Dict[str, Any] = {"fixture": {"examples": len(fixture["examples"]), "spec": fixture["spec"]}}
        if args.seed or args.verify:
            dsn = _dsn(args.dsn_stdin)
            if args.seed:
                output["seed"] = seed(dsn, fixture)
            if args.verify:
                output["verify"] = verify(dsn, fixture)
    except DemoFixtureError as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(output, ensure_ascii=False, indent=2))
    verify_result = output.get("verify")
    return 0 if not verify_result or verify_result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())