import type { MapViewModel, ViewFinding, ViewNode } from "./types";

export type AccessPath = {
  id: string;
  nodeIds: string[];
  edgeIds: string[];
  labels: string[];
  guarded: boolean;
  permitted: boolean;
  findings: ViewFinding[];
  terminalKind: string;
};

export type PathSummary = {
  paths: AccessPath[];
  total: number;
  truncated: boolean;
  visitedNodes: Set<string>;
};

const DEFAULT_MAX_DEPTH = 10;
const DEFAULT_MAX_PATHS = 300;

/**
 * Every simple path from an actor or root to a terminal node. The tree hides
 * alternative edges as crosslinks; this list keeps them visible without
 * expanding cycles forever. Hard bounds keep a hostile or huge graph from
 * freezing the UI.
 */
export function enumerateAccessPaths(
  model: MapViewModel,
  limits: { maxDepth?: number; maxPaths?: number } = {},
): PathSummary {
  const maxDepth = limits.maxDepth ?? DEFAULT_MAX_DEPTH;
  const maxPaths = limits.maxPaths ?? DEFAULT_MAX_PATHS;
  const byId = new Map(model.nodes.map((node) => [node.id, node]));
  const outgoing = new Map<string, typeof model.edges>();
  const incoming = new Map<string, number>();
  for (const node of model.nodes) {
    outgoing.set(node.id, []);
    incoming.set(node.id, 0);
  }
  for (const edge of model.edges) {
    if (!byId.has(edge.fromId) || !byId.has(edge.toId)) continue;
    outgoing.get(edge.fromId)?.push(edge);
    incoming.set(edge.toId, (incoming.get(edge.toId) ?? 0) + 1);
  }
  for (const list of outgoing.values()) list.sort((a, b) => a.id.localeCompare(b.id));

  const actorIds = model.nodes
    .filter((node) => node.kind === "actor")
    .map((node) => node.id);
  const rootIds = model.nodes
    .filter((node) => node.kind !== "file" && (incoming.get(node.id) ?? 0) === 0)
    .map((node) => node.id);
  const starts = [...new Set([...actorIds, ...rootIds])].sort();

  const paths: AccessPath[] = [];
  let truncated = false;
  const visitedNodes = new Set<string>();
  const seen = new Set<string>();

  const findingsFor = (nodes: readonly string[]): ViewFinding[] =>
    model.findings.filter((finding) => finding.nodeIds.some((id) => nodes.includes(id)));

  const record = (nodeIds: string[], edgeIds: string[], terminal: ViewNode): void => {
    visitedNodes.add(nodeIds[0]);
    const key = nodeIds.join(">");
    if (seen.has(key)) return;
    seen.add(key);
    if (paths.length >= maxPaths) {
      truncated = true;
      return;
    }
    const guardKinds: Record<string, true> = {
      guard: true,
      security_chain: true,
      matcher: true,
    };
    paths.push({
      id: `path:${key}`,
      nodeIds,
      edgeIds,
      labels: nodeIds.map((id) => byId.get(id)?.label ?? id),
      guarded: nodeIds.some((id) => guardKinds[byId.get(id)?.kind ?? ""] === true),
      permitted: edgeIds.some(
        (id) => model.edges.find((edge) => edge.id === id)?.kind === "permits",
      ),
      findings: findingsFor(nodeIds),
      terminalKind: terminal.kind,
    });
  };

  const walk = (
    id: string,
    nodeIds: string[],
    edgeIds: string[],
    path: ReadonlySet<string>,
  ): void => {
    const node = byId.get(id);
    if (!node || node.kind === "file" || path.has(id) || nodeIds.length > maxDepth) return;
    const nextPath = new Set(path).add(id);
    const nextNodes = [...nodeIds, id];
    const edges = outgoing.get(id) ?? [];
    const terminal =
      edges.length === 0 || node.kind === "data" || node.kind === "sink";
    if (terminal) {
      record(nextNodes, edgeIds, node);
      return;
    }
    let descended = false;
    for (const edge of edges) {
      if (nextPath.has(edge.toId)) continue;
      descended = true;
      walk(edge.toId, nextNodes, [...edgeIds, edge.id], nextPath);
    }
    if (!descended) {
      // Every continuation is a cycle or truncated depth: keep the path we
      // actually have, so a cycle does not erase the reachable prefix.
      record(nextNodes, edgeIds, node);
    }
  };

  for (const start of starts) walk(start, [], [], new Set());

  return { paths, total: paths.length, truncated, visitedNodes };
}
