"""Typed full retrieval row for the optional exact-vector path.

The shared ``SimilarFix`` DTO in ``backend/app/contracts/schemas.py`` predates
the richer SQL contract: it has no fields for the redacted before-context, the
rule version or the example status. This module carries the complete row
without changing the shared contract. ``VectorRuntime.retrieve_for_findings``
still returns the legacy DTO for existing callers; the full model is available
through ``retrieve_examples_for_findings`` and is what the integration snippet
in ``docs/vetted-vector-similar-fix-snippet.md`` switches the shared DTO to.

Nothing here marks a draft example as vetted and nothing here judges whether a
retrieved fix applies to the finding. ``distance`` is a similarity score only.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class VectorExample(BaseModel):
    """One row returned by exact_similar_fixes/exact_demo_fixes."""

    findingId: Optional[str] = None
    exampleId: str
    sourceRef: str
    redactedBefore: str
    redactedFix: str
    ruleVersion: str
    verificationBasis: str
    limitations: List[str] = Field(default_factory=list)
    status: str
    distance: float
    metric: str
    embeddingSpecId: str