// Generated from contracts.schema.json by node generate-types.mjs.
// Numeric bounds, formats and cross-reference invariants require validation.

export type Uuid = string;

export type Sha256 = string;

export type SourcePath = string;

export type Category = "SECRET" | "SCA" | "SAST";

export type RunScope = {
  workspaceId: Uuid;
  runId: Uuid;
  snapshotId: Uuid;
};

export type RunIdentity = {
  workspaceId: Uuid;
  runId: Uuid;
  snapshotId: Uuid;
  artifactHash: Sha256;
  snapshotManifestHash: Sha256;
  workflowRevisionId: Uuid;
};

export type AnalysisSignature = {
  parserVersion: string;
  rulesVersion: string;
  advisoryVersion: string;
  configVersion: string;
  hmacKeyVersion: string;
  parserHash: Sha256;
  rulesHash: Sha256;
  advisoryHash: Sha256;
  scanConfigHash: Sha256;
  domainPolicyRevisionId: Uuid | null;
  domainPolicyVersion: string | null;
  domainPolicyHash: Sha256 | null;
};

export type Location = {
  path: SourcePath;
  line: number;
  column: number;
  endLine: number;
  endColumn: number;
  byteStart: number;
  byteEnd: number;
};

export type SemanticAnchor = {
  normalizedPath: SourcePath;
  symbolKey: string;
  normalizedContextHash: Sha256;
  role: string;
  secretHmac: Sha256 | null;
};

export type Finding = {
  workspaceId: Uuid;
  runId: Uuid;
  snapshotId: Uuid;
  id: Uuid;
  fingerprint: Sha256;
  fingerprintVersion: "fp/v1";
  category: Category;
  ruleId: string;
  ruleVersion: string;
  ruleFamily: string;
  severity: "info" | "low" | "medium" | "high" | "critical";
  severityBasis: string;
  confidence: "high" | "medium" | "low";
  confidenceBasis: string;
  status: "confirmed" | "potential" | "inconclusive";
  primaryLocation: Location;
  primaryEvidenceId: Uuid;
  relatedEvidenceIds: Array<Uuid>;
  traceEvidenceIds: Array<Uuid>;
  graphNodeIds: Array<Uuid>;
  anchor: SemanticAnchor;
  advisoryIds: Array<string>;
  coverageWarnings: Array<string>;
};

export type EvidenceRef = {
  workspaceId: Uuid;
  runId: Uuid;
  snapshotId: Uuid;
  id: Uuid;
  location: Location;
  kind: "source" | "ast" | "dsl" | "sql" | "manifest";
  redactedSnippet: string;
  sourceHash: Sha256;
  producerVersion: string;
};

export type CodeNode = {
  workspaceId: Uuid;
  runId: Uuid;
  snapshotId: Uuid;
  id: Uuid;
  kind: "actor" | "route" | "security_chain" | "matcher" | "guard" | "function" | "source" | "sink" | "data" | "config";
  symbolKey: string;
  label: string;
  evidenceRefs: Array<Uuid>;
  producerVersion: string;
};

export type CodeEdge = {
  workspaceId: Uuid;
  runId: Uuid;
  snapshotId: Uuid;
  id: Uuid;
  fromId: Uuid;
  toId: Uuid;
  kind: "routes_to" | "matches" | "permits" | "requires" | "calls" | "flows_to" | "guards" | "reads" | "writes";
  evidenceRefs: Array<Uuid>;
  resolution: "resolved" | "partial" | "unknown";
  resolutionReason: string;
  producerVersion: string;
  basis: "source" | "policy_assumption";
  policyRef: Uuid | null;
};

export type CoverageScope = {
  path: SourcePath;
  ruleId: string;
  category: Category;
  status: "checked" | "partial" | "skipped" | "error" | "unsupported";
  reason: string;
};

export type Coverage = {
  status: "complete" | "partial" | "unavailable";
  filesTotal: number;
  filesVisited: number;
  rulesApplied: Array<string>;
  scopes: Array<CoverageScope>;
  skippedScopes: Array<string>;
  parserErrors: Array<Uuid>;
  unresolvedSymbols: Array<string>;
  limitsReached: Array<string>;
};

export type Diagnostic = {
  workspaceId: Uuid;
  runId: Uuid;
  snapshotId: Uuid;
  id: Uuid;
  category: Category;
  code: string;
  message: string;
  evidenceId: Uuid | null;
};

export type DomainPolicyInput = {
  workspaceId: Uuid;
  id: Uuid;
  version: string;
  hash: Sha256;
  sourceRef: string;
  approvalStatus: "assumption" | "approved";
  approvedBy: string | null;
  approvedAt: string | null;
  resources: Array<DomainPolicyResource>;
};

export type ScanResult = {
  schemaVersion: "0.1";
  identity: RunIdentity;
  signature: AnalysisSignature;
  category: Category;
  status: "complete" | "partial" | "failed" | "timeout" | "unsupported";
  producerVersion: string;
  durationMs: number;
  findings: Array<Finding>;
  evidence: Array<EvidenceRef>;
  diagnostics: Array<Diagnostic>;
  coverage: Coverage;
};

export type CodeFactsBundle = {
  schemaVersion: "0.1";
  identity: RunIdentity;
  signature: AnalysisSignature;
  nodes: Array<CodeNode>;
  edges: Array<CodeEdge>;
  evidence: Array<EvidenceRef>;
  coverage: Coverage;
  policyInputs: Array<DomainPolicyInput>;
  diagnostics: Array<Diagnostic>;
};

export type FindingNodeLink = {
  findingId: Uuid;
  nodeId: Uuid;
};

export type SecurityMapDto = {
  schemaVersion: "0.1";
  identity: RunIdentity;
  signature: AnalysisSignature;
  nodes: Array<CodeNode>;
  edges: Array<CodeEdge>;
  evidence: Array<EvidenceRef>;
  coverage: Coverage;
  policyInputs: Array<DomainPolicyInput>;
  diagnostics: Array<Diagnostic>;
  bundleHash: Sha256;
  findingRefs: Array<Uuid>;
  findingNodeLinks: Array<FindingNodeLink>;
};

export type AnalysisBundle = {
  schemaVersion: "0.1";
  identity: RunIdentity;
  signature: AnalysisSignature;
  scanResults: Array<ScanResult>;
  codeFacts: CodeFactsBundle;
};

export type ComparisonItem = {
  id: Uuid;
  baselineFindingId: Uuid | null;
  currentFindingId: Uuid | null;
  status: "persistent" | "introduced" | "changed" | "resolved" | "inconclusive" | "file_deleted";
  matchingBasis: "unique_anchor" | "ambiguous" | "none";
  reason: string;
};

export type DeletedFile = {
  path: SourcePath;
  baselineSourceHash: Sha256;
  reason: string;
};

export type ComparisonDto = {
  workspaceId: Uuid;
  id: Uuid;
  baselineRunId: Uuid;
  baselineSnapshotId: Uuid;
  currentRunId: Uuid;
  currentSnapshotId: Uuid;
  comparable: boolean;
  reasons: Array<string>;
  items: Array<ComparisonItem>;
  deletedFiles: Array<DeletedFile>;
};

export type EmbeddingSpec = {
  workspaceId: Uuid;
  id: Uuid;
  status: "pending" | "active" | "retired";
  provider: string | null;
  model: string | null;
  modelVersion: string | null;
  dimension: number | null;
  metric: "cosine" | "l2" | "inner_product" | null;
  queryPreparationVersion: string | null;
  documentPreparationVersion: string | null;
  redactionVersion: string | null;
};

export type SimilarFix = {
  exampleId: Uuid;
  sourceRef: string;
  verificationBasis: string;
  limitations: Array<string>;
  embeddingSpec: EmbeddingSpec;
  distance: number;
  metric: "cosine" | "l2" | "inner_product";
  redactedFix: string;
};

export type SimilarFixesResult = {
  status: "available" | "unavailable";
  reason: string | null;
  items: Array<SimilarFix>;
};

export type AiExplanation = {
  workspaceId: Uuid;
  runId: Uuid;
  snapshotId: Uuid;
  id: Uuid;
  findingId: Uuid;
  provider: string;
  model: string;
  modelVersion: string;
  promptVersion: string;
  validatorVersion: string;
  validationStatus: "accepted" | "rejected" | "unavailable";
  redactedExplanation: string;
  limitations: Array<string>;
};

export type RoleAccessPolicyResource = {
  kind: "role_access";
  resource: string;
  allowedRoles: Array<string>;
};

export type OwnershipPolicyResource = {
  kind: "ownership";
  resource: string;
  ownerField: string;
  trustedIdentity: string;
  roleExceptions: Array<string>;
};

export type DomainPolicyResource = RoleAccessPolicyResource | OwnershipPolicyResource;
