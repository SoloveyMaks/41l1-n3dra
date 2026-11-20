"""Безопасный приём ZIP-архива проекта.

Модуль лежал вне git из-за правила ``artifacts/`` в .gitignore, хотя router и
executor импортируют его. Контракт манифеста — ``ArtifactManifest`` и
``frontend/src/types/index.ts``: архив распаковывается в
``backend/data/artifacts/<id>``, в БД уходит только путь и метаданные.
"""

import hashlib
import io
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_FILE_COUNT = 5000
MAX_COMPRESSION_RATIO = 200


class SafeIntakeError(ValueError):
    """Архив не прошёл проверку безопасности или формата."""


def _is_unsafe_path(name: str) -> bool:
    if not name or name.startswith("/") or "\\" in name or "\x00" in name:
        return True
    path = PurePosixPath(name)
    return any(part in ("..", "") for part in path.parts) or (len(name) > 1 and name[1] == ":")


class SafeArtifactIntake:
    def __init__(self, storage_root: Path = DATA_DIR / "artifacts"):
        self.storage_root = storage_root

    def process_zip_bytes(self, filename: str, content: bytes) -> dict:
        if not content:
            raise SafeIntakeError("Архив пустой")
        if len(content) > MAX_ARCHIVE_BYTES:
            raise SafeIntakeError(
                f"Архив больше {MAX_ARCHIVE_BYTES // (1024 * 1024)} МБ"
            )

        try:
            archive = zipfile.ZipFile(io.BytesIO(content))
        except zipfile.BadZipFile as exc:
            raise SafeIntakeError("Файл не является корректным ZIP-архивом") from exc

        with archive:
            entries = []
            total_uncompressed = 0
            for info in archive.infolist():
                if info.is_dir():
                    continue
                if _is_unsafe_path(info.filename):
                    raise SafeIntakeError(f"Небезопасный путь в архиве: {info.filename}")
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise SafeIntakeError(f"Символические ссылки запрещены: {info.filename}")
                total_uncompressed += info.file_size
                entries.append(info)

            if not entries:
                raise SafeIntakeError("В архиве нет файлов")
            if len(entries) > MAX_FILE_COUNT:
                raise SafeIntakeError(f"В архиве больше {MAX_FILE_COUNT} файлов")
            if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
                raise SafeIntakeError(
                    f"Распакованный размер больше {MAX_UNCOMPRESSED_BYTES // (1024 * 1024)} МБ"
                )
            if total_uncompressed > len(content) * MAX_COMPRESSION_RATIO:
                raise SafeIntakeError("Подозрительная степень сжатия архива")

            artifact_id = f"art_{uuid.uuid4().hex[:8]}"
            target_dir = self.storage_root / artifact_id
            target_dir.mkdir(parents=True, exist_ok=False)
            try:
                for info in entries:
                    destination = (target_dir / info.filename).resolve()
                    if target_dir.resolve() not in destination.parents:
                        raise SafeIntakeError(f"Небезопасный путь в архиве: {info.filename}")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, destination.open("wb") as sink:
                        shutil.copyfileobj(source, sink)
            except Exception:
                shutil.rmtree(target_dir, ignore_errors=True)
                raise

        kotlin_count = sum(
            1 for e in entries if e.filename.endswith((".kt", ".kts"))
        )
        gradle_count = sum(
            1 for e in entries
            if e.filename.endswith((".gradle", ".gradle.kts")) or Path(e.filename).name == "gradle.properties"
        )
        return {
            "id": artifact_id,
            "filename": Path(filename).name,
            "hash_sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "uncompressed_size_bytes": total_uncompressed,
            "file_count": len(entries),
            "kotlin_file_count": kotlin_count,
            "gradle_file_count": gradle_count,
            "extracted_path": str(target_dir),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }