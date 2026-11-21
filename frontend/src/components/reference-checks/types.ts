/** Minimal published `AnalysisBundle` 0.1 shape used by the comparison. */
export interface AnalysisBundle {
  schemaVersion: '0.1';
  identity: Record<string, string>;
  signature: Record<string, string | null>;
  scanResults: Array<{
    category: 'SECRET' | 'SCA' | 'SAST';
    status: string;
    producerVersion: string;
    findings: Array<Record<string, unknown>>;
    coverage: { status: string; scopes: Array<Record<string, unknown>> };
  }>;
  [key: string]: unknown;
}

/** A pinned reference suite manifest (`reference-suite/v1`). */
export interface ReferenceSuiteManifest {
  schemaVersion: 'reference-suite/v1';
  suiteId: string;
  suiteVersion: string;
  project: { id: string; repository?: string | null; fixturePath?: string };
  reference: {
    branch: string;
    sha: string;
    snapshotManifestHash?: string | null;
    artifactHash?: string | null;
    bundleSha256?: string | null;
    runnerVersion: string;
    signature: Record<string, string | null>;
    expectedFindings: Array<Record<string, unknown>>;
    expectedControls: Array<Record<string, unknown>>;
  };
  compatibility: { bundleSchemaVersion: string };
}

/** One artifact/ref the user can select as the comparison target. */
export interface PinnedTarget {
  id: string;
  label: string;
  projectId: string;
  sha: string;
  branch?: string;
  /** Published immutable bundle; omit when `loadBundle` is provided. */
  bundle?: AnalysisBundle;
  loadBundle?: () => Promise<AnalysisBundle>;
}

export interface PinnedReference {
  id: string;
  label: string;
  projectId: string;
  branch: string;
  sha: string;
  manifest: ReferenceSuiteManifest;
}

export type ReferenceVerdict = 'pass' | 'fail' | 'inconclusive';

export interface ReferenceReportRow {
  id: string;
  category: string;
  ruleId: string;
  path: string;
  status: 'matched' | 'missing' | 'inconclusive' | 'triggered';
  reason: string;
  expectedFingerprint?: string | null;
  actualFingerprint?: string | null;
  actualFindings?: number | null;
}

export interface ReferenceUnexpectedFinding {
  category: string;
  ruleId: string | null;
  path: string | null;
  fingerprint: string | null;
  status: string | null;
}

export interface ReferenceComparisonReport {
  schemaVersion: 'reference-report/v1';
  comparatorVersion: string;
  suiteId: string;
  suiteVersion: string;
  projectId: string;
  status: ReferenceVerdict;
  reference: {
    branch: string;
    sha: string;
    runnerVersion: string;
  };
  target: {
    branch: string | null;
    sha: string;
    projectId: string;
    runId: string;
    snapshotId: string;
    artifactHash: string;
    snapshotManifestHash: string;
    runnerVersions: string[];
  };
  compatibility: {
    compatible: boolean;
    bundleSchemaVersion: string;
    reasons: string[];
  };
  findings: ReferenceReportRow[];
  controls: ReferenceReportRow[];
  unexpectedFindings: ReferenceUnexpectedFinding[];
  summary: {
    expectedFindings: number;
    matchedFindings: number;
    missingFindings: number;
    inconclusiveFindings: number;
    unexpectedFindings: number;
    expectedControls: number;
    matchedControls: number;
    triggeredControls: number;
    inconclusiveControls: number;
  };
}