"""Self-test for the local RAG store.

The checks use the real ``analyzers/examples/bundle.json`` artifact and the
checked-in remediation examples, so they prove that the store works on the same
data the backend will ingest, not on a toy payload.

Run standalone with::

    python -m rag.selftest

``analyzers.selftest`` calls :func:`run_checks` too, so one command covers the
analyzers and the retrieval layer.
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from rag.embed import DIMENSION, Index, spec_id, tokenize
    from rag.store import SpecMismatch, Store, StoreError, WorkspaceMismatch
else:
    from .embed import DIMENSION, Index, spec_id, tokenize
    from .store import SpecMismatch, Store, StoreError, WorkspaceMismatch

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "analyzers" / "examples" / "bundle.json"
EXAMPLES = ROOT / "rag" / "examples" / "fixes.json"

# Values that must never reach the index or any retrieval result.
FORBIDDEN = (
    "SYNTHETIC_",
    "cg26_parts_",
    "cg26_quote_",
    "cg26_depot_",
    "cg26_warranty_",
    "cg26_pickup_",
    "D7v4sB2wJ8kP6mQ9",
    "H9r4Vm2Kb6Qp",
    "qA7mN4kV9rT2sJ6c",
    "mK7xR4bP9wD2qT",
    "Gq7mP8vJdW3sY6zN",
)

SIMILAR_FIX_KEYS = {
    "exampleId",
    "sourceRef",
    "verificationBasis",
    "limitations",
    "embeddingSpec",
    "distance",
    "metric",
    "redactedFix",
}
EMBEDDING_KEYS = {
    "workspaceId",
    "id",
    "status",
    "provider",
    "model",
    "modelVersion",
    "dimension",
    "metric",
    "queryPreparationVersion",
    "documentPreparationVersion",
    "redactionVersion",
}


def _check(condition: bool, message: str, failures: list[str], messages: list[str]) -> None:
    messages.append(message)
    if not condition:
        failures.append(message)


def _is_uuid(value: object) -> bool:
    try:
        uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


def _store_text(path: Path) -> str:
    chunks = []
    for name in ("manifest.json", "events.jsonl", "vectors.jsonl"):
        candidate = path / name
        if candidate.is_file():
            chunks.append(candidate.read_text(encoding="utf-8"))
    return "\n".join(chunks)


def run_checks(failures: list[str], messages: list[str]) -> None:
    """Append RAG checks to the caller's failure/message lists."""

    if not BUNDLE.is_file():
        _check(False, f"rag: bundle не найден: {BUNDLE}", failures, messages)
        return
    if not EXAMPLES.is_file():
        _check(False, f"rag: примеры исправлений не найдены: {EXAMPLES}", failures, messages)
        return

    bundle: dict[str, Any] = json.loads(BUNDLE.read_text(encoding="utf-8"))
    examples_payload: dict[str, Any] = json.loads(EXAMPLES.read_text(encoding="utf-8"))
    workspace = bundle["identity"]["workspaceId"]

    with tempfile.TemporaryDirectory(prefix="cg26-rag-") as temp:
        root = Path(temp)
        store_dir = root / "store"
        store = Store(store_dir)

        first = store.ingest_bundle(bundle)
        _check(
            first["elements"] > 0 and first["new"] == first["elements"],
            "rag: первый ingest создаёт все элементы",
            failures,
            messages,
        )
        _check(
            first["byKind"].get("finding") == 15,
            f"rag: в индексе 15 findings, получено {first['byKind'].get('finding')}",
            failures,
            messages,
        )
        _check(
            (store_dir / "vectors.jsonl").is_file()
            and len((store_dir / "vectors.jsonl").read_text(encoding="utf-8").splitlines()) == first["elements"],
            "rag: векторы сохраняются по одному на элемент",
            failures,
            messages,
        )

        stats = store.stats()
        _check(stats["vectors"] == stats["elements"], "rag: число векторов совпадает с числом элементов", failures, messages)
        _check(stats["staleVectors"] == 0, "rag: нет устаревших векторов после ingest", failures, messages)
        _check(stats["specId"] == spec_id(), "rag: manifest хранит текущий specId", failures, messages)

        # Same input in a different directory must produce the same element ids.
        other = Store(root / "store-copy")
        other.ingest_bundle(bundle)
        _check(set(store.load()) == set(other.load()), "rag: id элементов детерминированы между хранилищами", failures, messages)

        # A second run of the same snapshot reuses every element.
        second_bundle = copy.deepcopy(bundle)
        second_bundle["identity"]["runId"] = str(uuid.uuid5(uuid.NAMESPACE_URL, "cg26-run-2"))
        second_bundle["identity"]["snapshotId"] = str(uuid.uuid5(uuid.NAMESPACE_URL, "cg26-snapshot-2"))
        second = store.ingest_bundle(second_bundle)
        _check(second["new"] == 0 and second["reused"] == second["elements"], "rag: повторный запуск переиспользует все элементы", failures, messages)
        stats = store.stats()
        _check(stats["reusedAcrossRuns"] == stats["elements"], "rag: элементы помечены как переиспользованные между запусками", failures, messages)
        _check(len(stats["runs"]) == 2, "rag: отчёт видит два запуска", failures, messages)
        _check(all("reuseRatio" in entry for entry in stats["runs"].values()), "rag: отчёт содержит reuseRatio по запускам", failures, messages)

        # Search by meaning, not by exact substring.
        found = store.search(
            "jdbc query concatenated value sql injection",
            k=3,
            kinds=("finding",),
            rule_family="sql-injection",
        )
        _check(found["status"] == "available" and bool(found["items"]), "rag: поиск находит sql-injection", failures, messages)
        if found["items"]:
            top = found["items"][0]
            _check(
                top["payload"].get("ruleFamily") == "sql-injection" and top["payload"].get("category") == "SAST",
                "rag: верхний результат относится к sql-injection/SAST",
                failures,
                messages,
            )
            _check(top["reusedAcrossRuns"] is True and top["reuseCount"] >= 1, "rag: результат несёт признак переиспользования", failures, messages)
            _check(top["elementId"].startswith("el:"), "rag: результат ссылается на стабильный element id", failures, messages)

        repeated = store.search(
            "jdbc query concatenated value sql injection",
            k=3,
            kinds=("finding",),
            rule_family="sql-injection",
        )
        _check(
            [item["elementId"] for item in repeated["items"]] == [item["elementId"] for item in found["items"]],
            "rag: повторный запрос даёт тот же порядок",
            failures,
            messages,
        )
        _check(store.search("   ")["status"] == "unavailable", "rag: пустой запрос даёт unavailable", failures, messages)

        # Changed text keeps the element id but replaces its vector.
        changed = copy.deepcopy(bundle)
        changed_finding = changed["scanResults"][0]["findings"][0]
        changed_finding["severityBasis"] = str(changed_finding.get("severityBasis", "")) + " (rechecked)"
        changed["identity"]["runId"] = str(uuid.uuid5(uuid.NAMESPACE_URL, "cg26-run-3"))
        changed["identity"]["snapshotId"] = str(uuid.uuid5(uuid.NAMESPACE_URL, "cg26-snapshot-3"))
        third = store.ingest_bundle(changed)
        _check(third["new"] == 0, "rag: изменение текста не создаёт новый элемент при том же fingerprint", failures, messages)
        stats = store.stats()
        _check(stats["staleVectors"] == 0, "rag: изменённый текст перевекторизован", failures, messages)
        _check(
            len((store_dir / "vectors.jsonl").read_text(encoding="utf-8").splitlines()) == stats["elements"],
            "rag: журнал векторов выводится из журнала элементов, без дублей",
            failures,
            messages,
        )

        # Vectors are sparse, unit-length and deterministic.
        index = Index.build(["sql injection parameterized query", "hostname verifier disabled"])
        sample = index.document_vector("sql injection parameterized query")
        _check(bool(sample), "rag: документ получает непустой вектор", failures, messages)
        _check(
            all(0 <= position < DIMENSION for position in sample),
            "rag: индексы вектора попадают в объявленную размерность",
            failures,
            messages,
        )
        _check(
            abs(sum(value * value for value in sample.values()) - 1.0) < 1e-9,
            "rag: вектор нормализован",
            failures,
            messages,
        )
        _check(
            sample == index.document_vector("sql injection parameterized query"),
            "rag: векторизация детерминирована",
            failures,
            messages,
        )
        _check(
            tokenize("queryForList SELECT") == ["query", "list", "select"],
            "rag: camelCase разбивается на слова",
            failures,
            messages,
        )
        _check(not index.query_vector("полностью неизвестные слова"), "rag: запрос вне словаря даёт пустой вектор", failures, messages)

        # Remediation examples and the contract-shaped result.
        examples = store.ingest_examples(
            examples_payload,
            run_id="examples-selftest",
            workspace_id=workspace,
        )
        _check(examples["examples"] == len(examples_payload["examples"]), "rag: все примеры исправлений проиндексированы", failures, messages)
        _check(examples["byKind"].get("fix") == examples["examples"], "rag: примеры попадают в kind=fix", failures, messages)

        # The contract's vetted status needs real scanner provenance, so a
        # fixture-only example stays draft and the strict default returns nothing.
        strict = store.similar_fixes(
            "unsafe sql string concatenation in jdbc query",
            rule_family="sql-injection",
        )
        _check(
            strict["status"] == "unavailable" and strict["items"] == [],
            "rag: без vetted-примеров строгий фильтр даёт unavailable",
            failures,
            messages,
        )

        fixes = store.similar_fixes(
            "unsafe sql string concatenation in jdbc query",
            k=3,
            rule_family="sql-injection",
            language="kotlin",
            framework="spring-boot",
            statuses=("vetted", "draft"),
        )
        _check(fixes["status"] == "available" and fixes["reason"] is None and bool(fixes["items"]), "rag: similar_fixes возвращает draft-пример при явном допуске", failures, messages)
        if fixes["items"]:
            item = fixes["items"][0]
            _check(set(item) == SIMILAR_FIX_KEYS, "rag: SimilarFix содержит ровно поля контракта", failures, messages)
            _check(_is_uuid(item["exampleId"]), "rag: SimilarFix.exampleId — UUID", failures, messages)
            _check(item["metric"] == "cosine" and isinstance(item["distance"], float), "rag: distance и metric заполнены", failures, messages)
            _check(set(item["embeddingSpec"]) == EMBEDDING_KEYS, "rag: EmbeddingSpec содержит ровно поля контракта", failures, messages)
            _check(item["embeddingSpec"]["status"] == "active", "rag: EmbeddingSpec активен", failures, messages)
            _check(item["embeddingSpec"]["workspaceId"] == workspace, "rag: EmbeddingSpec принадлежит workspace", failures, messages)
            _check(all(item["embeddingSpec"][key] for key in ("provider", "model", "modelVersion", "dimension", "metric")), "rag: EmbeddingSpec заполнен не null", failures, messages)
            _check(bool(item["limitations"]) and bool(item["redactedFix"]), "rag: пример несёт limitations и redactedFix", failures, messages)
            _check("SYNTHETIC_" not in item["redactedFix"], "rag: redactedFix не содержит синтетических секретов", failures, messages)

        missing = store.similar_fixes("anything", rule_family="does-not-exist")
        _check(
            missing["status"] == "unavailable" and bool(missing["reason"]) and missing["items"] == [],
            "rag: неизвестное семейство даёт typed unavailable",
            failures,
            messages,
        )

        # Retrieval baseline: natural-language queries must reach the right rule
        # family.  It is a lexical baseline, so the bar is "almost all", not all.
        probes = [
            ("sql query built by string concatenation from request parameter", "sql-injection"),
            ("hostname verifier that accepts every certificate", "tls-verification"),
            ("cors allows any origin together with credentials", "credentialed-cors"),
            ("export endpoint is public without authentication", "route-authorization"),
            ("read order by id without checking the owner", "object-authorization"),
            ("hardcoded password in source", "password"),
            ("api key committed to the repository", "api-key"),
            ("environment variable falls back to a literal token", "secret-fallback"),
            ("token assembled from two string fragments", "recoverable-token"),
        ]
        hits = 0
        for text, family in probes:
            result = store.search(text, k=1, kinds=("finding",))
            if result["items"] and result["items"][0]["payload"].get("ruleFamily") == family:
                hits += 1
        _check(hits >= 8, f"rag: естественные запросы попадают в своё семейство ({hits}/9)", failures, messages)
        _check(
            store.search("sql injection", k=1, kinds=("finding",))["items"][0]["payload"].get("ruleFamily")
            == "sql-injection",
            "rag: запрос по имени семейства возвращает его первым",
            failures,
            messages,
        )

        # Reindex rebuilds the vector journal without touching elements.
        before_reindex = store.stats()["elements"]
        reindexed = store.reindex()
        _check(reindexed["vectors"] == before_reindex, "rag: reindex восстанавливает векторы из элементов", failures, messages)
        _check(store.stats()["staleVectors"] == 0, "rag: после reindex нет устаревших векторов", failures, messages)

        # No secret value may reach the index.
        text = _store_text(store_dir)
        leaked = [marker for marker in FORBIDDEN if marker in text]
        _check(not leaked, f"rag: в индексе нет значений секретов ({', '.join(leaked)})", failures, messages)

        # Cross-workspace ingestion is an error, not a silent filter.
        alien = copy.deepcopy(bundle)
        alien["identity"]["workspaceId"] = str(uuid.uuid5(uuid.NAMESPACE_URL, "cg26-other-workspace"))
        try:
            store.ingest_bundle(alien)
        except WorkspaceMismatch:
            _check(True, "rag: чужой workspace отклоняется", failures, messages)
        else:
            _check(False, "rag: чужой workspace отклоняется", failures, messages)

        # A stale embedding spec makes retrieval unavailable instead of mixing spaces.
        tampered = root / "store-tampered"
        tampered.mkdir()
        for name in ("events.jsonl", "vectors.jsonl"):
            (tampered / name).write_text((store_dir / name).read_text(encoding="utf-8"), encoding="utf-8")
        manifest = json.loads((store_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest["specId"] = "deadbeefdeadbeef"
        (tampered / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        tampered_store = Store(tampered)
        mismatched = tampered_store.search("sql injection")
        _check(
            mismatched["status"] == "unavailable" and "spec" in str(mismatched["reason"]),
            "rag: несовпадение embedding spec даёт unavailable",
            failures,
            messages,
        )
        try:
            tampered_store.ingest_bundle(bundle)
        except SpecMismatch:
            _check(True, "rag: ingest в хранилище с другим spec отклоняется", failures, messages)
        else:
            _check(False, "rag: ingest в хранилище с другим spec отклоняется", failures, messages)

        # Missing store and missing element are typed errors, not empty success.
        try:
            Store(root / "absent").ingest_examples({"examples": [{"exampleId": "x"}]}, run_id="r", workspace_id=workspace)
        except StoreError:
            _check(True, "rag: неполный пример отклоняется", failures, messages)
        else:
            _check(False, "rag: неполный пример отклоняется", failures, messages)
        _check(Store(root / "absent").get("el:missing") is None, "rag: отсутствующий элемент возвращает None", failures, messages)


def main() -> int:
    failures: list[str] = []
    messages: list[str] = []
    run_checks(failures, messages)
    if failures:
        print("RAG SELFTEST FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print(f"RAG SELFTEST PASSED: {len(messages)} проверок")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
