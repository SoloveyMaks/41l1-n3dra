import type { Severity } from '../../types';

/** Один сохранённый masked-фрагмент, привязанный к файлу. */
export interface FileEvidenceFact {
  id: string;
  line: number;
  endLine: number;
}

export interface FileFindingFact {
  id: string;
  ruleId: string;
  severity: Severity;
  status: string;
  confidence: string;
  line: number;
}

/**
 * Файл, о котором есть факты в снимке анализа.
 * Это не «все файлы репозитория»: запись появляется только там, где снимок
 * оставил evidence, находку или покрытие.
 */
export interface RepositoryFileFact {
  path: string;
  bytes?: number | null;
  sourceHash?: string | null;
  evidence: FileEvidenceFact[];
  findings: FileFindingFact[];
  nodeCount: number;
  worstSeverity: Severity | null;
}

export type FileLinkKind =
  | 'imports'
  | 'calls'
  | 'flows_to'
  | 'reads'
  | 'writes'
  | 'routes_to'
  | 'matches'
  | 'permits'
  | 'requires'
  | 'guards'
  | string;

/**
 * Межфайловая связь. Появляется только из доказанного ребра фактов:
 * у ребра есть существующие evidenceRefs, basis=source, resolution != unknown,
 * и доказательства ребра покрывают оба файла именно этой пары.
 * Иерархия папок сюда не попадает.
 */
export interface RepositoryFileLink {
  id: string;
  fromPath: string;
  toPath: string;
  kind: FileLinkKind;
  label: string;
  resolution: 'resolved' | 'partial';
  evidenceCount: number;
}

export interface FileGraphCoverage {
  status: 'complete' | 'partial' | 'unavailable';
  filesTotal: number;
  filesVisited: number;
  skippedScopes: string[];
}

export interface RepositoryFileGraphData {
  files: RepositoryFileFact[];
  links: RepositoryFileLink[];
  coverage: FileGraphCoverage;
  /**
   * Рёбра, которые не стали связью файлов: нет существующих доказательств,
   * unknown, policy-предположение или доказательства не покрывают эту пару.
   */
  linksOmitted: number;
  /** Рёбра, у которых оба конца в одном файле. */
  linksInsideFiles: number;
}

export interface MaskedReadRequest {
  evidenceId: string;
  path: string;
  line: number;
  endLine: number;
}

export type MaskedReadResult =
  | {
      status: 'ready';
      text: string;
      redactionVersion?: string;
      remasked?: boolean;
    }
  | { status: 'unavailable'; reason: string };

export interface MaskedFragmentEntry {
  loading: boolean;
  result: MaskedReadResult | null;
}

/** Сколько сохранённых фрагментов раскрываем в панели файла. */
export const EVIDENCE_PREVIEW_LIMIT = 6;