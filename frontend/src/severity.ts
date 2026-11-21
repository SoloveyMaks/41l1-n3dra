import type { Finding, Severity } from './types';

export const SEVERITY_ORDER = ['critical', 'high', 'medium', 'low', 'info'] as const;
export type SeverityBucket = Severity | 'unknown';
export type FindingSortMode = 'severity' | 'file';

const SEVERITY_LABELS: Record<SeverityBucket, string> = {
  critical: 'Критично', high: 'Высокий', medium: 'Средний', low: 'Низкий', info: 'Инфо', unknown: 'Неизвестно',
};

export function severityBucket(value: unknown): SeverityBucket {
  return SEVERITY_ORDER.includes(value as Severity) ? value as Severity : 'unknown';
}

export function severityRank(value: unknown): number {
  const index = SEVERITY_ORDER.indexOf(value as Severity);
  return index === -1 ? SEVERITY_ORDER.length : index;
}

export function severityLabel(value: unknown): string {
  const bucket = severityBucket(value);
  return bucket === 'unknown' && typeof value === 'string' && value.trim()
    ? `Неизвестно (${value})`
    : SEVERITY_LABELS[bucket];
}

const compareText = (left: string, right: string): number => left < right ? -1 : left > right ? 1 : 0;

export function compareFindings(left: Finding, right: Finding, mode: FindingSortMode = 'severity'): number {
  if (mode === 'severity') {
    const severityDiff = severityRank(left.severity) - severityRank(right.severity);
    if (severityDiff !== 0) return severityDiff;
  }
  const pathDiff = compareText(left.primaryLocation.path, right.primaryLocation.path);
  if (pathDiff !== 0) return pathDiff;
  if (mode === 'file') {
    const lineDiff = left.primaryLocation.startLine - right.primaryLocation.startLine;
    if (lineDiff !== 0) return lineDiff;
    const columnDiff = left.primaryLocation.startColumn - right.primaryLocation.startColumn;
    if (columnDiff !== 0) return columnDiff;
  }
  const ruleDiff = compareText(left.ruleId, right.ruleId);
  if (ruleDiff !== 0) return ruleDiff;
  return compareText(left.id, right.id);
}

export function countSeverities(findings: readonly Finding[]): Record<SeverityBucket, number> {
  const counts: Record<SeverityBucket, number> = { critical: 0, high: 0, medium: 0, low: 0, info: 0, unknown: 0 };
  for (const finding of findings) counts[severityBucket(finding.severity)] += 1;
  return counts;
}
