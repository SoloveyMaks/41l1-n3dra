import { NodeRunRecord } from './types';

export interface CanonicalCoverage {
  status: string;
  skippedScopes: string[];
  expectedScopesVerified: boolean;
}

const SCOPE_LABEL: Record<string, string> = {
  'runtime-configuration': 'конфигурация времени выполнения',
  'runtime-guards': 'защита во время выполнения',
  'binaries-and-archives': 'бинарники и архивы',
  'dependency-resolution': 'разрешение зависимостей',
  'generated-code': 'сгенерированный код',
};

export const scopeLabel = (scope: string): string => SCOPE_LABEL[scope] || scope;

const parseVerified = (value: unknown): boolean =>
  value === true || value === 1 || value === '1' || value === 'true';

export const getCanonicalCoverage = (nodeRuns: NodeRunRecord[]): CanonicalCoverage | null => {
  const policy = nodeRuns.find((node) => node.kind === 'policy_gate');
  const raw = policy?.output_data_masked?.policy_result?.details?.canonicalCoverage;
  if (!raw) return null;
  return {
    status: String(raw.coverageStatus || 'unknown').toLowerCase(),
    skippedScopes: Array.isArray(raw.skippedScopes) ? raw.skippedScopes.map(String) : [],
    expectedScopesVerified: parseVerified(raw.expectedScopesVerified),
  };
};

export const coverageLabel = (coverage: CanonicalCoverage | null): string => {
  if (!coverage) return 'Неизвестно';
  if (coverage.status === 'full' || coverage.status === 'complete') {
    return coverage.expectedScopesVerified ? 'Полное' : 'Неподтверждено';
  }
  if (coverage.status === 'partial') return 'Неполное';
  if (coverage.status === 'unavailable' || coverage.status === 'unknown' || !coverage.status) return 'Неизвестно';
  return coverage.status;
};

export const coverageReason = (coverage: CanonicalCoverage | null): string => {
  if (!coverage) return 'Статус покрытия недоступен: backend не передал canonical coverage.';
  if ((coverage.status === 'full' || coverage.status === 'complete') && coverage.expectedScopesVerified) {
    return 'Все ожидаемые области проверены.';
  }
  if ((coverage.status === 'full' || coverage.status === 'complete') && !coverage.expectedScopesVerified) {
    return 'Полнота областей не подтверждена, даже если проверка завершилась.';
  }
  if (coverage.status === 'partial') {
    const skipped = coverage.skippedScopes.map(scopeLabel).join(', ');
    return skipped
      ? `Проверена только часть областей; не проверено: ${skipped}.`
      : 'Проверена только часть областей.';
  }
  return 'Статус покрытия неизвестен; считать проверку полной нельзя.';
};