"""Локальный sample-источник: упаковка каталога в ZIP только для чтения.

Путь задаётся сервером через `CG_TRAINING_PROJECT`, а не приходит из API.
`CG_TRAINING_PROJECT` должен указывать на корень проекта (каталог с
`settings.gradle(.kts)`/`build.gradle(.kts)`), а не на репозиторий-обёртку:
пути в ZIP уже относительны этого корня. Архив собирается детерминированно
(фиксированная дата записей), исходники не изменяются и не исполняются.
"""

from __future__ import annotations

import io
import os
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import List

from backend.app.artifacts.intake import SafeIntakeError

MAX_SAMPLE_FILES = int(os.getenv("CG_MAX_SAMPLE_FILES", "20000"))
MAX_SAMPLE_BYTES = int(os.getenv("CG_MAX_SAMPLE_BYTES", str(100 * 1024 * 1024)))
_SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", "node_modules", "build", "target", ".gradle"}


def build_zip_from_directory(source: Path) -> bytes:
    root = Path(source).resolve()
    if not root.is_dir():
        raise SafeIntakeError("Каталог sample-источника не найден")

    files: List[tuple[str, bytes]] = []
    total = 0
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d.casefold() not in _SKIP_DIRS)
        current_path = Path(current)
        for dirname in dirnames:
            if (current_path / dirname).is_symlink():
                raise SafeIntakeError("Символические ссылки в sample-источнике запрещены")
        for filename in sorted(filenames):
            target = current_path / filename
            if target.is_symlink() or not target.is_file():
                raise SafeIntakeError("Символические ссылки в sample-источнике запрещены")
            rel = target.relative_to(root).as_posix()
            if not rel or rel.startswith("/") or ".." in PurePosixPath(rel).parts:
                raise SafeIntakeError("Недопустимый путь в sample-источнике")
            if len(files) >= MAX_SAMPLE_FILES:
                raise SafeIntakeError("В sample-источнике слишком много файлов")
            with target.open("rb") as source_file:
                raw = source_file.read(min(5 * 1024 * 1024, MAX_SAMPLE_BYTES - total) + 1)
            if len(raw) > 5 * 1024 * 1024:
                raise SafeIntakeError(f"Файл sample-источника слишком большой: {rel}")
            total += len(raw)
            if total > MAX_SAMPLE_BYTES:
                raise SafeIntakeError("Sample-источник слишком большой")
            files.append((rel, raw))

    if not files:
        raise SafeIntakeError("Sample-источник пуст")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for rel, raw in sorted(files, key=lambda item: item[0]):
            info = zipfile.ZipInfo(rel, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(info, raw)
    return buffer.getvalue()

