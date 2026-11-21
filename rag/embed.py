"""Deterministic local embeddings for the RAG store.

No external model, no network and no third-party dependency.  The vectorizer is
a hashed TF-IDF vector space model with BM25 term weighting:

* text is lowercased, split on camelCase/snake_case boundaries and stop words;
* document terms are weighted by ``idf * tf*(k1+1) / (tf + k1*(1-b+b*dl/avgdl))``;
* query terms are weighted by ``idf * (1 + ln tf)``;
* both sides are hashed into a fixed 4096-dimensional space (unsigned feature
  hashing) and L2-normalised, so cosine distance is well defined and stable.

Why TF-IDF and not a neural model: the project has no provider yet, and reuse
must be reproducible and auditable.  A lexical model is honest about what it can
do — it ranks by shared vocabulary, not by "the same bug".  Package D can plug a
real provider in later: the store only requires a versioned spec plus
``document_vector``/``query_vector``.

Because ``idf`` depends on the corpus, vectors are *derived*: they are rebuilt
when the element journal changes.  Element ids stay stable, so reuse does not
depend on the vector layout.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Iterable

SPEC_VERSION = "2"
PROVIDER = "local"
MODEL = "bm25-hash-tfidf"
MODEL_VERSION = "2"
DIMENSION = 4096
METRIC = "cosine"
QUERY_PREPARATION_VERSION = "q2"
DOCUMENT_PREPARATION_VERSION = "d2"
REDACTION_VERSION = "bundle-redacted-v1"

K1 = 1.2
B = 0.75

_CAMEL = re.compile(r"(?<=[a-zа-яё0-9])(?=[A-ZА-ЯЁ])")
_WORD = re.compile(r"[a-zа-яё0-9]+")
_STOP = {
    "и", "в", "во", "на", "с", "со", "по", "для", "из", "от", "до", "не", "но",
    "что", "как", "это", "the", "a", "an", "of", "to", "in", "on", "for", "and",
    "or", "is", "are", "be", "with", "by", "as", "at", "it", "this", "that",
}

# Sparse vector: ``{hashed_index: weight}``.  Only non-zero weights are stored.
Vector = dict[int, float]


def spec() -> dict[str, object]:
    """The embedding spec as it is written into the store manifest."""

    return {
        "provider": PROVIDER,
        "model": MODEL,
        "modelVersion": MODEL_VERSION,
        "dimension": DIMENSION,
        "metric": METRIC,
        "queryPreparationVersion": QUERY_PREPARATION_VERSION,
        "documentPreparationVersion": DOCUMENT_PREPARATION_VERSION,
        "redactionVersion": REDACTION_VERSION,
    }


def spec_id(value: dict[str, object] | None = None) -> str:
    payload = json.dumps(value or spec(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def tokenize(text: str) -> list[str]:
    """Lowercase, split identifiers and drop stop words."""

    split = _CAMEL.sub(" ", text or "").lower()
    return [word for word in _WORD.findall(split) if word and word not in _STOP]


def _hash(term: str) -> int:
    digest = hashlib.blake2b(term.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % DIMENSION


def _normalize(vector: Vector) -> Vector:
    norm = math.sqrt(sum(value * value for value in vector.values()))
    if norm == 0.0:
        return {}
    return {index: value / norm for index, value in vector.items()}


def _accumulate(counts: dict[str, float]) -> Vector:
    vector: Vector = {}
    for term, weight in counts.items():
        index = _hash(term)
        vector[index] = vector.get(index, 0.0) + weight
    return _normalize({index: value for index, value in vector.items() if value != 0.0})


@dataclass
class Index:
    """Corpus statistics plus document/query vectorization under one spec."""

    idf: dict[str, float] = field(default_factory=dict)
    average_length: float = 1.0
    documents: int = 0
    corpus_hash: str = ""
    specId: str = field(default_factory=lambda: spec_id())

    @classmethod
    def build(cls, texts: Iterable[str]) -> "Index":
        tokenized = [tokenize(text) for text in texts]
        documents = len(tokenized)
        if documents == 0:
            return cls(idf={}, average_length=1.0, documents=0, corpus_hash=_corpus_hash([]))
        frequency: dict[str, int] = {}
        for terms in tokenized:
            for term in set(terms):
                frequency[term] = frequency.get(term, 0) + 1
        idf = {
            term: math.log(1.0 + (documents - count + 0.5) / (count + 0.5))
            for term, count in frequency.items()
        }
        average_length = sum(len(terms) for terms in tokenized) / documents
        return cls(
            idf=idf,
            average_length=average_length or 1.0,
            documents=documents,
            corpus_hash=_corpus_hash(tokenized),
        )

    def document_vector(self, text: str) -> Vector:
        terms = tokenize(text)
        if not terms or not self.idf:
            return {}
        length = len(terms)
        counts: dict[str, int] = {}
        for term in terms:
            counts[term] = counts.get(term, 0) + 1
        weighted: dict[str, float] = {}
        for term, frequency in counts.items():
            inverse = self.idf.get(term)
            if inverse is None:
                continue
            saturation = frequency * (K1 + 1.0) / (
                frequency + K1 * (1.0 - B + B * length / self.average_length)
            )
            weighted[term] = inverse * saturation
        return _accumulate(weighted)

    def query_vector(self, text: str) -> Vector:
        terms = tokenize(text)
        if not terms or not self.idf:
            return {}
        counts: dict[str, int] = {}
        for term in terms:
            counts[term] = counts.get(term, 0) + 1
        weighted = {
            term: self.idf[term] * (1.0 + math.log(frequency))
            for term, frequency in counts.items()
            if term in self.idf
        }
        return _accumulate(weighted)


def _corpus_hash(tokenized: Iterable[list[str]]) -> str:
    digest = hashlib.sha256()
    for terms in tokenized:
        digest.update((" ".join(terms) + "\n").encode("utf-8"))
    return digest.hexdigest()[:32]


def cosine_distance(left: Vector, right: Vector) -> float:
    """``1 - cos`` on unit vectors; smaller is closer, it is not a probability."""

    if not left or not right:
        return 1.0
    if len(left) > len(right):
        left, right = right, left
    dot = sum(value * right.get(index, 0.0) for index, value in left.items())
    return max(0.0, min(2.0, 1.0 - dot))


def prepare_query(text: str, index: Index) -> "Query":
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    return Query(text=normalized, vector=index.query_vector(normalized), specId=index.specId)


@dataclass(frozen=True)
class Query:
    """A prepared query: the same text rules as documents, different version."""

    text: str
    vector: Vector
    specId: str
