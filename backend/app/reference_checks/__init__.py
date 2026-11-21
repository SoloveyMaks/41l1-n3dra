"""Versioned reference-suite comparison for immutable analyzer bundles."""

from .bundle import BundleView, ScanOutcome, parse_bundle
from .compare import COMPARATOR_VERSION, compare_reference_suite
from .manifest import (
    CATEGORIES,
    ReferenceCheckError,
    ReferenceSuite,
    parse_manifest,
)

__all__ = [
    "BundleView",
    "CATEGORIES",
    "COMPARATOR_VERSION",
    "ReferenceCheckError",
    "ReferenceSuite",
    "ScanOutcome",
    "compare_reference_suite",
    "parse_bundle",
    "parse_manifest",
]