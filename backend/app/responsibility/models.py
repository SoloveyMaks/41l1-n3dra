"""Public DTO for read-only finding responsibility enrichment.

This is deliberately separate from Finding: nothing here changes fingerprint,
severity, confidence, counts, verdict, canonical reports or analyzer facts.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class RoleSuggestion(BaseModel):
    role: str
    basis: str
    confidence: str = "medium"


class CodeownersInfo(BaseModel):
    status: str = "unavailable"  # available | absent | unavailable
    file: Optional[str] = None
    matchedPattern: Optional[str] = None
    line: Optional[int] = None
    owners: List[str] = Field(default_factory=list)
    basis: str = ""
    limitations: List[str] = Field(default_factory=list)


class LineBlameInfo(BaseModel):
    status: str = "unsupported"  # unsupported | available
    reason: str = "Line blame не запрашивался: безопасной поддержки без токена нет."


class PersonEvidence(BaseModel):
    """Public provider account evidence; git names alone never establish a login."""

    displayName: Optional[str] = None
    login: Optional[str] = None
    provider: Optional[str] = None
    profileUrl: Optional[str] = None
    isBot: Optional[bool] = None


class CommitEvidence(BaseModel):
    sha: str
    author: Optional[PersonEvidence] = None
    committer: Optional[PersonEvidence] = None
    date: Optional[str] = None
    url: Optional[str] = None
    message: Optional[str] = None


class ReviewEvidence(BaseModel):
    reviewer: PersonEvidence
    state: str  # decision: APPROVED | CHANGES_REQUESTED | DISMISSED | NONE/UNKNOWN
    opinionState: str = "UNKNOWN"  # latest review opinion, including COMMENTED
    submittedAt: Optional[str] = None
    decisionAt: Optional[str] = None
    appliesToMerge: bool = False
    limitation: Optional[str] = None


class PullRequestEvidence(BaseModel):
    number: int
    title: str
    url: Optional[str] = None
    author: Optional[PersonEvidence] = None
    mergedBy: Optional[PersonEvidence] = None
    mergedAt: Optional[str] = None
    headSha: Optional[str] = None
    reviews: List[ReviewEvidence] = Field(default_factory=list)
    reviewsStatus: str = "unavailable"
    reviewsReason: str = ""
    reviewsTruncated: bool = False
    reviewsIncomplete: bool = False
    limitations: List[str] = Field(default_factory=list)


class HistoryInfo(BaseModel):
    status: str = "unavailable"  # available | unavailable | rate_limited | failed | unsupported
    provider: Optional[str] = None
    reason: str = ""
    evidenceKind: str = "last_file_commit"
    notBlame: bool = True
    commit: Optional[CommitEvidence] = None
    associationCommitSha: Optional[str] = None
    frozenCommitSha: Optional[str] = None
    pullRequests: List[PullRequestEvidence] = Field(default_factory=list)
    pullRequestsStatus: str = "not_requested"  # available | none | unavailable | rate_limited | failed | unsupported | not_requested
    pullRequestsReason: str = ""
    pullRequestsTruncated: bool = False
    lineBlame: LineBlameInfo = Field(default_factory=LineBlameInfo)
    limitations: List[str] = Field(default_factory=list)


class ResponsibilityScope(BaseModel):
    runId: str
    artifactId: Optional[str] = None
    snapshotId: Optional[str] = None
    commitSha: Optional[str] = None
    path: str


class ResponsibilityResponse(BaseModel):
    scope: ResponsibilityScope
    findingId: str
    analyzer: str
    ruleId: str
    suggestedRole: RoleSuggestion
    codeowners: CodeownersInfo
    history: HistoryInfo
    limitations: List[str] = Field(default_factory=list)
