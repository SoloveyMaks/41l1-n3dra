"""Bounded public file-history and PR evidence for one frozen commit.

This is intentionally *not* blame and not vulnerability attribution: the
provider returns the last commit for one file and PRs associated with that exact
commit. Reviews are shown only as provider facts, with an explicit limitation
when they cannot be proven current for the merged head commit.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlsplit

import httpx

from backend.app.codefacts.source import is_safe_source_path
from backend.app.responsibility.models import (
    CommitEvidence,
    HistoryInfo,
    LineBlameInfo,
    PersonEvidence,
    PullRequestEvidence,
    ReviewEvidence,
)

MAX_RESPONSE_BYTES = 256 * 1024
REQUEST_TIMEOUT_SECONDS = 4.0
CACHE_TTL_SECONDS = 15 * 60
CACHE_ERROR_TTL_SECONDS = 90
MAX_CACHE_ENTRIES = 256
MAX_PRS = 3
MAX_PRS_WITH_REVIEWS = 2
MAX_REVIEWERS = 24
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
        pullRequestsStatus="unavailable",
        pullRequestsReason="История коммита недоступна, PR не запрашивались.",
        limitations=[
            "Это последний коммит файла, а не автор уязвимости.",
            "Line blame не поддерживается без отдельного безопасного провайдера.",
            "PR/reviews не определяют виновного и не доказывают внесение уязвимости.",
        ],
    )


def _person(login: Any = None, name: Any = None) -> PersonEvidence:
    return PersonEvidence(
        login=str(login) if isinstance(login, str) and login else None,
        displayName=str(name) if isinstance(name, str) and name else None,
    )


def _identity(login: Any, name: Any = None) -> Optional[PersonEvidence]:
    person = _person(login, name)
    return person if person.login or person.displayName else None


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


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=2.0),
        follow_redirects=False,
        trust_env=False,
        headers={"Accept": "application/json", "User-Agent": "nedra-responsibility/1.0"},
    )


async def _fetch_json(
    client: httpx.AsyncClient, url: str, params: Dict[str, Any]
) -> Tuple[str, Any, str]:
    try:
        response = await client.get(url, params=params)
        raw = await response.aread()
        if len(raw) > MAX_RESPONSE_BYTES:
            return "failed", None, "Ответ provider превысил допустимый размер."
        if response.status_code in {301, 302, 303, 307, 308}:
            return "failed", None, "Provider вернул redirect; переход отклонён."
        if response.status_code == 429 or (
            response.status_code == 403
            and response.headers.get("x-ratelimit-remaining") == "0"
        ):
            return "rate_limited", None, "Provider временно ограничил публичные запросы."
        if response.status_code in {401, 403, 404}:
            return "unavailable", None, f"Provider ответил HTTP {response.status_code}."
        if response.status_code != 200:
            return "failed", None, f"Provider ответил HTTP {response.status_code}."
        try:
            return "ok", response.json(), ""
        except ValueError:
            return "failed", None, "Provider вернул не-JSON ответ."
    except (httpx.HTTPError, OSError) as exc:
        return "failed", None, f"Provider временно недоступен ({type(exc).__name__})."


def _review_last_decisions(
    raw_reviews: Any,
    *,
    head_sha: Optional[str],
    merged_at: Optional[str],
) -> Tuple[List[ReviewEvidence], bool, str]:
    if not isinstance(raw_reviews, list):
        return [], False, "Provider не вернул список reviews."
    by_reviewer: Dict[str, Dict[str, Any]] = {}
    for item in raw_reviews:
        if not isinstance(item, dict):
            continue
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
        login = user.get("login") if isinstance(user.get("login"), str) else ""
        if not login:
            continue
        submitted = item.get("submitted_at") if isinstance(item.get("submitted_at"), str) else None
        if merged_at and submitted and submitted > merged_at:
            # A review submitted after merge cannot be the reviewer decision
            # "before merge"; do not let a later approval rewrite history.
            continue
        previous = by_reviewer.get(login)
        if previous is None or (submitted or "") >= (previous.get("submitted_at") or ""):
            by_reviewer[login] = item
    ordered = sorted(by_reviewer.values(), key=lambda item: (str(item.get("submitted_at") or ""), str((item.get("user") or {}).get("login") or "")))
    truncated = len(ordered) > MAX_REVIEWERS
    results: List[ReviewEvidence] = []
    for item in ordered[:MAX_REVIEWERS]:
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
        login = user.get("login") if isinstance(user.get("login"), str) else None
        name = user.get("name") if isinstance(user.get("name"), str) else None
        submitted = item.get("submitted_at") if isinstance(item.get("submitted_at"), str) else None
        state = str(item.get("state") or "COMMENTED").upper()
        review_commit = item.get("commit_id") if isinstance(item.get("commit_id"), str) else None
        limitation = None
        applies = False
        if merged_at is None:
            limitation = "PR не merged; текущее review-состояние нельзя выдать за действующее перед merge."
        elif not submitted:
            limitation = "У review нет submitted_at; актуальность к merge не доказана."
        elif not head_sha or review_commit != head_sha:
            limitation = "Review относится к другому/неизвестному head commit; подтверждение к merged head отсутствует."
        elif review_commit == head_sha and submitted <= merged_at:
            applies = True
        results.append(
            ReviewEvidence(
                reviewer=_person(login, name),
                state=state,
                submittedAt=submitted,
                appliesToMerge=applies,
                limitation=limitation,
            )
        )
    return results, truncated, ""


async def _github_pull_requests(
    client: httpx.AsyncClient,
    owner: str,
    repo: str,
    commit_sha: str,
) -> Tuple[List[PullRequestEvidence], str, str, bool]:
    kind, payload, reason = await _fetch_json(
        client,
        f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/commits/{commit_sha}/pulls",
        {"per_page": MAX_PRS + 1},
    )
    if kind != "ok":
        return [], kind, reason, False
    items = payload if isinstance(payload, list) else []
    if not items:
        return [], "none", "Для frozen commit нет связанного PR.", False
    truncated = len(items) > MAX_PRS
    pull_requests: List[PullRequestEvidence] = []
    # Details and reviews are fetched only for the first two PRs; the rest stay
    # listed as associated facts without pretending we reviewed them.
    detail_results = await asyncio.gather(
        *[
            _fetch_json(
                client,
                f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/pulls/{item.get('number')}",
                {},
            )
            for item in items[:MAX_PRS_WITH_REVIEWS]
            if isinstance(item.get("number"), int)
        ],
        return_exceptions=True,
    )
    detail_by_number: Dict[int, Dict[str, Any]] = {}
    for result in detail_results:
        if isinstance(result, Exception) or result[0] != "ok" or not isinstance(result[1], dict):
            continue
        detail = result[1]
        if isinstance(detail.get("number"), int):
            detail_by_number[detail["number"]] = detail

    async def build_pr(item: Dict[str, Any], index: int) -> PullRequestEvidence:
        number = item.get("number") if isinstance(item.get("number"), int) else 0
        detail = detail_by_number.get(number, item)
        user = detail.get("user") if isinstance(detail.get("user"), dict) else {}
        merged_by = detail.get("merged_by") if isinstance(detail.get("merged_by"), dict) else {}
        merged_at = detail.get("merged_at") if isinstance(detail.get("merged_at"), str) else None
        head = detail.get("head") if isinstance(detail.get("head"), dict) else {}
        head_sha = head.get("sha") if isinstance(head.get("sha"), str) else None
        limitations: List[str] = []
        reviews: List[ReviewEvidence] = []
        reviews_status = "not_loaded"
        reviews_reason = ""
        reviews_truncated = False
        if index < MAX_PRS_WITH_REVIEWS:
            review_kind, review_payload, review_reason = await _fetch_json(
                client,
                f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/pulls/{number}/reviews",
                {"per_page": 100},
            )
            if review_kind == "ok":
                reviews, reviews_truncated, reviews_reason = _review_last_decisions(
                    review_payload,
                    head_sha=head_sha,
                    merged_at=merged_at,
                )
                reviews_status = "available"
            else:
                reviews_status = review_kind
                reviews_reason = review_reason
        else:
            reviews_status = "not_loaded"
            reviews_reason = "Показаны не все PR; reviews для дополнительных PR не загружались."
            limitations.append(reviews_reason)
        if merged_at is None:
            limitations.append("PR не merged: approvals/reviews не выдаются за действующие перед merge.")
        return PullRequestEvidence(
            number=number,
            title=str(detail.get("title") or ""),
            url=str(detail.get("html_url") or "") or None,
            author=_identity(user.get("login"), user.get("name")),
            mergedBy=_identity(merged_by.get("login"), merged_by.get("name")),
            mergedAt=merged_at,
            headSha=head_sha,
            reviews=reviews,
            reviewsStatus=reviews_status,
            reviewsReason=reviews_reason,
            reviewsTruncated=reviews_truncated,
            limitations=limitations,
        )

    for index, item in enumerate(items[:MAX_PRS]):
        if not isinstance(item, dict):
            continue
        pull_requests.append(await build_pr(item, index))
    return pull_requests, "available", "", truncated


async def _gitlab_pull_requests(
    client: httpx.AsyncClient,
    project: str,
    commit_sha: str,
) -> Tuple[List[PullRequestEvidence], str, str, bool]:
    kind, payload, reason = await _fetch_json(
        client,
        f"https://gitlab.com/api/v4/projects/{quote(project, safe='')}/repository/commits/{commit_sha}/merge_requests",
        {"per_page": MAX_PRS + 1},
    )
    if kind != "ok":
        return [], kind, reason, False
    items = payload if isinstance(payload, list) else []
    if not items:
        return [], "none", "Для frozen commit нет связанного MR.", False
    bounded = items[:MAX_PRS]
    result: List[PullRequestEvidence] = []
    for item in bounded:
        author = item.get("author") if isinstance(item.get("author"), dict) else {}
        merged_by = item.get("merged_by") if isinstance(item.get("merged_by"), dict) else {}
        merged_at = item.get("merged_at") if isinstance(item.get("merged_at"), str) else None
        result.append(
            PullRequestEvidence(
                number=int(item.get("iid") or 0),
                title=str(item.get("title") or ""),
                url=str(item.get("web_url") or "") or None,
                author=_identity(author.get("username"), author.get("name")),
                mergedBy=_identity(merged_by.get("username"), merged_by.get("name")),
                mergedAt=merged_at,
                headSha=None,
                reviews=[],
                reviewsStatus="unsupported",
                reviewsReason="Публичный GitLab API без токена не даёт доказать историческое review-состояние на merge.",
                reviewsTruncated=False,
                limitations=["GitLab reviews/approvals не выдаются за подтверждённые на head commit."],
            )
        )
    return result, "available", "", len(items) > MAX_PRS


async def _fetch_pull_requests(
    provider: str,
    project: str,
    commit_sha: str,
) -> Tuple[List[PullRequestEvidence], str, str, bool]:
    try:
        async with _client() as client:
            if provider == "github":
                owner, repo = project.split("/", 1)
                return await _github_pull_requests(client, owner, repo, commit_sha)
            return await _gitlab_pull_requests(client, project, commit_sha)
    except (httpx.HTTPError, OSError) as exc:
        return [], "failed", f"PR provider недоступен ({type(exc).__name__}).", False


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

    async with _client() as client:
        kind, payload, reason = await _fetch_json(client, url, params)
    if kind != "ok":
        result = _unavailable(kind, reason, provider)
    else:
        item = _first_item(payload)
        if item is None:
            result = _unavailable("unavailable", "На frozen commit не найден коммит для этого файла.", provider)
        elif provider == "github":
            commit_data = item.get("commit") if isinstance(item.get("commit"), dict) else {}
            author_data = commit_data.get("author") if isinstance(commit_data.get("author"), dict) else {}
            committer_data = commit_data.get("committer") if isinstance(commit_data.get("committer"), dict) else {}
            author_user = item.get("author") if isinstance(item.get("author"), dict) else {}
            committer_user = item.get("committer") if isinstance(item.get("committer"), dict) else {}
            result = HistoryInfo(
                status="available",
                provider=provider,
                reason="Последний коммит файла на frozen commit.",
                evidenceKind="last_file_commit",
                notBlame=True,
                commit=CommitEvidence(
                    sha=str(item.get("sha") or ""),
                    author=_identity(author_user.get("login"), author_data.get("name")),
                    committer=_identity(committer_user.get("login"), committer_data.get("name")),
                    date=str(author_data.get("date") or committer_data.get("date") or "") or None,
                    url=str(item.get("html_url") or "") or None,
                    message=(str(commit_data.get("message") or "")[:240] or None),
                ),
                lineBlame=LineBlameInfo(),
                limitations=[
                    "Это последний коммит файла, не автор уязвимости.",
                    "Author и committer — публичные identity коммита, не виновные.",
                    "Line blame не запрашивался и не подменяет file history.",
                    "Review/PR fact не доказывает внесение уязвимости.",
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
                    author=_identity(item.get("author_name"), item.get("author_name")),
                    committer=_identity(item.get("committer_name"), item.get("committer_name")),
                    date=str(item.get("committed_date") or item.get("authored_date") or "") or None,
                    url=str(item.get("web_url") or "") or None,
                    message=(str(item.get("title") or item.get("message") or "")[:240] or None),
                ),
                lineBlame=LineBlameInfo(),
                limitations=[
                    "Это последний коммит файла, не автор уязвимости.",
                    "Author и committer — публичные identity коммита, не виновные.",
                    "Line blame не запрашивался и не подменяет file history.",
                ],
            )

    if result.status == "available":
        pull_requests, pr_status, pr_reason, truncated = await _fetch_pull_requests(
            provider, project, commit_sha
        )
        result = result.model_copy(
            update={
                "pullRequests": pull_requests,
                "pullRequestsStatus": pr_status,
                "pullRequestsReason": pr_reason,
                "pullRequestsTruncated": truncated,
                "limitations": result.limitations
                + ["PR association — только факт связи с frozen commit; reviews не определяют виновного."],
            }
        )
    _cache_put(cache_key, result)
    return result
