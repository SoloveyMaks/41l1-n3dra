"""Document + vector store for analysis elements.

The project does not use a relational database.  Elements live in an
append-only JSONL journal next to a small manifest; vectors live in a derived
JSONL journal that is rebuilt whenever the element corpus changes.

There is no per-run row: every element has a content-derived id (finding
fingerprint, node symbol, evidence hash, example id), so scanning the same
project again records another run reference instead of creating a duplicate.

What "reuse" means here:

* the same element keeps the same id across runs;
* ingesting it again increments ``seen`` and adds the run to ``runs``;
* a search returns that id, so a map node, an explanation or a verified fix can
  be attached to it once and reused;
* vectors are derived from the declared embedding spec and the corpus, never
  from a per-run random seed, so two runs of the same code are interchangeable.

The store is scoped to one workspace on purpose: cross-workspace similarity is
forbidden by the contract, so mixing two workspaces is an error, not a filter.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .embed import (
    DIMENSION,
    METRIC,
    Index,
    Query,
    Vector,
    cosine_distance,
    prepare_query,
    spec,
    spec_id,
    tokenize,
)

SCHEMA_VERSION = "1"
PRODUCER_VERSION = "rag/0.1.0"

KINDS = ("finding", "node", "edge", "evidence", "fix")
FIX_STATUSES = ("draft", "vetted", "retired")

# Retrieval vocabulary, not detection logic: the analyzers already decided what
# a finding is.  These words only help a natural-language query ("public export
# endpoint") meet a keyword-shaped finding or fix.  They are our own wording, no
# third-party rule set.
CONCEPT_TAGS = {
    "sql-injection": "sql query string concatenation interpolation prepared statement parameter binding jdbc",
    "tls-verification": "tls https certificate hostname verifier trust all insecure ssl connection",
    "credentialed-cors": "cors cross origin wildcard credentials allowed origin browser",
    "route-authorization": "authorization route endpoint public unauthenticated permit all access control",
    "object-authorization": "object ownership owner check authorization identifier enumeration access control",
    "password": "password credential hardcoded literal secret source",
    "api-key": "api key token hardcoded literal secret source",
    "secret-fallback": "secret fallback default value environment variable getenv env elvis missing token",
    "recoverable-token": "token composed fragments reconstruct secret parts",
    "configuration-token": "configuration token embedded secret settings",
    "SCA": "vulnerable dependency version advisory cve library build",
}


def _concept_tags(payload: dict[str, Any]) -> str:
    family = str(payload.get("ruleFamily") or "")
    category = str(payload.get("category") or "")
    return CONCEPT_TAGS.get(family) or CONCEPT_TAGS.get(category) or ""


class StoreError(RuntimeError):
    """Base error for store operations."""


class SpecMismatch(StoreError):
    """The store was built with a different embedding spec."""


class WorkspaceMismatch(StoreError):
    """The store already belongs to another workspace."""


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def text_hash(text: str) -> str:
    return _sha(text)[:32]


def element_id(kind: str, identity: str) -> str:
    return "el:" + _sha(f"{kind}|{identity}")[:24]


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _uuid5(value: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, value))


@dataclass
class Element:
    id: str
    kind: str
    identity: str
    text: str
    payload: dict[str, Any]
    first_run: str
    last_run: str
    runs: dict[str, dict[str, Any]] = field(default_factory=dict)
    seen: int = 0

    @property
    def reuse_count(self) -> int:
        """How many times the element was seen after the run that created it."""

        return max(0, self.seen - 1)

    @property
    def reused_across_runs(self) -> bool:
        return len(self.runs) > 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "identity": self.identity,
            "text": self.text,
            "payload": self.payload,
            "firstRun": self.first_run,
            "lastRun": self.last_run,
            "runs": self.runs,
            "seen": self.seen,
            "reuseCount": self.reuse_count,
            "reusedAcrossRuns": self.reused_across_runs,
        }


# --------------------------------------------------------------- text builders


def _snippet(evidence_by_id: dict[str, dict[str, Any]], refs: Iterable[str]) -> str:
    for ref in refs:
        evidence = evidence_by_id.get(ref)
        if evidence:
            return str(evidence.get("redactedSnippet") or "")
    return ""


def _finding_text(finding: dict[str, Any], evidence_by_id: dict[str, dict[str, Any]]) -> str:
    location = finding.get("primaryLocation") or {}
    return " ".join(
        part
        for part in (
            "finding",
            str(finding.get("category", "")),
            str(finding.get("ruleFamily", "")),
            str(finding.get("severity", "")),
            str(finding.get("status", "")),
            str((finding.get("anchor") or {}).get("symbolKey", "")),
            str(location.get("path", "")),
            str(finding.get("severityBasis", "")),
            str(finding.get("confidenceBasis", "")),
            _snippet(evidence_by_id, [finding.get("primaryEvidenceId", "")]),
            _concept_tags(finding),
        )
        if part
    )


def _node_text(node: dict[str, Any], evidence_by_id: dict[str, dict[str, Any]]) -> str:
    return " ".join(
        part
        for part in (
            "node",
            str(node.get("kind", "")),
            str(node.get("symbolKey", "")),
            str(node.get("label", "")),
            _snippet(evidence_by_id, node.get("evidenceRefs") or []),
        )
        if part
    )


def _edge_text(edge: dict[str, Any], nodes: dict[str, dict[str, Any]], evidence_by_id: dict[str, dict[str, Any]]) -> str:
    source = nodes.get(edge.get("fromId", ""), {})
    target = nodes.get(edge.get("toId", ""), {})
    return " ".join(
        part
        for part in (
            "edge",
            str(edge.get("kind", "")),
            str(edge.get("resolution", "")),
            str(source.get("symbolKey", "")),
            str(target.get("symbolKey", "")),
            str(edge.get("resolutionReason", "")),
            _snippet(evidence_by_id, edge.get("evidenceRefs") or []),
        )
        if part
    )


def _evidence_text(evidence: dict[str, Any]) -> str:
    location = evidence.get("location") or {}
    return " ".join(
        part
        for part in (
            "evidence",
            str(evidence.get("kind", "")),
            str(location.get("path", "")),
            str(evidence.get("redactedSnippet", "")),
        )
        if part
    )


def _fix_text(example: dict[str, Any]) -> str:
    return " ".join(
        part
        for part in (
            "fix",
            str(example.get("category", "")),
            str(example.get("ruleFamily", "")),
            str(example.get("language", "")),
            str(example.get("framework", "")),
            str(example.get("description", "")),
            str(example.get("redactedFix", "")),
            str(example.get("verificationBasis", "")),
            " ".join(example.get("limitations") or []),
            _concept_tags(example),
        )
        if part
    )


# ---------------------------------------------------------------------- store


class Store:
    """Append-only element journal, derived vector journal and a manifest."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.manifest_path = self.path / "manifest.json"
        self.events_path = self.path / "events.jsonl"
        self.vectors_path = self.path / "vectors.jsonl"
        self._elements: dict[str, Element] | None = None
        self._manifest: dict[str, Any] | None = None
        self._vectors: dict[str, tuple[str, Vector]] | None = None
        self._index: Index | None = None

    # -- manifest ---------------------------------------------------------

    @property
    def manifest(self) -> dict[str, Any]:
        if self._manifest is None:
            if self.manifest_path.is_file():
                self._manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            else:
                self._manifest = {}
        return self._manifest

    def _ensure_manifest(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        self._check_manifest(workspace_id)
        self.path.mkdir(parents=True, exist_ok=True)
        if not self.manifest:
            self._manifest = {
                "schemaVersion": SCHEMA_VERSION,
                "producerVersion": PRODUCER_VERSION,
                "workspaceId": workspace_id,
                "embeddingSpec": spec(),
                "specId": spec_id(),
                "createdAt": _timestamp(),
                "firstRunId": run_id,
            }
            self._write_json(self.manifest_path, self._manifest)
            return self._manifest
        return self.manifest

    def _check_manifest(self, workspace_id: str) -> None:
        if not self.manifest:
            return
        stored_spec = self.manifest.get("embeddingSpec") or {}
        if self.manifest.get("specId") != spec_id() or stored_spec != spec():
            raise SpecMismatch(
                "store uses embedding spec "
                f"{self.manifest.get('specId')}, current code uses {spec_id()}; "
                "reindex into a new store instead of mixing vectors"
            )
        if self.manifest.get("workspaceId") != workspace_id:
            raise WorkspaceMismatch(
                "store belongs to another workspace; cross-workspace search is not allowed"
            )

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not path.is_file():
            return rows
        with path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as error:
                    raise StoreError(f"{path.name}:{line_number}: {error}") from error
        return rows

    # -- loading ----------------------------------------------------------

    def load(self) -> dict[str, Element]:
        if self._elements is not None:
            return self._elements
        elements: dict[str, Element] = {}
        for event in self._read_jsonl(self.events_path):
            if event.get("op") != "upsert":
                continue
            self._apply(elements, event)
        self._elements = elements
        return elements

    @staticmethod
    def _apply(elements: dict[str, Element], event: dict[str, Any]) -> None:
        identifier = event["id"]
        run_id = event["runId"]
        snapshot_id = event.get("snapshotId", "")
        seen_at = event.get("ts", "")
        element = elements.get(identifier)
        if element is None:
            element = Element(
                id=identifier,
                kind=event["kind"],
                identity=event["identity"],
                text=event["text"],
                payload=event["payload"],
                first_run=run_id,
                last_run=run_id,
            )
            elements[identifier] = element
        element.seen += 1
        element.last_run = run_id
        entry = element.runs.setdefault(run_id, {"snapshotId": snapshot_id, "count": 0, "lastSeenAt": seen_at})
        entry["count"] = int(entry.get("count", 0)) + 1
        entry["lastSeenAt"] = seen_at
        # The newest redacted text wins; identity keeps the element stable.
        element.text = event["text"]
        element.payload = event["payload"]

    def _load_vectors(self) -> dict[str, tuple[str, Vector]]:
        if self._vectors is not None:
            return self._vectors
        vectors: dict[str, tuple[str, Vector]] = {}
        for row in self._read_jsonl(self.vectors_path):
            if row.get("specId") != self.manifest.get("specId"):
                continue
            indices = row.get("indices")
            values = row.get("values")
            if not isinstance(indices, list) or not isinstance(values, list) or len(indices) != len(values):
                continue
            identifier = str(row.get("id") or "")
            digest = str(row.get("textHash") or "")
            if not identifier or not digest:
                continue
            vector: Vector = {}
            for index, value in zip(indices, values):
                if not isinstance(index, int) or not 0 <= index < DIMENSION:
                    vector = {}
                    break
                vector[index] = float(value)
            if vector:
                vectors[identifier] = (digest, vector)
        self._vectors = vectors
        return vectors

    # -- indexing ---------------------------------------------------------

    def index(self) -> Index:
        """Corpus statistics for the current element set."""

        if self._index is None:
            self._index = Index.build(element.text for element in self.load().values())
        return self._index

    def _rebuild_vectors(self) -> dict[str, Any]:
        """Rebuild the derived vector journal from the element journal."""

        elements = self.load()
        index = self.index()
        rows = []
        for identifier in sorted(elements):
            element = elements[identifier]
            vector = index.document_vector(element.text)
            rows.append(
                {
                    "id": identifier,
                    "specId": index.specId,
                    "corpusHash": index.corpus_hash,
                    "textHash": text_hash(element.text),
                    "indices": sorted(vector),
                    "values": [round(vector[key], 8) for key in sorted(vector)],
                }
            )
        self.path.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=self.path, prefix="vectors.", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                for row in rows:
                    stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            os.replace(temporary, self.vectors_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        self._vectors = None
        return {
            "vectors": len(rows),
            "specId": index.specId,
            "corpusHash": index.corpus_hash,
            "dimension": DIMENSION,
        }

    def reindex(self) -> dict[str, Any]:
        """Rebuild the vector journal from the element journal."""

        return self._rebuild_vectors()

    # -- ingest -----------------------------------------------------------

    def ingest_bundle(self, bundle: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
        identity = bundle.get("identity") or {}
        workspace_id = identity.get("workspaceId")
        run_id = identity.get("runId")
        snapshot_id = identity.get("snapshotId")
        if not workspace_id or not run_id or not snapshot_id:
            raise StoreError("bundle identity must contain workspaceId, runId and snapshotId")

        self._check_manifest(workspace_id)
        before = set(self.load())
        events = list(self._bundle_events(bundle))
        new = [event for event in events if event["id"] not in before]
        if dry_run:
            return {
                "dryRun": True,
                "runId": run_id,
                "elements": len(events),
                "new": len(new),
                "reused": len(events) - len(new),
                "byKind": _count_by_kind(events),
            }

        self._ensure_manifest(workspace_id, run_id)
        self._append(events)
        return {
            "runId": run_id,
            "snapshotId": snapshot_id,
            "elements": len(events),
            "new": len(new),
            "reused": len(events) - len(new),
            "byKind": _count_by_kind(events),
            "store": str(self.path),
        }

    def _bundle_events(self, bundle: dict[str, Any]) -> Iterable[dict[str, Any]]:
        identity = bundle.get("identity") or {}
        run_id = identity.get("runId", "")
        snapshot_id = identity.get("snapshotId", "")
        timestamp = _timestamp()

        code_facts = bundle.get("codeFacts") or {}
        nodes = {node.get("id"): node for node in code_facts.get("nodes") or []}
        evidence_by_id: dict[str, dict[str, Any]] = {}
        for result in bundle.get("scanResults") or []:
            for evidence in result.get("evidence") or []:
                evidence_by_id[evidence.get("id", "")] = evidence
        for evidence in code_facts.get("evidence") or []:
            evidence_by_id.setdefault(evidence.get("id", ""), evidence)

        emitted: set[str] = set()

        def emit(kind: str, key: str, text: str, payload: dict[str, Any]) -> dict[str, Any] | None:
            identifier = element_id(kind, key)
            if identifier in emitted:
                return None
            emitted.add(identifier)
            return {
                "op": "upsert",
                "id": identifier,
                "kind": kind,
                "identity": key,
                "text": text,
                "payload": payload,
                "runId": run_id,
                "snapshotId": snapshot_id,
                "ts": timestamp,
            }

        for result in bundle.get("scanResults") or []:
            for finding in result.get("findings") or []:
                anchor = finding.get("anchor") or {}
                location = finding.get("primaryLocation") or {}
                event = emit(
                    "finding",
                    f"{finding.get('category')}|{finding.get('fingerprint')}",
                    _finding_text(finding, evidence_by_id),
                    {
                        "category": finding.get("category"),
                        "ruleId": finding.get("ruleId"),
                        "ruleFamily": finding.get("ruleFamily"),
                        "severity": finding.get("severity"),
                        "status": finding.get("status"),
                        "symbolKey": anchor.get("symbolKey"),
                        "role": anchor.get("role"),
                        "path": location.get("path"),
                        "line": location.get("line"),
                        "endLine": location.get("endLine"),
                        "snippet": _snippet(evidence_by_id, [finding.get("primaryEvidenceId", "")]),
                        "fingerprint": finding.get("fingerprint"),
                        "advisoryIds": finding.get("advisoryIds") or [],
                    },
                )
                if event:
                    yield event

        for node in nodes.values():
            event = emit(
                "node",
                f"{node.get('kind')}|{node.get('symbolKey')}|{_path_of(node, evidence_by_id)}",
                _node_text(node, evidence_by_id),
                {
                    "kind": node.get("kind"),
                    "symbolKey": node.get("symbolKey"),
                    "label": node.get("label"),
                    "path": _path_of(node, evidence_by_id),
                    "snippet": _snippet(evidence_by_id, node.get("evidenceRefs") or []),
                },
            )
            if event:
                yield event

        for edge in code_facts.get("edges") or []:
            source = nodes.get(edge.get("fromId", ""), {})
            target = nodes.get(edge.get("toId", ""), {})
            event = emit(
                "edge",
                f"{edge.get('kind')}|{source.get('symbolKey')}|{target.get('symbolKey')}",
                _edge_text(edge, nodes, evidence_by_id),
                {
                    "kind": edge.get("kind"),
                    "resolution": edge.get("resolution"),
                    "fromSymbol": source.get("symbolKey"),
                    "toSymbol": target.get("symbolKey"),
                    "reason": edge.get("resolutionReason"),
                },
            )
            if event:
                yield event

        for evidence in evidence_by_id.values():
            location = evidence.get("location") or {}
            event = emit(
                "evidence",
                f"{evidence.get('kind')}|{location.get('path')}|{evidence.get('sourceHash')}|{location.get('line')}|{location.get('endLine')}",
                _evidence_text(evidence),
                {
                    "kind": evidence.get("kind"),
                    "path": location.get("path"),
                    "line": location.get("line"),
                    "endLine": location.get("endLine"),
                    "snippet": evidence.get("redactedSnippet"),
                    "sourceHash": evidence.get("sourceHash"),
                },
            )
            if event:
                yield event

    def ingest_examples(self, payload: dict[str, Any], *, run_id: str, workspace_id: str, dry_run: bool = False) -> dict[str, Any]:
        self._check_manifest(workspace_id)
        examples = payload.get("examples") or []
        if not isinstance(examples, list):
            raise StoreError("examples payload must contain an examples array")
        before = set(self.load())
        timestamp = _timestamp()
        events: list[dict[str, Any]] = []
        for example in examples:
            example_id = str(example.get("exampleId") or "").strip()
            if not example_id:
                raise StoreError("every remediation example needs exampleId")
            status = str(example.get("status") or "draft")
            if status not in FIX_STATUSES:
                raise StoreError(f"example {example_id}: status must be one of {FIX_STATUSES}")
            for key in ("sourceRef", "verificationBasis", "redactedFix"):
                if not str(example.get(key) or "").strip():
                    raise StoreError(f"example {example_id}: {key} must be a non-empty string")
            limitations = example.get("limitations") or []
            if not isinstance(limitations, list) or any(not str(item).strip() for item in limitations):
                raise StoreError(f"example {example_id}: limitations must be a list of non-empty strings")
            events.append(
                {
                    "op": "upsert",
                    "id": element_id("fix", example_id),
                    "kind": "fix",
                    "identity": example_id,
                    "text": _fix_text(example),
                    "payload": {
                        "exampleId": example_id if _is_uuid(example_id) else _uuid5(f"cg26-example:{example_id}"),
                        "externalId": example_id,
                        "category": example.get("category"),
                        "ruleFamily": example.get("ruleFamily"),
                        "language": example.get("language"),
                        "framework": example.get("framework"),
                        "status": status,
                        "sourceRef": example.get("sourceRef"),
                        "verificationBasis": example.get("verificationBasis"),
                        "limitations": limitations,
                        "redactedFix": example.get("redactedFix"),
                        "description": example.get("description"),
                    },
                    "runId": run_id,
                    "snapshotId": "examples",
                    "ts": timestamp,
                }
            )
        new = [event for event in events if event["id"] not in before]
        if not dry_run:
            self._ensure_manifest(workspace_id, run_id)
            self._append(events)
        return {
            "examples": len(events),
            "new": len(new),
            "reused": len(events) - len(new),
            "byKind": _count_by_kind(events),
        }

    def _append(self, events: list[dict[str, Any]]) -> None:
        """Append events, then rebuild the derived vectors."""

        self.path.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8", newline="\n") as stream:
            for event in events:
                stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        self._elements = None
        self._index = None
        self._rebuild_vectors()

    # -- search -----------------------------------------------------------

    def _vector_for(self, element: Element, index: Index) -> Vector:
        digest = text_hash(element.text)
        stored = self._load_vectors().get(element.id)
        if stored and stored[0] == digest:
            return stored[1]
        return index.document_vector(element.text)

    def search(
        self,
        text: str,
        *,
        k: int = 5,
        kinds: Iterable[str] | None = None,
        category: str | None = None,
        rule_family: str | None = None,
        language: str | None = None,
        framework: str | None = None,
        statuses: Iterable[str] | None = None,
        max_distance: float | None = None,
    ) -> dict[str, Any]:
        elements = self.load()
        manifest = self.manifest
        if manifest and manifest.get("specId") != spec_id():
            return {
                "status": "unavailable",
                "reason": "embedding spec mismatch: reindex the store",
                "items": [],
            }
        if not elements:
            return {"status": "unavailable", "reason": "store is empty", "items": []}
        if not text.strip():
            return {"status": "unavailable", "reason": "empty query", "items": []}

        allowed_kinds = set(kinds) if kinds else set(KINDS)
        allowed_statuses = set(statuses) if statuses else None
        index = self.index()
        query: Query = prepare_query(text, index)
        if not query.vector:
            return {
                "status": "unavailable",
                "reason": "query has no term in the index vocabulary",
                "items": [],
            }
        scored: list[tuple[float, Element]] = []
        for element in elements.values():
            if element.kind not in allowed_kinds:
                continue
            if category and element.payload.get("category") != category:
                continue
            if rule_family and element.payload.get("ruleFamily") != rule_family:
                continue
            if language and element.payload.get("language") != language:
                continue
            if framework and element.payload.get("framework") != framework:
                continue
            if allowed_statuses is not None and element.kind == "fix" and element.payload.get("status") not in allowed_statuses:
                continue
            distance = cosine_distance(query.vector, self._vector_for(element, index))
            if max_distance is not None and distance > max_distance:
                continue
            scored.append((distance, element))
        scored.sort(key=lambda item: (item[0], item[1].id))
        items = [
            {
                "elementId": element.id,
                "kind": element.kind,
                "distance": round(distance, 6),
                "metric": METRIC,
                "reusedAcrossRuns": element.reused_across_runs,
                "reuseCount": element.reuse_count,
                "payload": element.payload,
            }
            for distance, element in scored[: max(0, k)]
        ]
        return {
            "status": "available",
            "reason": None,
            "query": query.text,
            "specId": query.specId,
            "items": items,
        }

    def similar_fixes(
        self,
        text: str,
        *,
        k: int = 5,
        rule_family: str | None = None,
        language: str | None = None,
        framework: str | None = None,
        statuses: Iterable[str] = ("vetted",),
    ) -> dict[str, Any]:
        """Return the contract-shaped ``SimilarFixesResult``.

        Workspace is implicit in the store; ruleFamily/language/framework are
        applied as exact filters, as the data contract requires.  ``distance`` is
        exact cosine distance (``1 - cos``), not a probability, and it does not
        upgrade a fix to "verified".
        """

        result = self.search(
            text,
            k=k,
            kinds=("fix",),
            rule_family=rule_family,
            language=language,
            framework=framework,
            statuses=statuses,
        )
        if result["status"] != "available":
            return {"status": "unavailable", "reason": result["reason"] or "retrieval unavailable", "items": []}
        items = []
        for item in result["items"]:
            payload = item["payload"]
            if language and payload.get("language") != language:
                continue
            if framework and payload.get("framework") != framework:
                continue
            items.append(
                {
                    "exampleId": payload.get("exampleId"),
                    "status": payload.get("status"),
                    "sourceRef": payload.get("sourceRef"),
                    "verificationBasis": payload.get("verificationBasis"),
                    "limitations": payload.get("limitations") or [],
                    "embeddingSpec": {
                        "workspaceId": self.manifest.get("workspaceId"),
                        "id": _uuid5(f"cg26-embedding-spec:{spec_id()}"),
                        "status": "active",
                        **spec(),
                    },
                    "distance": item["distance"],
                    "metric": METRIC,
                    "redactedFix": payload.get("redactedFix"),
                }
            )
        if not items:
            return {"status": "unavailable", "reason": "no fix matches the requested status and scope filters", "items": []}
        return {"status": "available", "reason": None, "items": items}

    # -- reporting --------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        elements = self.load()
        by_kind: dict[str, int] = {kind: 0 for kind in KINDS}
        for element in elements.values():
            by_kind[element.kind] = by_kind.get(element.kind, 0) + 1
        runs: dict[str, dict[str, Any]] = {}
        for element in elements.values():
            for run_id in element.runs:
                entry = runs.setdefault(run_id, {"elements": 0, "reused": 0})
                entry["elements"] += 1
                if element.first_run != run_id:
                    entry["reused"] += 1
        for entry in runs.values():
            total = entry["elements"] or 1
            entry["reuseRatio"] = round(entry["reused"] / total, 4)
        index = self.index()
        vectors = self._load_vectors()
        stale = sum(
            1
            for element in elements.values()
            if element.id not in vectors or vectors[element.id][0] != text_hash(element.text)
        )
        return {
            "store": str(self.path),
            "schemaVersion": self.manifest.get("schemaVersion"),
            "specId": self.manifest.get("specId"),
            "workspaceId": self.manifest.get("workspaceId"),
            "embeddingSpec": self.manifest.get("embeddingSpec"),
            "corpusHash": index.corpus_hash,
            "vocabulary": len(index.idf),
            "elements": len(elements),
            "vectors": len(vectors),
            "staleVectors": stale,
            "byKind": by_kind,
            "reusedAcrossRuns": sum(1 for element in elements.values() if element.reused_across_runs),
            "runs": dict(sorted(runs.items())),
        }

    def reuse_report(self) -> dict[str, Any]:
        stats = self.stats()
        return {
            "store": stats["store"],
            "runs": stats["runs"],
            "reusedAcrossRuns": stats["reusedAcrossRuns"],
            "elements": stats["elements"],
            "vectors": stats["vectors"],
            "staleVectors": stats["staleVectors"],
        }

    def get(self, identifier: str) -> dict[str, Any] | None:
        element = self.load().get(identifier)
        return element.to_dict() if element else None


# ------------------------------------------------------------------ utilities


def _timestamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _path_of(node: dict[str, Any], evidence_by_id: dict[str, dict[str, Any]]) -> str:
    for ref in node.get("evidenceRefs") or []:
        evidence = evidence_by_id.get(ref)
        if evidence:
            return str((evidence.get("location") or {}).get("path") or "")
    return ""


def _count_by_kind(events: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        counts[event["kind"]] = counts.get(event["kind"], 0) + 1
    return counts
