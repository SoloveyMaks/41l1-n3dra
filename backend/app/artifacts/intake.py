"""Безопасный ZIP intake.

Правила:
* никаких абсолютных путей, drive-letter (`C:`), `\\`, `..`, `.`, NUL/control,
  двойных слэшей и пустых сегментов — это zip slip;
* symlink/device/fifo и encrypted entries отклоняются;
* границы: размер архива, суммарный распакованный размер, число файлов,
  размер одного файла, compression ratio, глубина пути;
* распаковка потоковая, с повторной проверкой фактических байтов;
* единственная папка-обёртка архива снимается, только если внутри неё лежит
  однозначный корень проекта (build-маркер): sourceManifest и extracted_path
  считаются от корня проекта, а не от имени папки, в которую его запаковали;
* код проекта никогда не импортируется и не исполняется — только чтение байтов;
* после распаковки строится sourceManifest (path/sizeBytes/sha256), его
  канонический hash и immutable snapshotId.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import stat
import tempfile
import unicodedata
import uuid
import zipfile
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional

from backend.app.codefacts.identity import (
    data_root,
    manifest_hash,
    sha256_hex,
    snapshot_uuid,
    storage_manifest_hash,
    workspace_id,
)
from backend.app.codefacts.store import CodeFactsStore


class SafeIntakeError(ValueError):
    """Архив не прошёл проверку безопасности или формата."""


MAX_ARCHIVE_BYTES = int(os.getenv("CG_MAX_ARCHIVE_BYTES", str(50 * 1024 * 1024)))
MAX_UNCOMPRESSED_BYTES = int(os.getenv("CG_MAX_UNCOMPRESSED_BYTES", str(250 * 1024 * 1024)))
MAX_FILE_COUNT = int(os.getenv("CG_MAX_FILE_COUNT", "5000"))
MAX_FILE_BYTES = int(os.getenv("CG_MAX_FILE_BYTES", str(25 * 1024 * 1024)))
MAX_COMPRESSION_RATIO = int(os.getenv("CG_MAX_COMPRESSION_RATIO", "200"))
MAX_PATH_DEPTH = int(os.getenv("CG_MAX_PATH_DEPTH", "32"))
MAX_PATH_LENGTH = 512
ARTIFACT_TTL_SECONDS = int(os.getenv("CG_ARTIFACT_TTL_SECONDS", str(7 * 24 * 60 * 60)))
CHUNK = 1024 * 1024

_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_CONTROL_RE = re.compile(r"[\u0000-\u001f\u007f]")
_SKIP_DIR_NAMES = {".git", ".hg", ".svn", "node_modules", "__macosx", "build", "target", ".gradle"}


def _zstd_zip_supported() -> bool:
    """ZIP_ZSTANDARD читается только вместе с compression.zstd (Python 3.14+)."""
    if getattr(zipfile, "ZIP_ZSTANDARD", None) is None:
        return False
    try:
        from compression import zstd  # noqa: F401 - проверка наличия кодека
    except ImportError:
        return False
    return True


# Методы, которые этот runtime действительно умеет распаковывать. Неизвестный
# или недоступный кодек остаётся SafeIntakeError, а не попыткой распаковки.
_SUPPORTED_COMPRESSION_METHODS = {
    zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA,
}
if _zstd_zip_supported():
    _SUPPORTED_COMPRESSION_METHODS.add(zipfile.ZIP_ZSTANDARD)

# Маркеры корня сборки, которые сами по себе доказывают, что верхняя папка —
# это корень проекта, а не просто каталог с исходниками.
_ROOT_BUILD_MARKERS = {
    "settings.gradle", "settings.gradle.kts",
    "build.gradle", "build.gradle.kts",
}
# Прочие манифесты ecosystems: недостаточны без каталога исходников рядом.
_ROOT_MARKERS_WITH_SRC = {
    "pom.xml", "build.sbt", "package.json", "pyproject.toml", "Cargo.toml",
    "go.mod", "CMakeLists.txt", "pubspec.yaml", "composer.json",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_filename(filename: str) -> str:
    name = Path(str(filename or "project.zip")).name.replace("\x00", "")
    if not name or len(name) > 200:
        raise SafeIntakeError("Недопустимое имя файла")
    if not name.lower().endswith(".zip"):
        raise SafeIntakeError("Разрешены только файлы с расширением .zip")
    return name


def _normalize_entry_name(name: str) -> str:
    if not isinstance(name, str) or not name:
        raise SafeIntakeError("Пустое имя файла в архиве")
    normalized = unicodedata.normalize("NFC", name)
    if len(normalized) > MAX_PATH_LENGTH:
        raise SafeIntakeError("Слишком длинный путь в архиве")
    if normalized.startswith("/") or _DRIVE_RE.match(normalized):
        raise SafeIntakeError(f"Абсолютный путь в архиве: {normalized!r}")
    if "\\" in normalized:
        raise SafeIntakeError(f"Windows-разделитель в архиве: {normalized!r}")
    if _CONTROL_RE.search(normalized):
        raise SafeIntakeError("Управляющие символы в пути архива")
    if "//" in normalized or normalized.endswith("."):
        raise SafeIntakeError(f"Небезопасный путь в архиве: {normalized!r}")
    if normalized.endswith("/"):
        normalized = normalized.rstrip("/")
        if not normalized:
            raise SafeIntakeError("Небезопасный путь в архиве")
    if not normalized:
        raise SafeIntakeError("Пустое имя файла в архиве")
    parts = normalized.split("/")
    if len(parts) > MAX_PATH_DEPTH:
        raise SafeIntakeError("Слишком глубокая структура архива")
    for part in parts:
        if part in ("", ".", ".."):
            raise SafeIntakeError(f"Небезопасный путь в архиве: {normalized!r}")
        if part.casefold() in _SKIP_DIR_NAMES:
            raise SafeIntakeError(f"Служебный каталог запрещён в архиве: {part!r}")
    return "/".join(parts)


def _entry_is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    return file_type == stat.S_IFLNK


def _entry_is_regular_or_dir(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    return file_type in (0, stat.S_IFREG, stat.S_IFDIR)


def _archive_id() -> str:
    return f"art_{uuid.uuid4().hex[:12]}"


def _count_kotlin(paths: Iterable[str]) -> int:
    return sum(1 for path in paths if path.endswith((".kt", ".kts")))


def _count_gradle(paths: Iterable[str]) -> int:
    names = {"gradle.properties", "libs.versions.toml", "settings.gradle", "settings.gradle.kts"}
    return sum(
        1 for path in paths
        if path.endswith((".gradle", ".gradle.kts")) or path.rsplit("/", 1)[-1] in names
    )


def _detect_wrapper_root(paths: Iterable[str]) -> Optional[str]:
    """Return the wrapper directory only when it is provably the project root.

    A ZIP is usually produced by packing a project folder, so every entry is
    prefixed with that folder name. The prefix is dropped only when the layout
    is unambiguous:

    * no file sits at archive root (flat and multi-module projects already are
      the root and must keep ``module/src/...`` or ``src/...`` as-is);
    * every file lives under one and the same top-level directory;
    * that directory contains a build-root marker at its own depth. Gradle
      ``settings``/``build`` files are enough; other ecosystem manifests
      additionally require a sibling ``src/`` tree.

    Everything else (several top-level trees, wrapper without markers, a
    nested project without its own marker) returns ``None``: callers keep the
    original paths instead of guessing.
    """
    wrapper: Optional[str] = None
    inner: List[str] = []
    for path in paths:
        head, sep, tail = path.partition("/")
        if not sep:
            return None
        if wrapper is None:
            wrapper = head
        elif head != wrapper:
            return None
        inner.append(tail)
    if wrapper is None:
        return None
    root_names = {path.split("/", 1)[0] for path in inner if "/" not in path}
    if root_names & _ROOT_BUILD_MARKERS:
        return wrapper
    if root_names & _ROOT_MARKERS_WITH_SRC and any(path.startswith("src/") for path in inner):
        return wrapper
    return None


def _strip_wrapper_root(rel: str, wrapper: Optional[str]) -> str:
    if not wrapper:
        return rel
    prefix = f"{wrapper}/"
    if not rel.startswith(prefix):
        raise SafeIntakeError(f"Путь вне выбранного корня проекта: {rel!r}")
    stripped = rel[len(prefix):]
    if not stripped:
        raise SafeIntakeError(f"Пустой путь после нормализации: {rel!r}")
    return stripped


def build_snapshot_from_directory(root: Path, workspace: str) -> Dict[str, Any]:
    """Строит snapshot по уже распакованному каталогу (fallback для старых артефактов)."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise SafeIntakeError("Каталог артефакта не найден")
    files: List[Dict[str, Any]] = []
    total = 0
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d.casefold() not in _SKIP_DIR_NAMES)
        current_path = Path(current)
        for dirname in dirnames:
            if (current_path / dirname).is_symlink():
                raise SafeIntakeError("Символические ссылки в артефакте запрещены")
        for filename in sorted(filenames):
            target = current_path / filename
            if target.is_symlink():
                raise SafeIntakeError("Символические ссылки в артефакте запрещены")
            if not target.is_file():
                continue
            rel = _normalize_entry_name(target.relative_to(root).as_posix())
            if len(files) >= MAX_FILE_COUNT:
                raise SafeIntakeError("В артефакте слишком много файлов")
            with target.open("rb") as source:
                raw = source.read(min(MAX_FILE_BYTES, MAX_UNCOMPRESSED_BYTES - total) + 1)
            if len(raw) > MAX_FILE_BYTES:
                raise SafeIntakeError("Файл артефакта слишком большой")
            total += len(raw)
            if total > MAX_UNCOMPRESSED_BYTES:
                raise SafeIntakeError("Артефакт превышает лимит распакованного размера")
            files.append({"path": rel, "sizeBytes": len(raw), "sha256": sha256_hex(raw)})
    if not files:
        raise SafeIntakeError("В артефакте нет файлов")
    files.sort(key=lambda item: tuple(item["path"].split("/")))
    source_hash = manifest_hash(files)
    mhash = storage_manifest_hash(files)
    sid = snapshot_uuid(mhash, workspace)
    return {
        "schemaVersion": "cg-snapshot/1",
        "workspaceId": workspace,
        "snapshotId": sid,
        "manifestHash": mhash,
        "sourceManifestHash": source_hash,
        "files": files,
        "createdAt": _now(),
    }


class SafeArtifactIntake:
    def __init__(self, storage_root: Optional[Path] = None):
        self.storage_root = Path(storage_root) if storage_root else data_root() / "artifacts"
        self.temp_root = data_root() / "tmp"
        self.store = CodeFactsStore()

    # --- upload ----------------------------------------------------------
    async def ingest_upload(self, upload: Any, filename: str, workspace: Optional[str] = None) -> Dict[str, Any]:
        """Потоково принимает UploadFile, не читая архив целиком в память."""
        safe_name = _safe_filename(filename)
        self.temp_root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.temp_root, 0o700)
        except OSError:
            pass
        fd, temp_name = tempfile.mkstemp(prefix="upload_", suffix=".zip", dir=str(self.temp_root))
        temp_path = Path(temp_name)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(fd, "wb") as handle:
                while True:
                    chunk = await upload.read(CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_ARCHIVE_BYTES:
                        raise SafeIntakeError(
                            f"Архив больше {MAX_ARCHIVE_BYTES // (1024 * 1024)} МБ"
                        )
                    digest.update(chunk)
                    handle.write(chunk)
            if size == 0:
                raise SafeIntakeError("Архив пустой")
            os.chmod(temp_path, 0o600)
            return await asyncio.to_thread(
                self.ingest_zip_file, safe_name, temp_path,
                digest.hexdigest(), workspace,
            )
        finally:
            try:
                temp_path.unlink()
            except OSError:
                pass

    # --- public API ------------------------------------------------------
    def ingest_zip_bytes(self, filename: str, content: bytes, workspace: Optional[str] = None) -> Dict[str, Any]:
        if not content:
            raise SafeIntakeError("Архив пустой")
        if len(content) > MAX_ARCHIVE_BYTES:
            raise SafeIntakeError(f"Архив больше {MAX_ARCHIVE_BYTES // (1024 * 1024)} МБ")
        self.temp_root.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="upload_", suffix=".zip", dir=str(self.temp_root))
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
            os.chmod(temp_path, 0o600)
            return self.ingest_zip_file(filename, temp_path, sha256_hex(content), workspace)
        finally:
            try:
                temp_path.unlink()
            except OSError:
                pass

    # Backward-compatible name used by the demo endpoint.
    def process_zip_bytes(self, filename: str, content: bytes) -> Dict[str, Any]:
        return self.ingest_zip_bytes(filename, content)

    def ingest_zip_file(self, filename: str, archive_path: Path, expected_archive_hash: Optional[str] = None,
                        workspace: Optional[str] = None, *, strip_wrapper_root: bool = True) -> Dict[str, Any]:
        safe_name = _safe_filename(filename)
        ws = workspace or workspace_id()
        archive_path = Path(archive_path)
        if not archive_path.is_file():
            raise SafeIntakeError("Временный архив не найден")
        archive_bytes = archive_path.stat().st_size
        if archive_bytes <= 0:
            raise SafeIntakeError("Архив пустой")
        if archive_bytes > MAX_ARCHIVE_BYTES:
            raise SafeIntakeError(f"Архив больше {MAX_ARCHIVE_BYTES // (1024 * 1024)} МБ")
        archive_hash = expected_archive_hash or hashlib.sha256(archive_path.read_bytes()).hexdigest()

        try:
            archive = zipfile.ZipFile(str(archive_path))
        except (zipfile.BadZipFile, OSError) as exc:
            raise SafeIntakeError("Файл не является корректным ZIP-архивом") from exc

        entries: List[zipfile.ZipInfo] = []
        names: set[str] = set()
        directories: set[str] = set()
        total_declared = 0
        with archive:
            for info in archive.infolist():
                _normalize_entry_name(info.orig_filename)
                if info.flag_bits & 0x1:
                    raise SafeIntakeError(f"Зашифрованные записи запрещены: {info.filename!r}")
                if _entry_is_symlink(info):
                    raise SafeIntakeError(f"Символические ссылки запрещены: {info.filename!r}")
                if not _entry_is_regular_or_dir(info):
                    raise SafeIntakeError(f"Специальные файлы запрещены: {info.filename!r}")
                if info.is_dir():
                    directories.add(_normalize_entry_name(info.filename).casefold())
                    continue
                if info.compress_type not in _SUPPORTED_COMPRESSION_METHODS:
                    raise SafeIntakeError("Неподдерживаемый метод сжатия ZIP")
                rel = _normalize_entry_name(info.filename)
                key = rel.casefold()
                if key in names:
                    raise SafeIntakeError(f"Дублирующийся путь в архиве: {rel!r}")
                names.add(key)
                if info.file_size < 0 or info.file_size > MAX_FILE_BYTES:
                    raise SafeIntakeError(f"Файл слишком большой после распаковки: {rel!r}")
                if info.compress_size <= 0 and info.file_size > 0:
                    raise SafeIntakeError(f"Подозрительная запись без сжатых данных: {rel!r}")
                if info.file_size > 1024 * 1024 and info.file_size > max(1, info.compress_size) * MAX_COMPRESSION_RATIO:
                    raise SafeIntakeError(f"Подозрительная степень сжатия файла: {rel!r}")
                total_declared += info.file_size
                if total_declared > MAX_UNCOMPRESSED_BYTES:
                    raise SafeIntakeError(
                        f"Распакованный размер больше {MAX_UNCOMPRESSED_BYTES // (1024 * 1024)} МБ"
                    )
                entries.append(info)

            if not entries:
                raise SafeIntakeError("В архиве нет файлов")
            if len(entries) > MAX_FILE_COUNT:
                raise SafeIntakeError(f"В архиве больше {MAX_FILE_COUNT} файлов")
            for key in names | directories:
                if key in names and key in directories:
                    raise SafeIntakeError("Путь одновременно является файлом и каталогом")
                if any(parent.as_posix() in names for parent in PurePosixPath(key).parents):
                    raise SafeIntakeError("Файл используется как родительский каталог")
            if total_declared > max(1024 * 1024, archive_bytes * MAX_COMPRESSION_RATIO):
                raise SafeIntakeError("Подозрительная общая степень сжатия архива")

            # Zip-slip/symlink/duplicate/limits уже проверены по исходным
            # путям. Обёртку снимаем только после этого и только при
            # доказанном корне проекта; архивный hash остаётся хэшем ZIP.
            wrapper_root = _detect_wrapper_root(
                [_normalize_entry_name(info.filename) for info in entries]
            ) if strip_wrapper_root else None
            artifact_id = _archive_id()
            target_dir = (self.storage_root / artifact_id).resolve()
            self.storage_root.mkdir(parents=True, exist_ok=True)
            if target_dir.exists():
                raise SafeIntakeError("Конфликт идентификатора артефакта")
            target_dir.mkdir(parents=False, exist_ok=False)
            try:
                os.chmod(target_dir, 0o700)
            except OSError:
                pass

            records: List[Dict[str, Any]] = []
            written_total = 0
            try:
                for info in entries:
                    rel = _normalize_entry_name(info.filename)
                    stored = _strip_wrapper_root(rel, wrapper_root)
                    destination = (target_dir / stored).resolve()
                    if target_dir != destination and target_dir not in destination.parents:
                        raise SafeIntakeError(f"Путь выходит за каталог распаковки: {rel!r}")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    file_size = 0
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                    if hasattr(os, "O_NOFOLLOW"):
                        flags |= os.O_NOFOLLOW
                    fd = os.open(destination, flags, 0o600)
                    with os.fdopen(fd, "wb") as sink, archive.open(info, "r") as source:
                        while True:
                            chunk = source.read(CHUNK)
                            if not chunk:
                                break
                            file_size += len(chunk)
                            written_total += len(chunk)
                            if file_size > MAX_FILE_BYTES:
                                raise SafeIntakeError(f"Файл превысил лимит распаковки: {rel!r}")
                            if written_total > MAX_UNCOMPRESSED_BYTES:
                                raise SafeIntakeError(
                                    f"Распакованный размер больше {MAX_UNCOMPRESSED_BYTES // (1024 * 1024)} МБ"
                                )
                            digest.update(chunk)
                            sink.write(chunk)
                    if file_size != info.file_size:
                        raise SafeIntakeError(f"Размер распакованного файла не совпал: {rel!r}")
                    records.append({
                        "path": stored,
                        "sizeBytes": file_size,
                        "sha256": digest.hexdigest(),
                    })
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, zlib.error) as exc:
                shutil.rmtree(target_dir, ignore_errors=True)
                raise SafeIntakeError("Не удалось прочитать данные ZIP-архива") from exc
            except Exception:
                shutil.rmtree(target_dir, ignore_errors=True)
                raise

            records.sort(key=lambda item: tuple(item["path"].split("/")))
            source_hash = manifest_hash(records)
            mhash = storage_manifest_hash(records)
            sid = snapshot_uuid(mhash, workspace)
            snapshot = {
                "schemaVersion": "cg-snapshot/1",
                "workspaceId": ws,
                "snapshotId": sid,
                "manifestHash": mhash,
                "sourceManifestHash": source_hash,
                "artifactHash": archive_hash,
                "files": records,
                "createdAt": _now(),
            }
            try:
                self.store.save_snapshot(ws, snapshot)
            except Exception:
                shutil.rmtree(target_dir, ignore_errors=True)
                raise

        paths = [record["path"] for record in records]
        manifest = {
            "id": artifact_id,
            "filename": safe_name,
            "hash_sha256": archive_hash,
            "size_bytes": archive_bytes,
            "uncompressed_size_bytes": written_total,
            "file_count": len(records),
            "kotlin_file_count": _count_kotlin(paths),
            "gradle_file_count": _count_gradle(paths),
            "extracted_path": str(target_dir),
            "created_at": _now(),
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(seconds=max(60, ARTIFACT_TTL_SECONDS))
            ).isoformat(),
            "workspace_id": ws,
            "snapshot_id": sid,
            "snapshot_manifest_hash": mhash,
            "source_root_prefix": wrapper_root or "",
        }
        meta = {
            "artifactId": artifact_id,
            "workspaceId": ws,
            "snapshotId": sid,
            "snapshotManifestHash": mhash,
            "artifactHash": archive_hash,
            "sizeBytes": archive_bytes,
            "uncompressedSizeBytes": written_total,
            "fileCount": len(records),
            "kotlinFileCount": manifest["kotlin_file_count"],
            "gradleFileCount": manifest["gradle_file_count"],
            "filename": safe_name,
            "createdAt": manifest["created_at"],
            "expiresAt": manifest["expires_at"],
            "sourceRootPrefix": wrapper_root or "",
        }
        self.store.save_artifact_meta(ws, artifact_id, meta)
        return manifest
