import type { MapViewModel, ViewFinding, ViewNode } from "./types";

export type AccessRow = {
  routeId: string;
  method: string;
  path: string;
  label: string;
  handlerId: string | null;
  handler: string;
  guards: string[];
  guardIds: string[];
  findings: ViewFinding[];
  highestSeverity: string | null;
  unchecked: boolean;
};

export type AccessSummary = {
  rows: AccessRow[];
  routeCount: number;
  uncheckedCount: number;
  withFindingsCount: number;
};

const severityRank: Record<string, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
  info: 4,
};

const methodPattern = /^([A-Z]+)\s+(.+)$/;

type RouteContext = {
  route: ViewNode;
  handler: ViewNode | null;
  guards: ViewNode[];
  paths: Set<string>;
  findings: ViewFinding[];
};

function nodeById(model: MapViewModel, id: string | null): ViewNode | undefined {
  return id ? model.nodes.find((node) => node.id === id) : undefined;
}

function evidencePaths(model: MapViewModel, evidenceRefs: readonly string[]): Set<string> {
  const paths = new Set<string>();
  for (const ref of evidenceRefs) {
    const evidence = model.evidence.find((item) => item.id === ref);
    const path = evidence?.location?.path;
    if (path) paths.add(path);
  }
  return paths;
}

function pathsOverlap(left: Set<string>, right: Set<string>): boolean {
  for (const path of right) {
    if (left.has(path)) return true;
  }
  return false;
}

function buildContexts(model: MapViewModel): RouteContext[] {
  return model.nodes
    .filter((node) => node.kind === "route")
    .map((route) => {
      const routeEdge = model.edges.find(
        (edge) => edge.kind === "routes_to" && edge.fromId === route.id,
      );
      const handler = nodeById(model, routeEdge?.toId ?? null) ?? null;
      const requires = model.edges.filter(
        (edge) =>
          edge.kind === "requires" &&
          (edge.fromId === route.id || (handler !== null && edge.fromId === handler.id)),
      );
      const guards = requires
        .map((edge) => nodeById(model, edge.toId))
        .filter((node): node is ViewNode => Boolean(node));
      const paths = new Set([
        ...evidencePaths(model, route.evidenceRefs),
        ...(handler ? evidencePaths(model, handler.evidenceRefs) : []),
      ]);
      const linkedIds = new Set([route.id, ...(handler ? [handler.id] : [])]);
      const findings = model.findings.filter((finding) =>
        finding.nodeIds.some((id) => linkedIds.has(id)),
      );
      return { route, handler, guards, paths, findings };
    });
}

/**
 * Findings without graph links are attached by file only when the file belongs
 * to exactly one route. A shared file would otherwise paint every endpoint in
 * the same red and recreate the "everything is broken" soup.
 */
function attachUnlinkedFindings(model: MapViewModel, contexts: RouteContext[]): void {
  for (const finding of model.findings) {
    if (finding.nodeIds.length) continue;
    const findingPaths = evidencePaths(model, finding.evidenceRefs);
    const matches = contexts.filter((context) => pathsOverlap(context.paths, findingPaths));
    if (matches.length === 1) matches[0].findings.push(finding);
  }
}

function highestSeverity(findings: readonly ViewFinding[]): string | null {
  let best: string | null = null;
  for (const finding of findings) {
    if (best === null || (severityRank[finding.severity] ?? 9) < (severityRank[best] ?? 9)) {
      best = finding.severity;
    }
  }
  return best;
}

export function buildAccessSummary(model: MapViewModel): AccessSummary {
  const contexts = buildContexts(model);
  attachUnlinkedFindings(model, contexts);
  const rows: AccessRow[] = contexts.map(({ route, handler, guards, findings }) => {
    const match = methodPattern.exec(route.label.trim());
    return {
      routeId: route.id,
      method: match ? match[1] : "ANY",
      path: match ? match[2] : route.label,
      label: route.label,
      handlerId: handler?.id ?? null,
      handler: handler?.label ?? "обработчик не найден",
      guards: guards.map((guard) => guard.label),
      guardIds: guards.map((guard) => guard.id),
      findings,
      highestSeverity: highestSeverity(findings),
      unchecked: guards.length === 0,
    };
  });
  rows.sort((a, b) => {
    if (a.unchecked !== b.unchecked) return a.unchecked ? -1 : 1;
    const aSeverity = a.highestSeverity ? severityRank[a.highestSeverity] ?? 9 : 9;
    const bSeverity = b.highestSeverity ? severityRank[b.highestSeverity] ?? 9 : 9;
    if (aSeverity !== bSeverity) return aSeverity - bSeverity;
    return a.path.localeCompare(b.path);
  });
  return {
    rows,
    routeCount: rows.length,
    uncheckedCount: rows.filter((row) => row.unchecked).length,
    withFindingsCount: rows.filter((row) => row.findings.length > 0).length,
  };
}
