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
  access: "public" | "guarded" | "unknown";
  permitLabels: string[];
};

export type AccessSummary = {
  rows: AccessRow[];
  routeCount: number;
  publicCount: number;
  unknownCount: number;
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
  permits: ViewNode[];
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
      const linked = new Set([route.id, ...(handler ? [handler.id] : [])]);
      const requires = model.edges.filter(
        (edge) =>
          edge.kind === "requires" &&
          (linked.has(edge.fromId) || linked.has(edge.toId)),
      );
      const guards = requires.flatMap((edge) => {
        const node = nodeById(model, linked.has(edge.fromId) ? edge.toId : edge.fromId);
        return node !== undefined && !linked.has(node.id) ? [node] : [];
      });
      const permits = model.edges
        .filter((edge) => edge.kind === "permits" && edge.toId === route.id)
        .map((edge) => nodeById(model, edge.fromId))
        .filter((node): node is ViewNode => Boolean(node));
      const paths = new Set([
        ...evidencePaths(model, route.evidenceRefs),
        ...(handler ? evidencePaths(model, handler.evidenceRefs) : []),
      ]);
      const linkedIds = new Set([route.id, ...(handler ? [handler.id] : [])]);
      const findings = model.findings.filter((finding) =>
        finding.nodeIds.some((id) => linkedIds.has(id)),
      );
      return { route, handler, guards, permits, paths, findings };
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
  const rows: AccessRow[] = contexts.map(({ route, handler, guards, permits, findings }) => {
    const match = methodPattern.exec(route.label.trim());
    const permitLabels = permits.map((node) => node.label);
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
      access: permits.length ? "public" : guards.length ? "guarded" : "unknown",
      permitLabels,
    };
  });
  const accessRank: Record<AccessRow["access"], number> = {
    public: 0,
    unknown: 1,
    guarded: 2,
  };
  rows.sort((a, b) => {
    const accessDelta = accessRank[a.access] - accessRank[b.access];
    if (accessDelta !== 0) return accessDelta;
    const aSeverity = a.highestSeverity ? severityRank[a.highestSeverity] ?? 9 : 9;
    const bSeverity = b.highestSeverity ? severityRank[b.highestSeverity] ?? 9 : 9;
    if (aSeverity !== bSeverity) return aSeverity - bSeverity;
    return a.path.localeCompare(b.path);
  });
  return {
    rows,
    routeCount: rows.length,
    publicCount: rows.filter((row) => row.access === "public").length,
    unknownCount: rows.filter((row) => row.access === "unknown").length,
    withFindingsCount: rows.filter((row) => row.findings.length > 0).length,
  };
}
