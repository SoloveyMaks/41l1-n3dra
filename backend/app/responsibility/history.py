"""Bounded public file-history and PR evidence for one frozen commit.

This is intentionally *not* blame and not vulnerability attribution. The
provider returns the last commit for one file. Associated PRs are looked up for
that file commit (not for the current branch head). Reviews show both the latest
opinion and the reconstructed decision; no comment can silently erase an
approval, and no unseen page can turn an old approval into an active one.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import zlib
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
MAX_COMPRESSED_BYTES = 256 * 1024
REQUEST_TIMEOUT_SECONDS = 4.0
ENRICHMENT_DEADLINE_SECONDS = 9.0
CACHE_TTL_SECONDS = 15 * 60
CACHE_ERROR_TTL_SECONDS = 90
PROVIDER_COOLDOWN_SECONDS = 60
MAX_CACHE_ENTRIES = 256
MAX_PRS = 3
MAX_PRS_WITH_REVIEWS = 2
MAX_REVIEWERS = 20
MAX_REVIEWS_PER_PAGE = 100
_ALLOWED_HOSTS = {"github.com": "github", "gitlab.com": "gitlab"}
_GITHUB_OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_GITHUB_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_GITLAB_SEGMENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,254}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_CACHE: Dict[str, Tuple[float, HistoryInfo]] = {}
_COOLDOWN: Dict[str, float] = {}


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


def _safe_provider_url(value: Any, provider: Optional[str]) -> Optional[str]:
    if not isinstance(value, str) or provider not in _ALLOWED_HOSTS.values():
        return None
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return None
    if parts.scheme != "https" or parts.username or parts.password:
        return None
    host_provider = _ALLOWED_HOSTS.get(parts.netloc.lower())
    if host_provider != provider:
        return None
    return value.strip()


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
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "User-Agent": "nedra-responsibility/1.0",
        },
    )


async def _read_limited_json_body(response: httpx.Response) -> Tuple[str, Optional[bytes], str]:
    encoding = response.headers.get("content-encoding", "").strip().lower()
    if encoding not in {"", "identity", "gzip", "x-gzip", "deflate"}:
        return "failed", None, "Provider вернул неподдерживаемый Content-Encoding."
    decoder = None
    if encoding in {"gzip", "x-gzip"}:
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    elif encoding == "deflate":
        decoder = zlib.decompressobj(zlib.MAX_WBITS)
    raw_total = 0
    decoded = bytearray()
    try:
        async for chunk in response.aiter_raw():
            raw_total += len(chunk)
            if raw_total > MAX_COMPRESSED_BYTES:
                return "failed", None, "Ответ provider превысил допустимый размер."
            if decoder is None:
                if len(decoded) + len(chunk) > MAX_RESPONSE_BYTES:
                    return "failed", None, "Ответ provider превысил допустимый размер."
                decoded.extend(chunk)
                continue
            pending = chunk
            while pending:
                remaining = MAX_RESPONSE_BYTES - len(decoded)
                if remaining <= 0:
                    return "failed", None, "Ответ provider превысил допустимый размер."
                part = decoder.decompress(pending, min(remaining, 65536) + 1)
                if len(part) > remaining:
                    return "failed", None, "Ответ provider превысил допустимый размер."
                decoded.extend(part)
                pending = decoder.unconsumed_tail
        if decoder is not None:
            remaining = MAX_RESPONSE_BYTES - len(decoded)
            if remaining <= 0:
                return "failed", None, "Ответ provider превысил допустимый размер."
            tail = decoder.flush()
            if len(tail) > remaining:
                return "failed", None, "Ответ provider превысил допустимый размер."
            decoded.extend(tail)
            if not decoder.eof:
                return "failed", None, "Provider вернул повреждённый Content-Encoding."
        return "ok", bytes(decoded), ""
    except (httpx.HTTPError, OSError, zlib.error):
        return "failed", None, "Provider оборвал ответ."


async def _fetch_json_page(
    client: httpx.AsyncClient, url: str, params: Dict[str, Any]
) -> Tuple[str, Any, str, Dict[str, str]]:
    """Stream a response with compressed and decompressed byte ceilings.

    Raw bytes are never trusted from Content-Length. gzip/deflate are decoded
    incrementally with zlib max_length, and unsupported encodings fail closed.
    """
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
            async with client.stream("GET", url, params=params) as response:
                headers = {key.lower(): value for key, value in response.headers.items()}
                status = response.status_code
                if status in {301, 302, 303, 307, 308}:
                    return "failed", None, "Provider вернул redirect; переход отклонён.", headers
                if status == 429 or (status == 403 and headers.get("x-ratelimit-remaining") == "0"):
                    return "rate_limited", None, "Provider временно ограничил публичные запросы.", headers
                if status in {401, 403, 404}:
                    return "unavailable", None, f"Provider ответил HTTP {status}.", headers
                if status != 200:
                    return "failed", None, f"Provider ответил HTTP {status}.", headers
                body_kind, body, body_reason = await _read_limited_json_body(response)
                if body_kind != "ok" or body is None:
                    return body_kind, None, body_reason, headers
                try:
                    return "ok", json.loads(body.decode("utf-8")), "", headers
                except (UnicodeDecodeError, ValueError):
                    return "failed", None, "Provider вернул не-JSON ответ.", headers
    except TimeoutError:
        return "failed", None, "Provider превысил общий deadline запроса.", {}
    except (httpx.HTTPError, OSError) as exc:
        return "failed", None, f"Provider временно недоступен ({type(exc).__name__}).", {}


async def _fetch_json(
    client: httpx.AsyncClient, url: str, params: Dict[str, Any]
) -> Tuple[str, Any, str]:
    kind, payload, reason, _ = await _fetch_json_page(client, url, params)
    return kind, payload, reason


def _has_next_page(headers: Dict[str, str]) -> bool:
    link = headers.get("link", "")
    return 'rel="next"' in link


def _review_decisions(
    raw_reviews: Any,
    *,
    head_sha: Optional[str],
    merged_at: Optional[str],
    incomplete: bool,
) -> Tuple[List[ReviewEvidence], bool, str]:
    """Reconstruct review decisions without erasing approvals with comments.

    GitHub's review list is chronological. A later COMMENTED review does not
    revoke an APPROVED decision, while CHANGES_REQUESTED and DISMISSED do. The
    latest opinion is kept separately from the decision. If pagination or the
    head commit is not proven, `appliesToMerge` is false.
    """
    if not isinstance(raw_reviews, list):
        return [], False, "Provider не вернул список reviews."
    by_reviewer: Dict[str, List[Dict[str, Any]]] = {}
    for item in raw_reviews:
        if not isinstance(item, dict):
            continue
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
        login = user.get("login") if isinstance(user.get("login"), str) else ""
        if not login:
            continue
        submitted = item.get("submitted_at") if isinstance(item.get("submitted_at"), str) else None
        if merged_at and submitted and submitted > merged_at:
            # A review after merge cannot be a reviewer decision before merge.
            continue
        by_reviewer.setdefault(login, []).append(item)

    ordered_reviewers = sorted(
        by_reviewer,
        key=lambda login: (
            str((by_reviewer[login][-1].get("submitted_at") if by_reviewer[login] else "") or ""),
            login,
        ),
    )
    truncated = len(ordered_reviewers) > MAX_REVIEWERS
    results: List[ReviewEvidence] = []
    for login in ordered_reviewers[:MAX_REVIEWERS]:
        reviews = sorted(
            by_reviewer[login],
            key=lambda item: (str(item.get("submitted_at") or ""), str(item.get("id") or "")),
        )
        latest = reviews[-1]
        opinion_state = str(latest.get("state") or "COMMENTED").upper()
        decision_state = "NONE"
        decision_at: Optional[str] = None
        decision_commit: Optional[str] = None
        for review in reviews:
            state = str(review.get("state") or "COMMENTED").upper()
            submitted = review.get("submitted_at") if isinstance(review.get("submitted_at"), str) else None
            commit = review.get("commit_id") if isinstance(review.get("commit_id"), str) else None
            if state in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
                decision_state = state
                decision_at = submitted
                decision_commit = commit
            # COMMENTED/PENDING do not erase an earlier approval or change request.
        user = latest.get("user") if isinstance(latest.get("user"), dict) else {}
        submitted = latest.get("submitted_at") if isinstance(latest.get("submitted_at"), str) else None
        limitation: Optional[str] = None
        applies = False
        if incomplete:
            limitation = "Список reviews неполный: unseen dismissal/approval может изменить решение."
        elif merged_at is None:
            limitation = "PR не merged; текущее review-состояние нельзя выдать за действующее перед merge."
        elif decision_state == "NONE":
            limitation = "Есть только комментарии; активного решения review нет."
        elif not decision_at:
            limitation = "У решения нет submitted_at; актуальность к merge не доказана."
        elif not head_sha or decision_commit != head_sha:
            limitation = "Решение относится к другому/неизвестному head commit; подтверждение к merged head отсутствует."
        else:
            applies = True
        results.append(
            ReviewEvidence(
                reviewer=_person(user.get("login"), user.get("name")),
                state=decision_state,
                opinionState=opinion_state,
                submittedAt=submitted,
                decisionAt=decision_at,
                appliesToMerge=applies,
                limitation=limitation,
            )
        )
    reason = "Список reviews неполный; активность approval не доказана." if incomplete else ""
    return results, truncated, reason


async def _github_pull_requests(
    client: httpx.AsyncClient,
    owner: str,
    repo: str,
    association_sha: str,
) -> Tuple[List[PullRequestEvidence], str, str, bool]:
    kind, payload, reason = await _fetch_json(
        client,
        f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/commits/{association_sha}/pulls",
        {"per_page": MAX_PRS + 1},
    )
    if kind != "ok":
        return [], kind, reason, False
    items = payload if isinstance(payload, list) else []
    if not items:
        return [], "none", "Для последнего коммита файла нет связанного PR.", False
    truncated = len(items) > MAX_PRS
    first = [item for item in items[:MAX_PRS] if isinstance(item, dict)]
    # Bounded concurrency: at most two detail requests and two review-page
    # requests in flight; never unbounded fan-out.
    detail_results = await asyncio.gather(
        *[
            _fetch_json(
                client,
                f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/pulls/{item.get('number')}",
                {},
            )
            for item in first[:MAX_PRS_WITH_REVIEWS]
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

    review_results = await asyncio.gather(
        *[
            _fetch_json_page(
                client,
                f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/pulls/{item.get('number')}/reviews",
                {"per_page": MAX_REVIEWS_PER_PAGE},
            )
            for item in first[:MAX_PRS_WITH_REVIEWS]
            if isinstance(item.get("number"), int)
        ],
        return_exceptions=True,
    )
    review_by_number: Dict[int, Tuple[str, Any, str, Dict[str, str]]] = {}
    for item, result in zip(first[:MAX_PRS_WITH_REVIEWS], review_results):
        number = item.get("number") if isinstance(item, dict) else None
        if not isinstance(number, int) or isinstance(result, Exception):
            continue
        if isinstance(result, tuple) and len(result) == 4:
            review_by_number[number] = result

    pull_requests: List[PullRequestEvidence] = []
    for index, item in enumerate(first):
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
        reviews_incomplete = False
        if index < MAX_PRS_WITH_REVIEWS:
            review_kind, review_payload, review_reason, review_headers = review_by_number.get(
                number, ("failed", None, "Reviews не загрузились.", {})
            )
            if review_kind == "ok":
                page_incomplete = _has_next_page(review_headers) or (
                    isinstance(review_payload, list) and len(review_payload) >= MAX_REVIEWS_PER_PAGE
                )
                reviews, reviews_truncated, reviews_reason = _review_decisions(
                    review_payload,
                    head_sha=head_sha if merged_at is not None else None,
                    merged_at=merged_at,
                    incomplete=page_incomplete,
                )
                reviews_incomplete = page_incomplete
                reviews_status = "incomplete" if page_incomplete else "available"
            else:
                reviews_status = review_kind
                reviews_reason = review_reason
        else:
            reviews_status = "not_loaded"
            reviews_reason = "Показаны не все PR; reviews для дополнительных PR не загружались."
            limitations.append(reviews_reason)
        if merged_at is None:
            limitations.append("PR не merged: approvals/reviews не выдаются за действующие перед merge.")
        pull_requests.append(
            PullRequestEvidence(
                number=number,
                title=str(detail.get("title") or ""),
                url=_safe_provider_url(detail.get("html_url"), "github"),
                author=_identity(user.get("login"), user.get("name")),
                mergedBy=_identity(merged_by.get("login"), merged_by.get("name")),
                mergedAt=merged_at,
                headSha=head_sha,
                reviews=reviews,
                reviewsStatus=reviews_status,
                reviewsReason=reviews_reason,
                reviewsTruncated=reviews_truncated,
                reviewsIncomplete=reviews_incomplete,
                limitations=limitations,
            )
        )
    return pull_requests, "available", "", truncated


async def _gitlab_pull_requests(
    client: httpx.AsyncClient,
    project: str,
    association_sha: str,
) -> Tuple[List[PullRequestEvidence], str, str, bool]:
    kind, payload, reason = await _fetch_json(
        client,
        f"https://gitlab.com/api/v4/projects/{quote(project, safe='')}/repository/commits/{association_sha}/merge_requests",
        {"per_page": MAX_PRS + 1},
    )
    if kind != "ok":
        return [], kind, reason, False
    items = payload if isinstance(payload, list) else []
    if not items:
        return [], "none", "Для последнего коммита файла нет связанного MR.", False
    result: List[PullRequestEvidence] = []
    for item in items[:MAX_PRS]:
        author = item.get("author") if isinstance(item.get("author"), dict) else {}
        merged_by = item.get("merged_by") if isinstance(item.get("merged_by"), dict) else {}
        merged_at = item.get("merged_at") if isinstance(item.get("merged_at"), str) else None
        result.append(
            PullRequestEvidence(
                number=int(item.get("iid") or 0),
                title=str(item.get("title") or ""),
                url=_safe_provider_url(item.get("web_url"), "gitlab"),
                author=_identity(author.get("username"), author.get("name")),
                mergedBy=_identity(merged_by.get("username"), merged_by.get("name")),
                mergedAt=merged_at,
                headSha=None,
                reviews=[],
                reviewsStatus="unsupported",
                reviewsReason="Публичный GitLab API без токена не даёт доказать историческое review-состояние на merge.",
                reviewsTruncated=False,
                reviewsIncomplete=False,
                limitations=["GitLab reviews/approvals не выдаются за подтверждённые на head commit."],
            )
        )
    return result, "available", "", len(items) > MAX_PRS


async def _fetch_pull_requests(
    provider: str,
    project: str,
    association_sha: str,
) -> Tuple[List[PullRequestEvidence], str, str, bool]:
    try:
        async with _client() as client:
            if provider == "github":
                owner, repo = project.split("/", 1)
                return await _github_pull_requests(client, owner, repo, association_sha)
            return await _gitlab_pull_requests(client, project, association_sha)
    except (httpx.HTTPError, OSError) as exc:
        return [], "failed", f"PR provider недоступен ({type(exc).__name__}).", False


async def _fetch_file_history(repository: Any, path: str) -> HistoryInfo:
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
    if _COOLDOWN.get(provider, 0.0) > time.monotonic():
        return _unavailable(
            "rate_limited",
            "Provider в cooldown после 429; запросы временно не повторяются.",
            provider,
        )

    if provider == "github":
        owner, repo = project.split("/", 1)
        url = f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/commits"
        params = {"sha": commit_sha, "path": path, "per_page": 1}
    else:
        url = f"https://gitlab.com/api/v4/projects/{quote(project, safe='')}/repository/commits"
        params = {"ref_name": commit_sha, "path": path, "per_page": 1}

    async with _client() as client:
        kind, payload, reason = await _fetch_json(client, url, params)
    if kind == "rate_limited":
        _COOLDOWN[provider] = time.monotonic() + PROVIDER_COOLDOWN_SECONDS
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
                    url=_safe_provider_url(item.get("html_url"), provider),
                    message=(str(commit_data.get("message") or "")[:240] or None),
                ),
                frozenCommitSha=commit_sha,
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
                    author=_identity(None, item.get("author_name")),
                    committer=_identity(None, item.get("committer_name")),
                    date=str(item.get("committed_date") or item.get("authored_date") or "") or None,
                    url=_safe_provider_url(item.get("web_url"), provider),
                    message=(str(item.get("title") or item.get("message") or "")[:240] or None),
                ),
                frozenCommitSha=commit_sha,
                lineBlame=LineBlameInfo(),
                limitations=[
                    "Это последний коммит файла, не автор уязвимости.",
                    "Author и committer — публичные identity коммита, не виновные.",
                    "Line blame не запрашивался и не подменяет file history.",
                ],
            )

    if result.status == "available" and result.commit:
        association_sha = result.commit.sha or commit_sha
        pull_requests, pr_status, pr_reason, truncated = await _fetch_pull_requests(
            provider, project, association_sha
        )
        if pr_status == "rate_limited":
            _COOLDOWN[provider] = time.monotonic() + PROVIDER_COOLDOWN_SECONDS
        result = result.model_copy(
            update={
                "associationCommitSha": association_sha,
                "pullRequests": pull_requests,
                "pullRequestsStatus": pr_status,
                "pullRequestsReason": pr_reason,
                "pullRequestsTruncated": truncated,
                "limitations": result.limitations
                + [
                    "PR association — только факт связи с association commit; reviews не определяют виновного.",
                ],
            }
        )
    _cache_put(cache_key, result)
    return result


async def fetch_file_history(repository: Any, path: str) -> HistoryInfo:
    try:
        return await asyncio.wait_for(
            _fetch_file_history(repository, path),
            timeout=ENRICHMENT_DEADLINE_SECONDS,
        )
    except asyncio.TimeoutError:
        return _unavailable(
            "failed",
            "Ответ provider превысил общий deadline enrichment; частичные данные не выдаются.",
        )
