import { NodeRunRecord } from './types';

export interface CanonicalCoverage {
  status: string;
  skippedScopes: string[];
  expectedScopesVerified: boolean;
}

export const getCanonicalCoverage = (nodeRuns: NodeRunRecord[]): CanonicalCoverage | null => {
  const policy = nodeRuns.find((node) => node.kind === 'policy_gate');
  const raw = policy?.output_data_masked?.policy_result?.details?.canonicalCoverage;
  if (!raw) return null;
  return {
    status: String(raw.coverageStatus || 'unknown'),
    skippedScopes: Array.isArray(raw.skippedScopes) ? raw.skippedScopes.map(String) : [],
    expectedScopesVerified: Boolean(raw.expectedScopesVerified),
  };
};

export const coverageLabel = (coverage: CanonicalCoverage | null): string => {
  if (!coverage) return 'Неизвестно';
  if (coverage.status === 'full') return 'Полное';
  if (coverage.status === 'partial') return 'Неполное';
  if (coverage.status === 'unknown') return 'Неизвестно';
  return coverage.status;
};