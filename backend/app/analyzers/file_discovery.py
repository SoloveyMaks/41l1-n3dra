"""Scan directory policy shared by the backend analyzers.

Intake excludes generated and VCS directories from the frozen snapshot. The
backend analyzers must not read files that the frozen input does not contain;
otherwise a file added to ``build/`` after freeze would be analyzed under the
old snapshot identity.
"""

from __future__ import annotations

from typing import List

from backend.app.artifacts.intake import _SKIP_DIR_NAMES as INTAKE_SKIP_DIRS


def prune_scan_dirs(dirnames: List[str]) -> None:
    dirnames[:] = sorted(
        name for name in dirnames if name.casefold() not in INTAKE_SKIP_DIRS
    )