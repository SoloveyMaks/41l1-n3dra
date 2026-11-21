"""Local RAG store for analysis elements.

Public surface:

``Index`` / ``spec`` / ``spec_id``
    Corpus statistics and a deterministic, versioned vectorizer (no network,
    no model download, no third-party dependency).
``Store``
    Append-only element journal + derived vector journal + manifest.
``prepare_query``
    Query preparation under its own version.

The store is intentionally file based: the project does not use a relational
database, and the team contract only requires a typed retrieval result, not a
particular backend.
"""

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
from .store import (
    Element,
    SpecMismatch,
    Store,
    StoreError,
    WorkspaceMismatch,
    element_id,
    text_hash,
)

__all__ = [
    "DIMENSION",
    "METRIC",
    "Element",
    "Index",
    "Query",
    "SpecMismatch",
    "Store",
    "StoreError",
    "Vector",
    "WorkspaceMismatch",
    "cosine_distance",
    "element_id",
    "prepare_query",
    "spec",
    "spec_id",
    "text_hash",
    "tokenize",
]
