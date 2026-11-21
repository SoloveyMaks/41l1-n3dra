"""Bounded public file-history evidence for a frozen repository commit.

This is intentionally *not* blame and not vulnerability attribution: the
provider returns the last commit for one file. Only public GitHub/GitLab hosts
and the commit SHA stored with the artifact are accepted.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote, urlsplit

import httpx

from backend.app.codefacts.source import is_safe_source_path
from backend.app.responsibility.models import CommitEvidence, HistoryInfo, LineBlameInfo

MAX_RESPONSE_BYTES = 256 * 1024
REQUEST_TIMEOUT_SECONDS = 4.0
CACHE_TTL_SECONDS = 15 * 60
CACHE_ERROR_TTL_SECONDS = 90
MAX_CACHE_ENTRIES = 256
_ALLOWED_HOSTS = {"github.com": "github", "gitlab.com": "gitlab"}
_GITHUB_OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_GITHUB_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_GITLAB_SEGMENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,254}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_CACHE: Dict[str, Tuple[float, HistoryInfo]] = {}


def _cache_get(key: str) -> Optional[HistoryInfo]:
    item = _CACHE.get(key)
    if not item:
        return None
    expires, value = item
    if expires <= time.monotonic():
        _CACHE.pop(key, None)
        return None
    return value


def _cache_put(key: str, value: HistoryInfo) -> None:
    if len(_CACHE) >= MAX_CACHE_ENTRIES:
        oldest = min(_CACHE, key=lambda item: _CACHE[item][0])
        _CACHE.pop(oldest, None)
    ttl = CACHE_ERROR_TTL_SECONDS if value.status != "available" else CACHE_TTL_SECONDS
    _CACHE[key] = (time.monotonic() + ttl, value)


def _unavailable(status: str, reason: str, provider: Optional[str] = None) -> HistoryInfo:
    return HistoryInfo(
        status=status,
        provider=provider,
        reason=reason,
        evidenceKind="last_file_commit",
        notBlame=True,
        lineBlame=LineBlameInfo(),
        limitations=[
            "Это последний коммит файла, а не автор уязвимости.",
            "Line blame не поддерживается без отдельного безопасного провайдера.",
        ],
    )


def parse_repository_target(repository: Any) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (provider, project_path, commit_sha) or reasons safe to return."""
    if not isinstance(repository, dict):
        return None, None, None
    provider = repository.get("provider")
    raw_url = repository.get("url")
    commit_sha = repository.get("commit_sha")
    if provider not in {"github", "gitlab"} or not isinstance(raw_url, str):
        return None, None, None
    if not isinstance(commit_sha, str) or not _SHA.fullmatch(commit_sha.lower()):
        return None, None, None
    try:
        parts = urlsplit(raw_url.strip())
    except ValueError:
        return None, None, None
    if parts.scheme != "https" or parts.netloc.lower() not in _ALLOWED_HOSTS:
        return None, None, None
    if parts.query or parts.fragment or parts.username or parts.password:
        return None, None, None
    if _ALLOWED_HOSTS[parts.netloc.lower()] != provider:
        return None, None, None
    segments = [segment for segment in parts.path.split("/") if segment]
    if provider == "github":
        if len(segments) != 2:
            return None, None, None
        owner, repo = segments
        if not _GITHUB_OWNER.fullmatch(owner):
            return None, None, None
        if repo.lower().endswith(".git"):
            repo = repo[:-4]
        if not _GITHUB_REPO.fullmatch(repo) or repo in (".", ".."):
            return None, None, None
        return provider, f"{owner}/{repo}", commit_sha.lower()
    if len(segments) < 2 or any(not _GITLAB_SEGMENT.fullmatch(segment) for segment in segments):
        return None, None, None
    return provider, "/".join(segments), commit_sha.lower()


def _first_item(payload: Any) -> Optional[Dict[str, Any]]:
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return payload[0]
    return None


async def fetch_file_history(repository: Any, path: str) -> HistoryInfo:
    provider, project, commit_sha = parse_repository_target(repository)
    if provider is None or project is None or commit_sha is None:
        return _unavailable(
            "unsupported",
            "Артефакт не содержит поддерживаемого публичного repository/commit_sha.",
        )
    if not is_safe_source_path(path):
        return _unavailable("unsupported", "Путь находки не прошёл проверку.", provider)
    cache_key = f"{provider}:{project}:{commit_sha}:{path}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    if provider == "github":
        owner, repo = project.split("/", 1)
        url = f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/commits"
        params = {"sha": commit_sha, "path": path, "per_page": 1}
    else:
        url = f"https://gitlab.com/api/v4/projects/{quote(project, safe='')}/repository/commits"
        params = {"ref_name": commit_sha, "path": path, "per_page": 1}

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=2.0),
            follow_redirects=False,
            trust_env=False,
            headers={
                "Accept": "application/json",
                "User-Agent": "nedra-responsibility/1.0",
            },
        ) as client:
            response = await client.get(url, params=params)
            raw = await response.aread()
            if len(raw) > MAX_RESPONSE_BYTES:
                result = _unavailable("failed", "Ответ provider превысил допустимый размер.", provider)
                _cache_put(cache_key, result)
                return result
            if response.status_code in {301, 302, 303, 307, 308}:
                result = _unavailable("failed", "Provider вернул redirect; переход отклонён.", provider)
            elif response.status_code == 429 or (
                response.status_code == 403
                and response.headers.get("x-ratelimit-remaining") == "0"
            ):
                result = _unavailable("rate_limited", "Provider временно ограничил публичные запросы.", provider)
            elif response.status_code != 200:
                result = _unavailable(
                    "unavailable" if response.status_code in {401, 403, 404} else "failed",
                    f"Provider ответил HTTP {response.status_code}; история недоступна.",
                    provider,
                )
            else:
                try:
                    payload = response.json()
                except ValueError:
                    payload = None
                item = _first_item(payload)
                if item is None:
                    result = _unavailable(
                        "unavailable",
                        "На frozen commit не найден коммит для этого файла.",
                        provider,
                    )
                elif provider == "github":
                    commit_data = item.get("commit") if isinstance(item.get("commit"), dict) else {}
                    author_data = commit_data.get("author") if isinstance(commit_data.get("author"), dict) else {}
                    result = HistoryInfo(
                        status="available",
                        provider=provider,
                        reason="Последний коммит файла на frozen commit.",
                        evidenceKind="last_file_commit",
                        notBlame=True,
                        commit=CommitEvidence(
                            sha=str(item.get("sha") or ""),
                            author=str(author_data.get("name") or "") or None,
                            date=str(author_data.get("date") or "") or None,
                            url=str(item.get("html_url") or "") or None,
                            message=(str(commit_data.get("message") or "")[:240] or None),
                        ),
                        lineBlame=LineBlameInfo(),
                        limitations=[
                            "Это последний коммит файла, не автор уязвимости.",
                            "Line blame не запрашивался и не подменяет file history.",
                        ],
                    )
                else:
                    result = HistoryInfo(
                        status="available",
                        provider=provider,
                        reason="Последний коммит файла на frozen commit.",
                        evidenceKind="last_file_commit",
                        notBlame=True,
                        commit=CommitEvidence(
                            sha=str(item.get("id") or ""),
                            author=str(item.get("author_name") or "") or None,
                            date=str(item.get("committed_date") or item.get("authored_date") or "") or None,
                            url=str(item.get("web_url") or "") or None,
                            message=(str(item.get("title") or item.get("message") or "")[:240] or None),
                        ),
                        lineBlame=LineBlameInfo(),
                        limitations=[
                            "Это последний коммит файла, не автор уязвимости.",
                            "Line blame не запрашивался и не подменяет file history.",
                        ],
                    )
    except (httpx.HTTPError, OSError) as exc:
        result = _unavailable(
            "failed",
            f"Provider временно недоступен ({type(exc).__name__}); роль остаётся доступной.",
            provider,
        )
    _cache_put(cache_key, result)
    return result
