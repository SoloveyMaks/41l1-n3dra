/** UI descriptors are labels/IDs only. The server resolves manifests and runs. */
export interface PinnedReference {
  id: string;
  label: string;
  projectId?: string;
  branch?: string;
  sha?: string;
}

/** One server-owned target run the user can select. */
export interface PinnedTarget {
  id: string;
  label: string;
  /** Server-owned run id; the router resolves the published envelope. */
  runId: string;
  /** Optional artifact id; the server requires it to belong to the run. */
  artifactId?: string;
  projectId?: string;
}

export interface ReferenceComparisonRequest {
  referenceManifestId: string;
  targetRunId: string;
  targetArtifactId?: string;
}

export type ReferenceVerdict = 'pass' | 'fail' | 'inconclusive';

export interface ReferenceReportRow {
  id: string;
  category: string;
  ruleId: string;
  path: string;
  status: 'matched' | 'missing' | 'mismatch' | 'inconclusive' | 'triggered';
  reason: string;
  expectedFingerprint?: string | null;
  actualFingerprint?: string | null;
  actualSeverity?: string | null;
  actualFindings?: number | null;
}

export interface ReferenceUnexpectedFinding {
  category: string;
  ruleId: string | null;
  path: string | null;
  fingerprint: string | null;
  status: string | null;
}

export interface ReferenceProvenanceSnapshot {
  source: string;
  workspaceId: string;
  runId: string | null;
  snapshotId: string | null;
  commitSha: string | null;
  branch: string | null;
  snapshotManifestHash: string | null;
  artifactHash: string | null;
  bundleContentSha256: string | null;
  sourceExportSha256: string | null;
  manifestHash: string | null;
}

export interface ReferenceProvenance {
  mode: 'server-canonical' | 'raw-fixture';
  attestation: 'canonical-published' | 'none';
  verified: boolean;
  reasons: string[];
  reference: ReferenceProvenanceSnapshot | null;
  target: ReferenceProvenanceSnapshot | null;
  checks: {
    reference: Record<string, unknown>;
    target: Record<string, unknown>;
  };
  untrustedClaims?: Record<string, unknown>;
}

export interface ReferenceComparisonReport {
  schemaVersion: 'reference-report/v2';
  comparatorVersion: string;
  suiteId: string;
  suiteVersion: string;
  projectId: string;
  status: ReferenceVerdict;
  reference: {
    branch: string;
    sha: string;
    snapshotManifestHash: string | null;
    artifactHash: string | null;
    bundleContentSha256: string | null;
    bundleSha256: string | null;
    runnerVersion: string;
    snapshotPresent: boolean;
  };
  target: {
    branch: string | null;
    sha: string | null;
    workspaceId: string | null;
    runId: string | null;
    snapshotId: string | null;
    artifactId: string | null;
    artifactHash: string | null;
    snapshotManifestHash: string | null;
    runnerVersions: string[];
    provenance: string;
  };
  compatibility: {
    compatible: boolean;
    bundleSchemaVersion: string;
    reasons: string[];
    coverageReasons: string[];
    coverageWarnings: string[];
  };
  provenance: ReferenceProvenance;
  findings: ReferenceReportRow[];
  controls: ReferenceReportRow[];
  unexpectedFindings: ReferenceUnexpectedFinding[];
  summary: {
    expectedFindings: number;
    matchedFindings: number;
    missingFindings: number;
    mismatchedFindings: number;
    inconclusiveFindings: number;
    unexpectedFindings: number;
    expectedControls: number;
    matchedControls: number;
    triggeredControls: number;
    inconclusiveControls: number;
  };
  untrusted?: {
    bundleIdentity: Record<string, string>;
    note: string;
  };
}
