"""Импорт публичного репозитория GitHub/GitLab как immutable-артефакта.

Границы модуля:
* только корневые HTTPS-адреса `github.com` и `gitlab.com`; credentials,
  порты, query, fragment, SSH и локальные хосты отклоняются;
* ветка нужна только для resolve. Архив всегда качается по полученному
  commit SHA, поэтому движение ветки после resolve не меняет содержимое;
* ZIP проходит ту же проверку `SafeArtifactIntake`. До неё снимается
  provider-root и выбрасываются служебные каталоги, но каждая запись исходного
  архива проверяется на symlink, пути, размеры и compression ratio;
* submodules и Git LFS не скачиваются: в архиве остаются `.gitmodules` и
  LFS-указатели, внешние объекты не запрашиваются;
* git не вызывается, код проекта не импортируется и не исполняется.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import stat
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlsplit

import httpx

from backend.app.artifacts.intake import (
    CHUNK,
    MAX_ARCHIVE_BYTES,
    MAX_COMPRESSION_RATIO,
    MAX_FILE_BYTES,
    MAX_FILE_COUNT,
    MAX_PATH_DEPTH,
    MAX_PATH_LENGTH,
    MAX_UNCOMPRESSED_BYTES,
    SafeArtifactIntake,
    SafeIntakeError,
)
from backend.app.codefacts.identity import data_root

MAX_URL_LENGTH = 2048
MAX_BRANCH_LENGTH = 255
MAX_JSON_BYTES = 256 * 1024
MAX_GITLAB_SEGMENTS = 20
REQUEST_TIMEOUT_SECONDS = float(os.getenv("CG_REPO_REQUEST_TIMEOUT_SECONDS", "20"))
DOWNLOAD_TIMEOUT_SECONDS = float(os.getenv("CG_REPO_DOWNLOAD_TIMEOUT_SECONDS", "120"))
MAX_CONCURRENT_IMPORTS = max(1, int(os.getenv("CG_REPO_MAX_CONCURRENT", "2")))

_PROVIDER_HOSTS = {"github.com": "github", "gitlab.com": "gitlab"}
_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_GITHUB_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_GITHUB_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_GITLAB_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,254}$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_CONTROL_RE = re.compile(r"[\u0000-\u001f\u007f]")
_SKIP_DIR_NAMES = {".git", ".hg", ".svn", "node_modules", "__macosx", "build", "target", ".gradle"}

_IMPORT_SEM = asyncio.Semaphore(MAX_CONCURRENT_IMPORTS)
_INTAKE = SafeArtifactIntake()


class RepositoryImportError(RuntimeError):
    """Ошибка импорта с безопасным сообщением для пользователя."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class _RepoTarget:
    provider: str
    host: str
    segments: Tuple[str, ...]
    url: str

    @property
    def project_path(self) -> str:
        return "/".join(self.segments)

    @property
    def repo_name(self) -> str:
        return self.segments[-1]


def _reject(message: str, status_code: int = 400) -> RepositoryImportError:
    return RepositoryImportError(message, status_code)


def _parse_repository_url(url: Any) -> _RepoTarget:
    if not isinstance(url, str):
        raise _reject("Поле url обязательно")
    raw = url.strip()
    if not raw:
        raise _reject("Поле url обязательно")
    if len(raw) > MAX_URL_LENGTH:
        raise _reject("Слишком длинный URL репозитория")
    if "\\" in raw or any(_CONTROL_RE.search(ch) or ch.isspace() for ch in raw):
        raise _reject("URL репозитория содержит недопустимые символы")
    try:
        parts = urlsplit(raw)
        port = parts.port
        host = (parts.hostname or "").lower()
    except ValueError as exc:
        raise _reject("Некорректный URL репозитория") from exc
    if parts.scheme.lower() != "https":
        raise _reject("Разрешены только HTTPS-адреса GitHub/GitLab")
    if parts.username is not None or parts.password is not None or port is not None:
        raise _reject("URL с логином, паролем или портом запрещён")
    if parts.query or parts.fragment:
        raise _reject("URL репозитория не должен содержать query или fragment")
    provider = _PROVIDER_HOSTS.get(host)
    if provider is None:
        raise _reject("Разрешены только github.com и gitlab.com")
    if parts.netloc.lower() != host:
        raise _reject("Некорректный хост в URL репозитория")
    path = parts.path
    if not path or path == "/" or "%" in path:
        raise _reject("Нужен корневой URL репозитория")
    if path.endswith("/"):
        path = path[:-1]
    if not path.startswith("/") or "//" in path:
        raise _reject("Некорректный путь URL репозитория")
    segments = path[1:].split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise _reject("Некорректный путь URL репозитория")

    if provider == "github":
        if len(segments) != 2:
            raise _reject("Для GitHub нужен корневой адрес вида https://github.com/owner/repo")
        owner, repo = segments
        if not _GITHUB_OWNER_RE.fullmatch(owner):
            raise _reject("Некорректный owner GitHub")
        if repo.lower().endswith(".git"):
            repo = repo[:-4]
        if not _GITHUB_REPO_RE.fullmatch(repo) or repo in (".", "..") or repo.endswith("."):
            raise _reject("Некорректное имя репозитория GitHub")
        segments = [owner, repo]
    else:
        if not 2 <= len(segments) <= MAX_GITLAB_SEGMENTS:
            raise _reject("Для GitLab нужен корневой адрес вида https://gitlab.com/group/project")
        last = segments[-1]
        if last.lower().endswith(".git"):
            segments[-1] = last[:-4]
        if len("/".join(segments)) > 255:
            raise _reject("Слишком длинный путь проекта GitLab")
        for segment in segments:
            if (
                not _GITLAB_SEGMENT_RE.fullmatch(segment)
                or segment in (".", "..")
                or segment.endswith(".")
            ):
                raise _reject("Некорректный путь проекта GitLab")

    canonical = f"https://{host}/" + "/".join(segments)
    return _RepoTarget(provider=provider, host=host, segments=tuple(segments), url=canonical)


def _normalize_branch(branch: Any) -> Optional[str]:
    if branch is None:
        return None
    if not isinstance(branch, str):
        raise _reject("Поле branch должно быть строкой")
    value = branch.strip()
    if not value:
        return None
    if len(value) > MAX_BRANCH_LENGTH:
        raise _reject("Слишком длинное имя ветки")
    if (
        not _BRANCH_RE.fullmatch(value)
        or value in ("HEAD", ".", "..")
        or value.startswith(("/", "-", "."))
        or value.endswith(("/", "."))
        or value.endswith(".lock")
        or ".." in value
        or "//" in value
        or "@{" in value
    ):
        raise _reject("Некорректное имя ветки")
    return value


def _validate_sha(value: Any) -> str:
    if not isinstance(value, str):
        raise _reject("Некорректный commit SHA")
    sha = value.strip().lower()
    if not _SHA_RE.fullmatch(sha):
        raise _reject("Commit SHA должен быть 40 или 64 hex-символа")
    return sha


def _github_api(target: _RepoTarget, suffix: str = "") -> str:
    owner, repo = target.segments
    base = f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
    return f"{base}/{suffix}" if suffix else base


def _gitlab_api(target: _RepoTarget, suffix: str = "") -> str:
    project = quote(target.project_path, safe="")
    base = f"https://gitlab.com/api/v4/projects/{project}"
    return f"{base}/{suffix}" if suffix else base


def _archive_url(target: _RepoTarget, sha: str) -> str:
    if target.provider == "github":
        owner, repo = target.segments
        return f"https://codeload.github.com/{quote(owner, safe='')}/{quote(repo, safe='')}/zip/{sha}"
    return f"{_gitlab_api(target, 'repository/archive.zip')}?sha={sha}"


def _api_headers(provider: str) -> Dict[str, str]:
    headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
    if provider == "github":
        headers["Accept"] = "application/vnd.github+json"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    return headers


def _http_error(response: httpx.Response, provider: str) -> RepositoryImportError:
    status = response.status_code
    if status == 429 or (
        provider == "github"
        and status == 403
        and (
            response.headers.get("x-ratelimit-remaining") == "0"
            or response.headers.get("retry-after") is not None
        )
    ):
        return _reject("Провайдер ограничил частоту запросов; повторите позже", 429)
    if status in (401, 403, 404, 409, 422, 451):
        return _reject(
            "Репозиторий, ветка или коммит не найдены; приватный доступ не поддерживается",
            404,
        )
    if status == 413:
        return _reject("Архив репозитория слишком большой", 413)
    return _reject("Провайдер вернул ошибку; попробуйте позже", 502)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(connect=10.0, read=30.0, write=15.0, pool=10.0),
        headers={
            "User-Agent": "cyber-garden-repository-import/1.0",
            "Accept-Encoding": "identity",
        },
        limits=httpx.Limits(max_connections=2, max_keepalive_connections=0),
    )


async def _get_json(client: httpx.AsyncClient, url: str, provider: str) -> Dict[str, Any]:
    async def _run() -> Dict[str, Any]:
        async with client.stream("GET", url, headers=_api_headers(provider)) as response:
            if response.status_code != 200:
                raise _http_error(response, provider)
            payload = bytearray()
            async for chunk in response.aiter_bytes():
                payload.extend(chunk)
                if len(payload) > MAX_JSON_BYTES:
                    raise _reject("Слишком большой ответ провайдера", 502)
        try:
            data = json.loads(bytes(payload))
        except (ValueError, UnicodeDecodeError) as exc:
            raise _reject("Некорректный ответ провайдера", 502) from exc
        if not isinstance(data, dict):
            raise _reject("Некорректный ответ провайдера", 502)
        return data

    try:
        return await asyncio.wait_for(_run(), timeout=REQUEST_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        raise _reject("Провайдер не ответил вовремя", 502) from exc
    except httpx.HTTPError as exc:
        raise _reject("Не удалось подключиться к провайдеру", 502) from exc


async def _default_branch(client: httpx.AsyncClient, target: _RepoTarget) -> str:
    url = _github_api(target) if target.provider == "github" else _gitlab_api(target)
    data = await _get_json(client, url, target.provider)
    raw = data.get("default_branch")
    if not isinstance(raw, str) or not raw.strip():
        raise _reject("В репозитории нет веток или коммитов", 404)
    try:
        branch = _normalize_branch(raw)
    except RepositoryImportError as exc:
        raise _reject("Провайдер вернул некорректное имя ветки", 502) from exc
    if branch is None:
        raise _reject("В репозитории нет веток или коммитов", 404)
    return branch


async def _branch_commit(client: httpx.AsyncClient, target: _RepoTarget, branch: str) -> str:
    ref = quote(branch, safe="")
    if target.provider == "github":
        url = _github_api(target, f"branches/{ref}")
    else:
        url = _gitlab_api(target, f"repository/branches/{ref}")
    data = await _get_json(client, url, target.provider)
    commit = data.get("commit")
    if not isinstance(commit, dict):
        raise _reject("Провайдер не вернул коммит ветки", 502)
    raw = commit.get("sha") if target.provider == "github" else commit.get("id")
    if not isinstance(raw, str) or not _SHA_RE.fullmatch(raw.lower()):
        raise _reject("Провайдер не вернул commit SHA", 502)
    return raw.lower()


async def resolve_repository(url: str, branch: Optional[str] = None) -> Dict[str, Any]:
    """Возвращает канонический адрес, провайдера, фактическую ветку и commit SHA."""
    target = _parse_repository_url(url)
    requested = _normalize_branch(branch)
    async with _IMPORT_SEM:
        async with _client() as client:
            resolved_branch = requested or await _default_branch(client, target)
            commit_sha = await _branch_commit(client, target, resolved_branch)
    return {
        "url": target.url,
        "provider": target.provider,
        "branch": resolved_branch,
        "commit_sha": commit_sha,
    }


def _normalize_entry_name(name: str) -> str:
    if not isinstance(name, str) or not name:
        raise _reject("Пустое имя файла в архиве репозитория")
    normalized = unicodedata.normalize("NFC", name)
    if len(normalized) > MAX_PATH_LENGTH:
        raise _reject("Слишком длинный путь в архиве репозитория")
    if normalized.startswith("/") or _DRIVE_RE.match(normalized):
        raise _reject("Абсолютный путь в архиве репозитория")
    if "\\" in normalized or _CONTROL_RE.search(normalized):
        raise _reject("Недопустимые символы в пути архива репозитория")
    if "//" in normalized or normalized.endswith("."):
        raise _reject("Небезопасный путь в архиве репозитория")
    if normalized.endswith("/"):
        normalized = normalized.rstrip("/")
    if not normalized:
        raise _reject("Пустое имя файла в архиве репозитория")
    parts = normalized.split("/")
    if len(parts) > MAX_PATH_DEPTH:
        raise _reject("Слишком глубокая структура архива репозитория")
    for part in parts:
        if part in ("", ".", ".."):
            raise _reject("Небезопасный путь в архиве репозитория")
    return "/".join(parts)


def _entry_mode(info: zipfile.ZipInfo) -> int:
    return stat.S_IFMT(info.external_attr >> 16)


def _entry_is_symlink(info: zipfile.ZipInfo) -> bool:
    return _entry_mode(info) == stat.S_IFLNK


def _entry_is_special(info: zipfile.ZipInfo) -> bool:
    return _entry_mode(info) not in (0, stat.S_IFREG, stat.S_IFDIR)


def _entry_is_dir(info: zipfile.ZipInfo) -> bool:
    return info.is_dir() or _entry_mode(info) == stat.S_IFDIR


def _provider_root(paths: List[str], repo_name: str) -> Optional[str]:
    roots = {path.split("/", 1)[0] for path in paths}
    if len(roots) != 1:
        return None
    root = next(iter(roots))
    folded_root = root.casefold()
    folded_name = repo_name.casefold()
    if folded_root != folded_name and not folded_root.startswith(f"{folded_name}-"):
        return None
    if any("/" not in path for path in paths):
        return None
    return root


def _is_skipped_path(path: str) -> bool:
    return any(part.casefold() in _SKIP_DIR_NAMES for part in path.split("/"))


def _sanitize_archive(source_path: Path, target_path: Path, repo_name: str) -> None:
    try:
        archive = zipfile.ZipFile(source_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise _reject("Провайдер вернул повреждённый ZIP-архив", 502) from exc

    try:
        with archive:
            entries: List[Tuple[zipfile.ZipInfo, str]] = []
            declared_total = 0
            for info in archive.infolist():
                _normalize_entry_name(info.orig_filename)
                if info.flag_bits & 0x1:
                    raise _reject("Зашифрованные записи в архиве репозитория запрещены")
                if _entry_is_symlink(info):
                    raise _reject("Символические ссылки в архиве репозитория запрещены")
                if _entry_is_special(info):
                    raise _reject("Специальные файлы в архиве репозитория запрещены")
                rel = _normalize_entry_name(info.filename)
                if _entry_is_dir(info):
                    continue
                if info.file_size < 0 or info.file_size > MAX_FILE_BYTES:
                    raise _reject(f"Файл слишком большой после распаковки: {rel}", 413)
                if info.compress_size <= 0 and info.file_size > 0:
                    raise _reject(f"Подозрительная запись без сжатых данных: {rel}")
                if (
                    info.file_size > 1024 * 1024
                    and info.file_size > max(1, info.compress_size) * MAX_COMPRESSION_RATIO
                ):
                    raise _reject(f"Подозрительная степень сжатия файла: {rel}", 413)
                declared_total += info.file_size
                if declared_total > MAX_UNCOMPRESSED_BYTES:
                    raise _reject("Распакованный размер архива превышает лимит", 413)
                entries.append((info, rel))

            if not entries:
                raise _reject("В архиве репозитория нет файлов")
            if len(entries) > MAX_FILE_COUNT:
                raise _reject(f"В архиве больше {MAX_FILE_COUNT} файлов", 413)

            root = _provider_root([rel for _, rel in entries], repo_name)
            kept: List[Tuple[zipfile.ZipInfo, str]] = []
            seen: set[str] = set()
            for info, rel in entries:
                stripped = rel[len(root) + 1:] if root and rel.startswith(f"{root}/") else rel
                if not stripped or _is_skipped_path(stripped):
                    continue
                key = stripped.casefold()
                if key in seen:
                    raise _reject("Дублирующийся путь в архиве репозитория")
                seen.add(key)
                kept.append((info, stripped))
            if not kept:
                raise _reject("После удаления служебных каталогов в архиве нет файлов")

            written_total = 0
            with zipfile.ZipFile(
                target_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
            ) as result:
                for info, rel in kept:
                    target_info = zipfile.ZipInfo(rel, date_time=info.date_time)
                    target_info.compress_type = zipfile.ZIP_DEFLATED
                    target_info.external_attr = 0o600 << 16
                    written = 0
                    with archive.open(info) as source, result.open(target_info, "w") as sink:
                        while True:
                            chunk = source.read(CHUNK)
                            if not chunk:
                                break
                            written += len(chunk)
                            written_total += len(chunk)
                            if written > MAX_FILE_BYTES:
                                raise _reject(f"Файл превысил лимит распаковки: {rel}", 413)
                            if written_total > MAX_UNCOMPRESSED_BYTES:
                                raise _reject("Распакованный размер архива превышает лимит", 413)
                            sink.write(chunk)
                    if written != info.file_size:
                        raise _reject(f"Размер распакованного файла не совпал: {rel}")
    except RepositoryImportError:
        raise
    except (zipfile.BadZipFile, RuntimeError, ValueError, OSError) as exc:
        raise _reject("Не удалось прочитать ZIP-архив репозитория", 502) from exc


async def _download_archive(
    client: httpx.AsyncClient, target: _RepoTarget, sha: str, destination: Path
) -> None:
    url = _archive_url(target, sha)

    async def _run() -> None:
        written = 0
        async with client.stream(
            "GET", url, headers={"Accept": "application/zip", "Accept-Encoding": "identity"}
        ) as response:
            if response.status_code != 200:
                raise _http_error(response, target.provider)
            declared = response.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > MAX_ARCHIVE_BYTES:
                raise _reject(
                    f"Архив больше {MAX_ARCHIVE_BYTES // (1024 * 1024)} МБ", 413
                )
            with destination.open("wb") as sink:
                async for chunk in response.aiter_bytes(CHUNK):
                    written += len(chunk)
                    if written > MAX_ARCHIVE_BYTES:
                        raise _reject(
                            f"Архив больше {MAX_ARCHIVE_BYTES // (1024 * 1024)} МБ", 413
                        )
                    sink.write(chunk)
        if written == 0:
            raise _reject("Провайдер вернул пустой архив", 502)

    try:
        return await asyncio.wait_for(_run(), timeout=DOWNLOAD_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        raise _reject("Загрузка архива заняла слишком много времени", 502) from exc
    except httpx.HTTPError as exc:
        raise _reject("Не удалось скачать архив репозитория", 502) from exc


def _artifact_filename(repo_name: str, sha: str) -> str:
    return f"{repo_name[:100]}-{sha[:12]}.zip"


def _attach_repository_meta(manifest: Dict[str, Any], repository: Dict[str, str]) -> str:
    workspace = str(manifest["workspace_id"])
    artifact_id = str(manifest["id"])
    source_ref = f"{repository['url']}@{repository['commit_sha']}"
    meta = _INTAKE.store.load_artifact_meta(workspace, artifact_id) or {}
    meta.update({
        "artifactId": artifact_id,
        "workspaceId": workspace,
        "snapshotId": manifest["snapshot_id"],
        "snapshotManifestHash": manifest["snapshot_manifest_hash"],
        "artifactHash": manifest["hash_sha256"],
        "sizeBytes": manifest["size_bytes"],
        "uncompressedSizeBytes": manifest["uncompressed_size_bytes"],
        "fileCount": manifest["file_count"],
        "kotlinFileCount": manifest["kotlin_file_count"],
        "gradleFileCount": manifest["gradle_file_count"],
        "filename": manifest["filename"],
        "createdAt": manifest["created_at"],
        "expiresAt": manifest.get("expires_at"),
        "sourceRootPrefix": manifest.get("source_root_prefix", ""),
        "sourceRef": source_ref,
        "repository": dict(repository),
    })
    _INTAKE.store.save_artifact_meta(workspace, artifact_id, meta)
    return source_ref


async def import_repository(
    url: str,
    branch: Optional[str] = None,
    commit_sha: Optional[str] = None,
) -> Dict[str, Any]:
    """Скачивает и принимает публичный репозиторий; возвращает intake-манифест.

`commit_sha` фиксирует загрузку ровно на этом SHA: branch остаётся метаданными
и при отсутствии резолвится в фактическую default-ветку без сверки с текущим
head. Без `commit_sha` ветка резолвится в её текущий commit через branch API.
"""
    target = _parse_repository_url(url)
    requested_sha = _validate_sha(commit_sha) if commit_sha is not None else None
    if requested_sha is None:
        resolved = await resolve_repository(target.url, branch)
        resolved_branch = resolved["branch"]
        artifact_sha = resolved["commit_sha"]
    else:
        branch_name = _normalize_branch(branch)
        async with _IMPORT_SEM:
            async with _client() as client:
                resolved_branch = branch_name or await _default_branch(client, target)
        artifact_sha = requested_sha

    tmp_root = data_root() / "tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(tmp_root, 0o700)
    except OSError:
        pass

    source_fd, source_name = tempfile.mkstemp(
        prefix="repo_archive_", suffix=".zip", dir=str(tmp_root)
    )
    filtered_fd, filtered_name = tempfile.mkstemp(
        prefix="repo_filtered_", suffix=".zip", dir=str(tmp_root)
    )
    os.close(source_fd)
    os.close(filtered_fd)
    source_path = Path(source_name)
    filtered_path = Path(filtered_name)
    try:
        os.chmod(source_path, 0o600)
        os.chmod(filtered_path, 0o600)
        async with _IMPORT_SEM:
            async with _client() as client:
                await _download_archive(client, target, artifact_sha, source_path)
            await asyncio.to_thread(
                _sanitize_archive, source_path, filtered_path, target.repo_name
            )
            if filtered_path.stat().st_size > MAX_ARCHIVE_BYTES:
                raise _reject(
                    f"Архив больше {MAX_ARCHIVE_BYTES // (1024 * 1024)} МБ", 413
                )
            filename = _artifact_filename(target.repo_name, artifact_sha)
            manifest = await asyncio.to_thread(
                _INTAKE.ingest_zip_file, filename, filtered_path, strip_wrapper_root=True
            )
    except SafeIntakeError as exc:
        raise _reject(str(exc)) from exc
    finally:
        for temp_path in (source_path, filtered_path):
            try:
                temp_path.unlink()
            except OSError:
                pass

    repository = {
        "url": target.url,
        "provider": target.provider,
        "branch": resolved_branch,
        "commit_sha": artifact_sha,
    }
    source_ref = await asyncio.to_thread(_attach_repository_meta, manifest, repository)
    manifest["repository"] = dict(repository)
    manifest["source_ref"] = source_ref
    return manifest
