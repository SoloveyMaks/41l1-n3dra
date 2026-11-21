"""Versioned reference-suite comparison for immutable analyzer bundles."""

from .bundle import BundleView, ScanOutcome, parse_bundle
from .compare import COMPARATOR_VERSION, RAW_MODE, SERVER_MODE, compare_reference_suite
from .manifest import (
    CATEGORIES,
    ReferenceCheckError,
    ReferenceSuite,
    parse_manifest,
)
from .provenance import ServerSnapshot, Verification
from .registry import ReferenceSuiteRegistry

__all__ = [
    "BundleView",
    "CATEGORIES",
    "COMPARATOR_VERSION",
    "RAW_MODE",
    "ReferenceCheckError",
    "ReferenceSuite",
    "ReferenceSuiteRegistry",
    "SERVER_MODE",
    "ScanOutcome",
    "ServerSnapshot",
    "Verification",
    "compare_reference_suite",
    "parse_bundle",
    "parse_manifest",
]
