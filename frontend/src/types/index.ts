export type PortType =
  | 'trigger'
  | 'artifact'
  | 'scan_result'
  | 'findings_bundle'
  | 'policy_result'
  | 'explain_result'
  | 'ai_scan_result'
  | 'report'
  | 'condition_branch'
  | 'delivery_result';

export type NodeKind =
  | 'manual_trigger'
  | 'schedule_trigger'
  | 'project_input'
  | 'secret_scan'
  | 'sca_scan'
  | 'kotlin_sast'
  | 'merge_findings'
  | 'branch_merge'
  | 'condition'
  | 'policy_gate'
  | 'ai_explain'
  | 'ai_scan'
  | 'build_report'
  | 'notify';

export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info';
export type SecurityVerdict = 'pending' | 'pass' | 'fail' | 'inconclusive';
export type ExecutionStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'interrupted' | 'cancelled';
export type NodeRunStatus = 'pending' | 'running' | 'retrying' | 'succeeded' | 'failed' | 'skipped' | 'cancelled';

export interface Location {
  path: string;
  startLine: number;
  endLine: number;
  startColumn: number;
  endColumn: number;
  byteRange?: [number, number];
}

export interface TraceStep {
  step: number;
  description: string;
  location?: Location;
}

export interface Finding {
  id: string;
  fingerprint: string;
  analyzer: 'secret' | 'sca' | 'sast' | string;
  ruleId: string;
  ruleVersion: string;
  severity: Severity;
  severityBasis: string;
  confidence: 'confirmed' | 'potential' | 'inconclusive';
  confidenceBasis: string;
  status: 'confirmed' | 'potential' | 'inconclusive';
  primaryLocation: Location;
  relatedLocations: Location[];
  trace: TraceStep[];
  redactedEvidence: string;
  advisoryIds: string[];
  coverageWarnings: string[];
  ai_enrichment?: {
    explanation: string;
    remediation: string;
    suggestedFixedVersion?: string;
  };
}

export interface ResponsibilityRole {
  role: string;
  basis: string;
  confidence: 'high' | 'medium' | 'low' | string;
}

export interface ResponsibilityCodeowners {
  status: 'available' | 'absent' | 'unavailable' | string;
  file?: string | null;
  matchedPattern?: string | null;
  line?: number | null;
  owners: string[];
  basis: string;
  limitations: string[];
}

export interface ResponsibilityPerson {
  displayName?: string | null;
  login?: string | null;
}

export interface ResponsibilityCommit {
  sha: string;
  author?: ResponsibilityPerson | null;
  committer?: ResponsibilityPerson | null;
  date?: string | null;
  url?: string | null;
  message?: string | null;
}

export interface ResponsibilityReview {
  reviewer: ResponsibilityPerson;
  state: string;
  opinionState?: string;
  submittedAt?: string | null;
  decisionAt?: string | null;
  appliesToMerge: boolean;
  limitation?: string | null;
}

export interface ResponsibilityPullRequest {
  number: number;
  title: string;
  url?: string | null;
  author?: ResponsibilityPerson | null;
  mergedBy?: ResponsibilityPerson | null;
  mergedAt?: string | null;
  headSha?: string | null;
  reviews: ResponsibilityReview[];
  reviewsStatus: string;
  reviewsReason: string;
  reviewsTruncated: boolean;
  reviewsIncomplete?: boolean;
  limitations: string[];
}

export interface ResponsibilityLineBlame {
  status: 'unsupported' | 'available' | string;
  reason: string;
}

export interface ResponsibilityHistory {
  status: 'available' | 'unavailable' | 'rate_limited' | 'failed' | 'unsupported' | string;
  provider?: string | null;
  reason: string;
  evidenceKind: string;
  notBlame: boolean;
  commit?: ResponsibilityCommit | null;
  associationCommitSha?: string | null;
  frozenCommitSha?: string | null;
  pullRequests: ResponsibilityPullRequest[];
  pullRequestsStatus: string;
  pullRequestsReason: string;
  pullRequestsTruncated: boolean;
  lineBlame: ResponsibilityLineBlame;
  limitations: string[];
}

export interface ResponsibilityData {
  scope: {
    runId: string;
    artifactId?: string | null;
    snapshotId?: string | null;
    commitSha?: string | null;
    path: string;
  };
  findingId: string;
  analyzer: string;
  ruleId: string;
  suggestedRole: ResponsibilityRole;
  codeowners: ResponsibilityCodeowners;
  history: ResponsibilityHistory;
  limitations: string[];
}

export type AiScanStatus = 'succeeded' | 'partial' | 'degraded' | 'rejected';

export interface AiObservationGrounding {
  fileRef: string;
  sourceHash: string;
  quoteSha256: string;
  startByte: number;
  endByte: number;
  modelStartLine: number;
  modelEndLine: number;
}

/** Гипотеза модели. Никогда не попадает в подтверждённые findings или вердикт. */
export interface AiObservation {
  id: string;
  origin: 'ai_hypothesis';
  ruleClass: string;
  severity: Severity;
  confidence: 'potential';
  confidenceBasis: string;
  status: 'potential';
  primaryLocation: Location;
  redactedEvidence: string;
  rationale: string;
  remediation: string;
  grounding: AiObservationGrounding;
}

export interface AiScanCoverageIssue {
  path: string;
  code: string;
  detail?: string;
}

export interface AiScanCoverage {
  status: 'complete' | 'partial' | 'degraded' | 'rejected' | string;
  filesTotal: number;
  filesSelected: number;
  filesSent: number;
  bytesSelected: number;
  bytesSent: number;
  maxFiles: number;
  maxBytes: number;
  maxObservations: number;
  truncatedFiles: string[];
  omittedCount: number;
  omitted: AiScanCoverageIssue[];
  failedCount: number;
  failed: AiScanCoverageIssue[];
  limitsReached: string[];
  providerErrors: string[];
}

export interface AiScanProvenance {
  provider: string;
  model: string;
  endpoint: string;
  promptVersion: string;
  validatorVersion: string;
  selectionVersion: string;
  configVersion: string;
  requestId?: string | null;
  latencyMs: number;
  usage: Record<string, number | null>;
  finishReason?: string | null;
  responseSha256?: string | null;
  fallbackReason?: string | null;
}

export interface AiScanResult {
  status: AiScanStatus;
  observations: AiObservation[];
  suppressedDuplicates: number;
  validationErrors: string[];
  coverage: AiScanCoverage;
  provenance?: AiScanProvenance | null;
  isHypothesis: boolean;
  countsAsFindings: false;
}

export interface SecurityPassport {
  files_scanned: number;
  files_skipped: number;
  lines_of_code: number;
  rules_executed: string[];
  parser_errors: number;
  unresolved_dependencies: number;
  confirmed_findings: number;
  potential_findings: number;
  inconclusive_findings: number;
  verdict: SecurityVerdict;
  completeness_score: number;
  coverage_warnings: string[];
}

export interface WorkflowNode {
  id: string;
  kind: NodeKind;
  version?: number;
  title?: string;
  config: Record<string, any>;
  position: { x: number; y: number };
}

export interface WorkflowEdge {
  id: string;
  source: string;
  sourcePort: string;
  target: string;
  targetPort: string;
}

export interface WorkflowDraft {
  schemaVersion: number;
  name: string;
  description: string;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
}

export interface PortDefinition {
  id: string;
  name: string;
  type: PortType;
}

export interface NodeDefinition {
  kind: NodeKind;
  title: string;
  category: string;
  description: string;
  inputs: PortDefinition[];
  outputs: PortDefinition[];
  default_config: Record<string, any>;
}

export type ComparisonStatus =
  | 'persistent' | 'introduced' | 'changed' | 'resolved'
  | 'inconclusive' | 'file_deleted';

export interface ComparisonItem {
  id: string;
  baselineFindingId: string | null;
  currentFindingId: string | null;
  status: ComparisonStatus;
  matchingBasis: 'unique_anchor' | 'ambiguous' | 'none';
  reason: string;
}

export interface DeletedFile {
  path: string;
  baselineSourceHash: string;
  reason: string;
}

export type SqlPatternStatus =
  | 'persistent' | 'changed' | 'pattern_disappeared'
  | 'inconclusive' | 'file_deleted';

export interface SqlPatternResult {
  scope?: string;
  status?: SqlPatternStatus;
  reason?: string;
  ruleId?: string;
  path?: string;
  baselineRunId?: string;
  currentRunId?: string;
  [key: string]: any;
}

export interface ComparisonDto {
  workspaceId: string;
  id: string;
  baselineRunId: string;
  baselineSnapshotId: string;
  currentRunId: string;
  currentSnapshotId: string;
  comparable: boolean;
  reasons: string[];
  items: ComparisonItem[];
  deletedFiles: DeletedFile[];
}

export type SessionRole = 'user' | 'admin';
export type SessionMode = 'account' | 'demo';

export interface SessionUser {
  userId: string;
  email: string;
  displayName: string;
  role: SessionRole;
  workspaceId: string;
  createdAt?: string | null;
}

export interface SessionState {
  authenticated: boolean;
  authRequired: boolean;
  workspaceId: string;
  expiresInSec: number;
  role?: SessionRole;
  adminAvailable?: boolean;
  mode?: SessionMode | null;
  user?: SessionUser | null;
  sessionId?: string | null;
  registrationInviteRequired?: boolean;
}

export interface RunHistoryItem {
  id: string;
  workflow_id: string;
  revision_id: string;
  artifact_id?: string | null;
  trigger_type: string;
  execution_status: ExecutionStatus;
  security_verdict: SecurityVerdict;
  total_findings: number;
  critical_count: number;
  high_count: number;
  medium_count: number;
  low_count: number;
  info_count: number;
  started_at?: string | null;
  finished_at?: string | null;
  created_at: string;
  workflow_name?: string | null;
  project_name?: string | null;
}

export interface ProfileHistoryResponse {
  mode: SessionMode;
  userId?: string | null;
  sharedDemoWorkspace: boolean;
  items: RunHistoryItem[];
  total: number;
  limit: number;
  offset: number;
  projects: { id: string | null; name: string | null }[];
  workflows: { id: string; name: string | null }[];
  statuses: string[];
}

export interface HistoryResponse {
  mode: SessionMode;
  userId?: string | null;
  workspaceId: string;
  sharedDemoWorkspace: boolean;
  limit: number;
  items: RunHistoryItem[];
}

export interface ProfileSummary {
  runs: number;
  lastRunAt?: string | null;
  byStatus: Record<string, number>;
  byVerdict: Record<string, number>;
  totalFindings: number;
}

export interface ProfileData {
  mode: 'account';
  user: SessionUser;
  session: {
    sessionId?: string | null;
    issuedAt?: number | null;
    expiresAt?: number | null;
  };
  history: ProfileSummary;
}

export interface AccountCredentials {
  email: string;
  password: string;
  displayName?: string;
  registrationCode?: string;
}

export type TelegramDeliveryMode = 'alerts' | 'jsonc' | 'both';

export interface TelegramSettings {
  deliveryMode: TelegramDeliveryMode;
  scenario: string | null;
}

export interface TelegramMe {
  bound: boolean;
  chatId?: string | number;
  telegramUsername?: string | null;
  createdAt?: string | null;
  settings?: TelegramSettings | null;
  botUsername?: string | null;
  externalStatus?: 'ready' | 'blocked' | string | null;
  proxyConfigured?: boolean;
}

export interface TelegramBinding {
  code: string;
  expiresAt?: string | null;
  expiresInSec: number;
  deepLink?: string | null;
  instructions: string;
}

export interface TelegramScenario {
  id: string;
  name: string;
}

export interface AdminOverview {
  counts?: Record<string, number>;
  db?: {
    healthy?: boolean;
    latencyMs?: number | null;
    migrations?: { applied?: number; latestAppliedAt?: string | null } | null;
    error?: string;
  };
  ai?: {
    provider?: string;
    model?: string;
    configured?: boolean;
  };
  vector?: {
    enabled?: boolean;
    available?: boolean;
    installedVersion?: string | null;
    status?: string;
    activeEmbeddingSpecs?: number | null;
    embeddingModel?: string | null;
    dimension?: number | null;
    metric?: string | null;
  };
  auth?: {
    publicDemo?: boolean;
    adminAvailable?: boolean;
    adminAccessRequiresSeparateCode?: boolean;
  };
}

export type RepositoryProvider = 'github' | 'gitlab';

export interface RepositorySource {
  url: string;
  provider: RepositoryProvider;
  branch: string;
  commit_sha: string;
}

export interface ArtifactManifest {
  id: string;
  filename: string;
  hash_sha256: string;
  size_bytes: number;
  uncompressed_size_bytes: number;
  file_count: number;
  kotlin_file_count: number;
  gradle_file_count: number;
  created_at: string;
  source_ref?: string;
  repository?: RepositorySource | null;
}

export type RepositoryTriggerMode = 'poll' | 'webhook';

export interface RepositoryWatch {
  id: string;
  workflow_id: string;
  url: string;
  provider: RepositoryProvider;
  branch: string;
  /** Пустая строка для webhook: запуск по событию не использует расписание. */
  cron_or_interval: string;
  trigger_mode?: RepositoryTriggerMode;
  /** Секрет выпущен на сервере. Это не подтверждение, что хук уже стоит в репозитории. */
  webhook_ready?: boolean;
  enabled?: boolean | number | string;
  next_check_at?: string | null;
  last_checked_at?: string | null;
  last_seen_commit?: string | null;
  last_run_id?: string | null;
  last_error?: string | null;
  last_delivery_at?: string | null;
  last_delivery_status?: string | null;
  last_delivery_error?: string | null;
  created_at?: string | null;
}

/** Ответ setup: секрет приходит один раз и только по явному действию администратора. */
export interface RepositoryWebhookSetup {
  endpointPath: string;
  secret: string;
  provider: RepositoryProvider;
  event: 'push';
}

export interface RepositoryCheckResult {
  status: 'unchanged' | 'started' | 'busy' | 'error';
  runId?: string;
  commitSha?: string;
  detail?: string;
}

export interface RunSummary {
  id: string;
  workflow_id: string;
  revision_id: string;
  artifact_id?: string;
  trigger_type: string;
  execution_status: ExecutionStatus;
  security_verdict: SecurityVerdict;
  created_at: string;
  started_at?: string;
  finished_at?: string;
  total_findings: number;
  critical_count: number;
  high_count: number;
  medium_count: number;
  low_count: number;
  info_count: number;
  last_event_seq: number;
}

export interface NodeRunRecord {
  id: string;
  run_id: string;
  node_id: string;
  kind: NodeKind;
  status: NodeRunStatus;
  started_at?: string;
  finished_at?: string;
  duration_ms: number;
  error_message?: string;
  input_data_masked: Record<string, any>;
  output_data_masked: Record<string, any>;
}

export interface ReportData {
  run_id: string;
  verdict: SecurityVerdict;
  passport: SecurityPassport;
  ai_summary: string;
  ai_items: Array<{
    findingId: string;
    explanation: string;
    remediation: string;
    suggestedFixedVersion?: string;
  }>;
  ai_status: string;
  readable_markdown: string;
  official_json: string;
  created_at: string;
}

export type AppTab = 'editor' | 'live' | 'findings' | 'map' | 'report' | 'comparison' | 'schedule' | 'settings';
