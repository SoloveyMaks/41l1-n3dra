from __future__ import annotations
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Tuple, Union
from pydantic import BaseModel, Field, model_validator

from backend.app.security.secrets import reject_reserved_envelope


class PortType(str, Enum):
    TRIGGER = "trigger"
    ARTIFACT = "artifact"
    SCAN_RESULT = "scan_result"
    FINDINGS_BUNDLE = "findings_bundle"
    POLICY_RESULT = "policy_result"
    EXPLAIN_RESULT = "explain_result"
    AI_SCAN_RESULT = "ai_scan_result"
    REPORT = "report"
    CONDITION_BRANCH = "condition_branch"
    DELIVERY_RESULT = "delivery_result"
    CUSTOM_FINDINGS = "custom_findings"


class NodeKind(str, Enum):
    MANUAL_TRIGGER = "manual_trigger"
    SCHEDULE_TRIGGER = "schedule_trigger"
    PROJECT_INPUT = "project_input"
    SECRET_SCAN = "secret_scan"
    SCA_SCAN = "sca_scan"
    KOTLIN_SAST = "kotlin_sast"
    MERGE_FINDINGS = "merge_findings"
    BRANCH_MERGE = "branch_merge"
    CONDITION = "condition"
    POLICY_GATE = "policy_gate"
    AI_EXPLAIN = "ai_explain"
    AI_SCAN = "ai_scan"
    BUILD_REPORT = "build_report"
    NOTIFY = "notify"
    CUSTOM_CHECK = "custom_check"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Confidence(str, Enum):
    CONFIRMED = "confirmed"
    POTENTIAL = "potential"
    INCONCLUSIVE = "inconclusive"


class FindingStatus(str, Enum):
    CONFIRMED = "confirmed"
    POTENTIAL = "potential"
    INCONCLUSIVE = "inconclusive"


class ExecutionStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


class NodeRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class SecurityVerdict(str, Enum):
    PENDING = "pending"
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"


class Location(BaseModel):
    path: str
    startLine: int
    endLine: int
    startColumn: int
    endColumn: int
    byteRange: Optional[List[int]] = Field(default=None, description="[start, end) byte offsets")


class TraceStep(BaseModel):
    step: int
    description: str
    location: Optional[Location] = None


class Finding(BaseModel):
    id: str
    fingerprint: str
    analyzer: str  # "secret" | "sca" | "sast"
    ruleId: str
    ruleVersion: str = "1.0.0"
    severity: Severity
    severityBasis: str
    confidence: Confidence
    confidenceBasis: str
    status: FindingStatus = FindingStatus.CONFIRMED
    primaryLocation: Location
    relatedLocations: List[Location] = Field(default_factory=list)
    trace: List[TraceStep] = Field(default_factory=list)
    redactedEvidence: str
    advisoryIds: List[str] = Field(default_factory=list)
    coverageWarnings: List[str] = Field(default_factory=list)


class Diagnostic(BaseModel):
    level: str = "info"  # "info" | "warning" | "error"
    message: str
    source: str
    details: Dict[str, Any] = Field(default_factory=dict)


class RuleObservation(BaseModel):
    """Per-file outcome of one actually instrumented analyzer rule.

    Only rules present here were observed. A missing rule must not be treated
    as checked; `not_applicable`, `skipped` and `error` are not negative proof.
    """
    rule_id: str
    rule_version: str = "1.0.0"
    path: str
    status: str = "not_applicable"  # checked | not_applicable | skipped | error
    invocations: int = 0
    findings: int = 0
    reason: str = ""


class Coverage(BaseModel):
    files_scanned: int = 0
    files_skipped: int = 0
    lines_of_code: int = 0
    rules_executed: List[str] = Field(default_factory=list)
    parser_errors: int = 0
    unresolved_dependencies: int = 0
    rule_observations: List[RuleObservation] = Field(default_factory=list)


class ScanResult(BaseModel):
    analyzer: str
    findings: List[Finding] = Field(default_factory=list)
    diagnostics: List[Diagnostic] = Field(default_factory=list)
    coverage: Coverage = Field(default_factory=Coverage)
    duration_ms: float = 0.0


class FindingsBundle(BaseModel):
    findings: List[Finding] = Field(default_factory=list)
    diagnostics: List[Diagnostic] = Field(default_factory=list)
    coverage: Coverage = Field(default_factory=Coverage)
    scan_durations: Dict[str, float] = Field(default_factory=dict)


class PolicyResult(BaseModel):
    verdict: SecurityVerdict
    reasons: List[str] = Field(default_factory=list)
    details: Dict[str, Any] = Field(default_factory=dict)
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    info_count: int = 0
    findings_count: int = 0
    coverage_warnings_count: int = 0


class AiItem(BaseModel):
    findingId: str
    explanation: str
    remediation: str
    suggestedFixedVersion: Optional[str] = None


class AiProvenance(BaseModel):
    """Where an explanation actually came from. Never contains credentials."""
    provider: str = "none"
    model: str = ""
    endpoint: str = ""
    request_id: Optional[str] = None
    latency_ms: float = 0.0
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    finish_reason: Optional[str] = None
    response_sha256: Optional[str] = None
    fallback_reason: Optional[str] = None


class AiBatchProvenance(BaseModel):
    """One bounded provider request and its validation outcome."""
    batchIndex: int
    findingIds: List[str] = Field(default_factory=list)
    status: str = "succeeded"  # "succeeded" | "fallback"
    itemCount: int = 0
    error: Optional[str] = None
    provenance: Optional[AiProvenance] = None


class SimilarFix(BaseModel):
    """One exact pgvector example returned by retrieval.

    ``status`` is part of the row, not a presentation default: a draft or demo
    example must stay visibly draft, and a row without an explicit status is
    rejected instead of being promoted to vetted by Pydantic.
    """
    findingId: Optional[str] = None
    exampleId: str
    sourceRef: str
    redactedBefore: str
    redactedFix: str
    ruleVersion: str
    verificationBasis: str
    limitations: List[str] = Field(default_factory=list)
    status: Literal["draft", "vetted"]
    distance: float
    metric: str
    embeddingSpecId: str


class RetrievalInfo(BaseModel):
    """Typed result of the optional exact-retrieval step.

    status: disabled | unavailable | ready | ok | partial
    A failed or unavailable retrieval must never fail the main analysis.
    """
    status: str = "disabled"
    reason: str = ""
    embeddingModel: str = ""
    embeddingVersion: str = ""
    dimension: Optional[int] = None
    metric: str = ""
    embeddingSpecId: Optional[str] = None


class ExplainResult(BaseModel):
    status: str = "succeeded"  # "succeeded" | "failed" | "fallback"
    model: str = "deterministic-expert-v1"
    summary: str = ""
    items: List[AiItem] = Field(default_factory=list)
    validation_errors: List[str] = Field(default_factory=list)
    raw_secrets_checked: bool = True
    provenance: Optional[AiProvenance] = None
    batch_provenance: List[AiBatchProvenance] = Field(default_factory=list)
    retrieval: Optional[RetrievalInfo] = None
    similar_fixes: List[SimilarFix] = Field(default_factory=list)


class AiObservationGrounding(BaseModel):
    """Server-verified anchor for one AI hypothesis.

    Byte bounds are recomputed from the frozen snapshot source; model-supplied
    offsets are only hints and are never stored as truth.
    """
    fileRef: str
    sourceHash: str
    quoteSha256: str
    startByte: int
    endByte: int
    modelStartLine: int
    modelEndLine: int


class AiObservation(BaseModel):
    """A finding-like AI hypothesis, not a deterministic finding.

    It deliberately has no `fingerprint`, `analyzer`, or `advisoryIds`: this
    type must never be inserted into canonical findings or policy counts.
    """
    id: str
    origin: str = "ai_hypothesis"
    ruleClass: str
    severity: Severity
    confidence: Confidence = Confidence.POTENTIAL
    confidenceBasis: str = "AI hypothesis; not confirmed by deterministic scanners"
    status: FindingStatus = FindingStatus.POTENTIAL
    primaryLocation: Location
    redactedEvidence: str
    rationale: str
    remediation: str
    grounding: AiObservationGrounding


class AiScanCoverageIssue(BaseModel):
    path: str
    code: str
    detail: str = ""


class AiScanCoverage(BaseModel):
    """Coverage of the selected AI subset, never of the whole project."""
    status: str = "degraded"  # complete | partial | degraded | rejected
    filesTotal: int = 0
    filesSelected: int = 0
    filesSent: int = 0
    bytesSelected: int = 0
    bytesSent: int = 0
    maxFiles: int = 0
    maxBytes: int = 0
    maxObservations: int = 0
    truncatedFiles: List[str] = Field(default_factory=list)
    omittedCount: int = 0
    omitted: List[AiScanCoverageIssue] = Field(default_factory=list)
    failedCount: int = 0
    failed: List[AiScanCoverageIssue] = Field(default_factory=list)
    limitsReached: List[str] = Field(default_factory=list)
    providerErrors: List[str] = Field(default_factory=list)


class AiScanProvenance(BaseModel):
    """Provider/prompt identity of one bounded AI scan request. Never a key."""
    provider: str = "none"
    model: str = ""
    endpoint: str = ""
    promptVersion: str = ""
    validatorVersion: str = ""
    selectionVersion: str = ""
    configVersion: str = ""
    requestId: Optional[str] = None
    latencyMs: float = 0.0
    usage: Dict[str, Optional[int]] = Field(default_factory=dict)
    finishReason: Optional[str] = None
    responseSha256: Optional[str] = None
    fallbackReason: Optional[str] = None


class AiScanResult(BaseModel):
    """Separate result of the AI_SCAN block; never changes the verdict."""
    status: str = "degraded"  # succeeded | partial | degraded | rejected
    observations: List[AiObservation] = Field(default_factory=list)
    suppressedDuplicates: int = 0
    validationErrors: List[str] = Field(default_factory=list)
    coverage: AiScanCoverage = Field(default_factory=AiScanCoverage)
    provenance: Optional[AiScanProvenance] = None
    isHypothesis: bool = True
    countsAsFindings: bool = False


class SecurityPassport(BaseModel):
    files_scanned: int = 0
    files_skipped: int = 0
    lines_of_code: int = 0
    rules_executed: List[str] = Field(default_factory=list)
    parser_errors: int = 0
    unresolved_dependencies: int = 0
    confirmed_findings: int = 0
    potential_findings: int = 0
    inconclusive_findings: int = 0
    verdict: SecurityVerdict = SecurityVerdict.PENDING
    # Canonical coverage completeness: 1.0 only when complete + expected scopes verified.
    # 0.0 with completeness_known=false means "not established", not "zero scanned".
    completeness_score: float = 0.0
    completeness_known: bool = False
    coverage_status: str = "unknown"  # complete | partial | unavailable | unknown
    coverage_reasons: List[str] = Field(default_factory=list)
    skipped_scopes: List[str] = Field(default_factory=list)
    # Legacy parser/AST ratio; kept for JSON consumers, not the overall verdict.
    parser_completeness_score: float = 1.0
    coverage_warnings: List[str] = Field(default_factory=list)


class DeliveryResult(BaseModel):
    channel: str = "webhook"  # "webhook" | "telegram" | "smtp"
    status: str = "succeeded"  # "succeeded" | "failed"
    target: str = ""
    status_code: Optional[int] = None
    message: str = ""
    timestamp: str = ""


# Workflow graph definitions
class PortDefinition(BaseModel):
    id: str
    name: str
    type: PortType


class NodeDefinition(BaseModel):
    kind: NodeKind
    title: str
    category: str
    description: str
    inputs: List[PortDefinition]
    outputs: List[PortDefinition]
    default_config: Dict[str, Any] = Field(default_factory=dict)


class WorkflowNode(BaseModel):
    id: str
    kind: NodeKind
    version: int = 1
    title: Optional[str] = None
    config: Dict[str, Any] = Field(default_factory=dict)
    position: Dict[str, float] = Field(default_factory=lambda: {"x": 100.0, "y": 100.0})

    @model_validator(mode="before")
    @classmethod
    def _reject_reserved_secret_envelopes(cls, value: Any) -> Any:
        """Секрет с префиксом enc:v1: из пользовательского ввода → 422."""
        reject_reserved_envelope(value)
        return value


class WorkflowEdge(BaseModel):
    id: str
    source: str
    sourcePort: str
    target: str
    targetPort: str


class WorkflowDraft(BaseModel):
    schemaVersion: int = 1
    name: str = "Security Workflow"
    description: str = ""
    nodes: List[WorkflowNode] = Field(default_factory=list)
    edges: List[WorkflowEdge] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _reject_reserved_secret_envelopes(cls, value: Any) -> Any:
        """Зарезервированный формат enc:v1:* не проходит create/update как 422."""
        reject_reserved_envelope(value)
        return value


class WorkflowValidationIssue(BaseModel):
    level: str = "error"  # "error" | "warning"
    nodeId: Optional[str] = None
    edgeId: Optional[str] = None
    portId: Optional[str] = None
    message: str


class WorkflowValidationResult(BaseModel):
    valid: bool
    issues: List[WorkflowValidationIssue] = Field(default_factory=list)


class ArtifactManifest(BaseModel):
    id: str
    filename: str
    hash_sha256: str
    size_bytes: int
    uncompressed_size_bytes: int
    file_count: int
    kotlin_file_count: int
    gradle_file_count: int
    created_at: str


class RunEvent(BaseModel):
    seq: int
    runId: str
    timestamp: str
    type: str  # "node_status" | "finding_summary" | "coverage" | "run_status" | "delivery_status" | "edge_data" | "resync_required"
    nodeId: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class NodeRunRecord(BaseModel):
    id: str
    run_id: str
    node_id: str
    kind: NodeKind
    status: NodeRunStatus
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_ms: float = 0.0
    error_message: Optional[str] = None
    input_data_masked: Dict[str, Any] = Field(default_factory=dict)
    output_data_masked: Dict[str, Any] = Field(default_factory=dict)


class RunSummary(BaseModel):
    id: str
    workflow_id: str
    revision_id: str
    artifact_id: Optional[str] = None
    trigger_type: str = "manual"  # "manual" | "schedule"
    execution_status: ExecutionStatus
    security_verdict: SecurityVerdict
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    total_findings: int = 0
    critical_findings: int = 0
    high_findings: int = 0
    last_event_seq: int = 0


class ScheduleRecord(BaseModel):
    id: str
    workflow_id: str
    artifact_id: str
    enabled: bool = True
    cron_or_interval: str = "*/30 * * * *"  # or "interval:60"
    timezone: str = "UTC"
    last_run_at: Optional[str] = None
    next_fire_at: Optional[str] = None
    created_at: str
