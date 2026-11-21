"""Server-owned reference suite registry.

A client sends only a ``referenceManifestId``. The server resolves it through
this registry: the manifest and an optional frozen reference snapshot are
files owned by the deployment, never request data. IDs are strict path-safe
tokens and may not escape the registry directory.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from .manifest import ReferenceCheckError

DEFAULT_REFERENCE_DIR = (
    Path(__file__).resolve().parents[3] / "docs" / "reference-checks" / "fixtures"
)
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SNAPSHOT_SUFFIX = ".reference.json"


class ReferenceSuiteRegistry:
    """Read manifests and optional frozen reference snapshots from disk."""

    def __init__(self, directory: str | Path | None = None):
        configured = directory or os.getenv("CG_REFERENCE_SUITES_DIR") or DEFAULT_REFERENCE_DIR
        self.directory = Path(configured).resolve()

    def _path(self, manifest_id: str, suffix: str = ".json") -> Path:
        if not isinstance(manifest_id, str) or not _ID_RE.fullmatch(manifest_id):
            raise ReferenceCheckError(
                "REFERENCE_UNKNOWN", "referenceManifestId is not a valid server-owned id"
            )
        path = (self.directory / f"{manifest_id}{suffix}").resolve()
        if path.parent != self.directory:
            raise ReferenceCheckError(
                "REFERENCE_UNKNOWN", "referenceManifestId escapes the registry directory"
            )
        return path

    def load_manifest(self, manifest_id: str) -> Any:
        path = self._path(manifest_id)
        if not path.is_file():
            raise ReferenceCheckError(
                "REFERENCE_UNKNOWN", f"reference suite {manifest_id!r} is not registered"
            )
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReferenceCheckError(
                "REFERENCE_UNREADABLE",
                f"reference suite {manifest_id!r} cannot be read: {exc}",
            ) from exc

    def load_reference_snapshot(self, manifest_id: str) -> Optional[Any]:
        """Optional frozen reference snapshot stored next to the manifest.

        Expected shape::

            {"sourceCommit": "...", "sourceBranch": "...", "bundle": {...}}

        The server computes the content hash from ``bundle``; a deployment that
        prefers a live reference run can pin ``reference.runId``/``snapshotId``
        in the manifest instead.
        """

        path = self._path(manifest_id, _SNAPSHOT_SUFFIX)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReferenceCheckError(
                "REFERENCE_UNREADABLE",
                f"reference snapshot {manifest_id!r} cannot be read: {exc}",
            ) from exc
