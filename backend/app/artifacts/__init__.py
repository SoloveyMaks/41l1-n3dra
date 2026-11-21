"""Безопасный приём исходников: ZIP-архив превращается в immutable snapshot."""

from backend.app.artifacts.intake import (
    MAX_ARCHIVE_BYTES,
    MAX_FILE_COUNT,
    MAX_UNCOMPRESSED_BYTES,
    SafeArtifactIntake,
    SafeIntakeError,
    build_snapshot_from_directory,
)

__all__ = [
    "MAX_ARCHIVE_BYTES",
    "MAX_FILE_COUNT",
    "MAX_UNCOMPRESSED_BYTES",
    "SafeArtifactIntake",
    "SafeIntakeError",
    "build_snapshot_from_directory",
]