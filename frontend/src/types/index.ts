export type PortType =
  | 'trigger'
  | 'artifact'
  | 'scan_result'
  | 'findings_bundle'
  | 'policy_result'
  | 'explain_result'
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

export interface SessionState {
  authenticated: boolean;
  authRequired: boolean;
  workspaceId: string;
  expiresInSec: number;
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
