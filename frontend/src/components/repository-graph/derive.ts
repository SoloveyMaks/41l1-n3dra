import type {
  Finding as ContractFinding,
  SecurityMapDto,
} from '../../../../docs/data-contracts/contracts.ts';
import type { Severity } from '../../types';
import type {
  FileFindingFact,
  FileLinkKind,
  RepositoryFileFact,
  RepositoryFileGraphData,
  RepositoryFileLink,
} from './types';

const SEVERITY_RANK: Record<Severity, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
  info: 4,
};

const EDGE_LABEL: Record<string, string> = {
  imports: 'Импортирует',
  calls: 'Вызывает',
  flows_to: 'Передаёт данные',
  reads: 'Читает',
  writes: 'Изменяет',
  routes_to: 'Маршрутизирует',
  matches: 'Совпадает с правилом',
  permits: 'Разрешает',
  requires: 'Требует',
  guards: 'Проверяет',
};

export const fileLinkLabel = (kind: FileLinkKind): string =>
  EDGE_LABEL[kind] ?? String(kind).replace(/_/g, ' ');

const worst = (left: Severity | null, right: Severity): Severity => {
  if (!left) return right;
  return SEVERITY_RANK[right] < SEVERITY_RANK[left] ? right : left;
};

type MutableFile = RepositoryFileFact & { evidenceIds: Set<string> };

type MutableLink = RepositoryFileLink & { evidenceIds: Set<string> };

/**
 * Строит файловый граф только из опубликованных фактов карты.
 *
 * Файлы появляются из evidence, coverage scopes и находок. Связи файлов —
 * только из рёбер, у которых есть evidenceRefs, basis=source и известная
 * resolution. Папки здесь не создают рёбер и не изображают вызовы.
 */
export function deriveFileGraph(
  map: SecurityMapDto,
  findings: readonly ContractFinding[],
): RepositoryFileGraphData {
  const files = new Map<string, MutableFile>();
  const evidenceById = new Map(map.evidence.map((item) => [item.id, item]));

  const ensureFile = (path: string): MutableFile => {
    let file = files.get(path);
    if (!file) {
      file = {
        path,
        evidence: [],
        evidenceIds: new Set<string>(),
        findings: [],
        nodeCount: 0,
        worstSeverity: null,
      };
      files.set(path, file);
    }
    return file;
  };

  for (const scope of map.coverage.scopes ?? []) {
    if (scope.path) ensureFile(scope.path);
  }

  for (const evidence of map.evidence) {
    const path = evidence.location?.path;
    if (!path) continue;
    const file = ensureFile(path);
    if (file.evidenceIds.has(evidence.id)) continue;
    file.evidenceIds.add(evidence.id);
    file.evidence.push({
      id: evidence.id,
      line: evidence.location.line,
      endLine: evidence.location.endLine,
    });
  }

  const nodePaths = new Map<string, Set<string>>();
  for (const node of map.nodes) {
    const paths = new Set<string>();
    for (const evidenceId of node.evidenceRefs) {
      const evidence = evidenceById.get(evidenceId);
      if (evidence?.location?.path) paths.add(evidence.location.path);
    }
    nodePaths.set(node.id, paths);
    for (const path of paths) ensureFile(path).nodeCount += 1;
  }

  for (const finding of findings) {
    const path = finding.primaryLocation?.path;
    if (!path) continue;
    const file = ensureFile(path);
    if (!file.findings.some((item) => item.id === finding.id)) {
      const fact: FileFindingFact = {
        id: finding.id,
        ruleId: finding.ruleId,
        severity: finding.severity,
        status: finding.status,
        confidence: finding.confidence,
        line: finding.primaryLocation.line,
      };
      file.findings.push(fact);
      file.worstSeverity = worst(file.worstSeverity, finding.severity);
    }
    for (const evidenceId of [
      ...(finding.relatedEvidenceIds ?? []),
      ...(finding.traceEvidenceIds ?? []),
    ]) {
      const evidence = evidenceById.get(evidenceId);
      if (evidence?.location?.path) ensureFile(evidence.location.path);
    }
  }

  const links = new Map<string, MutableLink>();
  let linksOmitted = 0;
  let linksInsideFiles = 0;

  for (const edge of map.edges) {
    const fromPaths = nodePaths.get(edge.fromId);
    const toPaths = nodePaths.get(edge.toId);
    const hasEvidence = (edge.evidenceRefs?.length ?? 0) > 0;
    if (
      !hasEvidence ||
      edge.basis !== 'source' ||
      edge.resolution === 'unknown' ||
      !fromPaths?.size ||
      !toPaths?.size
    ) {
      linksOmitted += 1;
      continue;
    }

    let crossFile = false;
    for (const fromPath of fromPaths) {
      for (const toPath of toPaths) {
        if (fromPath === toPath) continue;
        crossFile = true;
        const key = `${fromPath}|${toPath}|${edge.kind}`;
        let link = links.get(key);
        if (!link) {
          link = {
            id: key,
            fromPath,
            toPath,
            kind: edge.kind,
            label: fileLinkLabel(edge.kind),
            resolution: edge.resolution === 'resolved' ? 'resolved' : 'partial',
            evidenceCount: 0,
            evidenceIds: new Set<string>(),
          };
          links.set(key, link);
        }
        if (link.resolution === 'resolved' && edge.resolution !== 'resolved') {
          link.resolution = 'partial';
        }
        for (const evidenceId of edge.evidenceRefs) link.evidenceIds.add(evidenceId);
      }
    }
    if (!crossFile) linksInsideFiles += 1;
  }

  const sortedFiles = [...files.values()]
    .map(({ evidenceIds: _evidenceIds, ...file }) => ({
      ...file,
      evidence: [...file.evidence].sort((a, b) => a.line - b.line || a.id.localeCompare(b.id)),
      findings: [...file.findings].sort(
        (a, b) =>
          SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity] ||
          a.line - b.line ||
          a.ruleId.localeCompare(b.ruleId),
      ),
    }))
    .sort((a, b) => a.path.localeCompare(b.path));

  const sortedLinks = [...links.values()]
    .map(({ evidenceIds, ...link }) => ({
      ...link,
      evidenceCount: evidenceIds.size,
    }))
    .sort(
      (a, b) =>
        a.fromPath.localeCompare(b.fromPath) ||
        a.toPath.localeCompare(b.toPath) ||
        a.kind.localeCompare(b.kind),
    );

  return {
    files: sortedFiles,
    links: sortedLinks,
    coverage: {
      status: map.coverage.status,
      filesTotal: map.coverage.filesTotal,
      filesVisited: map.coverage.filesVisited,
      skippedScopes: [...(map.coverage.skippedScopes ?? [])],
    },
    linksOmitted,
    linksInsideFiles,
  };
}