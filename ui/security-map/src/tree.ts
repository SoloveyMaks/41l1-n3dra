import type { ViewEdge, ViewNode } from "./types";

export type TreeRow = {
  node: ViewNode;
  depth: number;
  parentId: string | null;
  edgeId: string | null;
  cycle: boolean;
  crosslink: boolean;
  rootId: string;
  hasChildren: boolean;
};

export type ExplorationTree = {
  roots: string[];
  orphans: string[];
  rows: TreeRow[];
  visibleIds: Set<string>;
  visibleEdgeIds: Set<string>;
  treeEdgeIds: Set<string>;
  crosslinkEdgeIds: Set<string>;
};

/**
 * Exploration forest over the real graph. It never rewrites an edge into a
 * tree edge: a repeated node is marked as a crosslink/cycle, and every edge
 * whose endpoints are visible remains available to the graph canvas.
 */
export function buildExplorationTree(
  nodes: readonly ViewNode[],
  edges: readonly ViewEdge[],
  expanded: ReadonlySet<string>,
): ExplorationTree {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const outgoing = new Map<string, ViewEdge[]>(nodes.map((node) => [node.id, []]));
  const incomingCount = new Map<string, number>(nodes.map((node) => [node.id, 0]));
  const degree = new Map<string, number>(nodes.map((node) => [node.id, 0]));
  const undirected = new Map<string, Set<string>>(nodes.map((node) => [node.id, new Set()]));
  for (const edge of edges) {
    if (!byId.has(edge.fromId) || !byId.has(edge.toId)) continue;
    outgoing.get(edge.fromId)!.push(edge);
    incomingCount.set(edge.toId, (incomingCount.get(edge.toId) ?? 0) + 1);
    degree.set(edge.fromId, (degree.get(edge.fromId) ?? 0) + 1);
    degree.set(edge.toId, (degree.get(edge.toId) ?? 0) + 1);
    undirected.get(edge.fromId)!.add(edge.toId);
    undirected.get(edge.toId)!.add(edge.fromId);
  }
  for (const list of outgoing.values()) list.sort((a, b) => a.id.localeCompare(b.id));

  const roots = nodes
    .filter((node) => (incomingCount.get(node.id) ?? 0) === 0 && (degree.get(node.id) ?? 0) > 0)
    .map((node) => node.id)
    .sort();
  const rootSet = new Set(roots);
  const seenComponents = new Set<string>();

  // A pure cycle has no in-degree-zero node. Give each weak component a
  // deterministic representative so it remains reachable, without inventing
  // a node or a synthetic edge.
  for (const node of [...nodes].sort((a, b) => a.id.localeCompare(b.id))) {
    if (seenComponents.has(node.id)) continue;
    const stack = [node.id];
    const component: string[] = [];
    while (stack.length) {
      const id = stack.pop()!;
      if (seenComponents.has(id)) continue;
      seenComponents.add(id);
      component.push(id);
      for (const next of undirected.get(id) ?? []) stack.push(next);
    }
    if (!component.some((id) => rootSet.has(id))) {
      const candidate = component
        .map((id) => byId.get(id)!)
        .sort(
          (a, b) =>
            (incomingCount.get(a.id) ?? 0) - (incomingCount.get(b.id) ?? 0) ||
            a.id.localeCompare(b.id),
        )[0];
      if (candidate) {
        roots.push(candidate.id);
        rootSet.add(candidate.id);
      }
    }
  }
  roots.sort();

  const orphans = nodes
    .filter((node) => (degree.get(node.id) ?? 0) === 0)
    .map((node) => node.id)
    .sort();

  const rows: TreeRow[] = [];
  const visited = new Set<string>();
  const treeEdgeIds = new Set<string>();
  const reportedCrosslinks = new Set<string>();

  const visit = (
    id: string,
    depth: number,
    parentId: string | null,
    edgeId: string | null,
    rootId: string,
    path: ReadonlySet<string>,
  ) => {
    const node = byId.get(id);
    if (!node) return;
    if (path.has(id)) {
      rows.push({
        node,
        depth,
        parentId,
        edgeId,
        cycle: true,
        crosslink: false,
        rootId,
        hasChildren: (outgoing.get(id)?.length ?? 0) > 0,
      });
      if (edgeId) treeEdgeIds.add(edgeId);
      return;
    }
    const crosslink = visited.has(id);
    rows.push({
      node,
      depth,
      parentId,
      edgeId,
      cycle: false,
      crosslink,
      rootId,
      hasChildren: (outgoing.get(id)?.length ?? 0) > 0,
    });
    if (crosslink) {
      if (edgeId) reportedCrosslinks.add(edgeId);
      return;
    }
    visited.add(id);
    if (edgeId) treeEdgeIds.add(edgeId);
    if (!expanded.has(id)) return;
    const nextPath = new Set(path).add(id);
    for (const edge of outgoing.get(id) ?? []) {
      visit(edge.toId, depth + 1, id, edge.id, rootId, nextPath);
    }
  };

  for (const root of roots) {
    if (!visited.has(root)) visit(root, 0, null, null, root, new Set());
  }
  for (const orphan of orphans) {
    if (!visited.has(orphan)) visit(orphan, 0, null, null, orphan, new Set());
  }

  const visibleIds = new Set(rows.map((row) => row.node.id));
  const visibleEdgeIds = new Set<string>();
  const crosslinkEdgeIds = new Set<string>(reportedCrosslinks);
  for (const edge of edges) {
    if (!visibleIds.has(edge.fromId) || !visibleIds.has(edge.toId)) continue;
    visibleEdgeIds.add(edge.id);
    if (!treeEdgeIds.has(edge.id)) crosslinkEdgeIds.add(edge.id);
  }

  return {
    roots,
    orphans,
    rows,
    visibleIds,
    visibleEdgeIds,
    treeEdgeIds,
    crosslinkEdgeIds,
  };
}