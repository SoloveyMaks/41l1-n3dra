import type { ViewEdge, ViewNode } from "./types";

export const NODE_WIDTH = 248;
export const NODE_HEIGHT = 232;
const H_GAP = 56;
const V_GAP = 34;

const KIND_COLUMN: Record<string, number> = {
  actor: 0,
  route: 1,
  security_chain: 2,
  matcher: 2,
  guard: 2,
  function: 3,
  source: 3,
  sink: 4,
  data: 5,
  config: 4,
};
const kindColumn = (node: ViewNode) => KIND_COLUMN[node.kind] ?? 3;

type Position = { x: number; y: number };

function stronglyConnected(ids: string[], adjacency: Map<string, string[]>): string[][] {
  let index = 0;
  const indices = new Map<string, number>();
  const lowlink = new Map<string, number>();
  const stack: string[] = [];
  const onStack = new Set<string>();
  const components: string[][] = [];

  const visit = (node: string) => {
    indices.set(node, index);
    lowlink.set(node, index);
    index += 1;
    stack.push(node);
    onStack.add(node);

    for (const next of adjacency.get(node) ?? []) {
      if (!indices.has(next)) {
        visit(next);
        lowlink.set(node, Math.min(lowlink.get(node)!, lowlink.get(next)!));
      } else if (onStack.has(next)) {
        lowlink.set(node, Math.min(lowlink.get(node)!, indices.get(next)!));
      }
    }

    if (lowlink.get(node) === indices.get(node)) {
      const component: string[] = [];
      let current: string | undefined;
      do {
        current = stack.pop();
        if (!current) break;
        onStack.delete(current);
        component.push(current);
      } while (current !== node);
      components.push(component.sort());
    }
  };

  for (const node of ids) if (!indices.has(node)) visit(node);
  return components;
}

/** Deterministic left-to-right layers over the real graph; cycles share one layer. */
export function computeMapLayout(
  nodes: readonly ViewNode[],
  edges: readonly ViewEdge[],
): Record<string, Position> {
  if (!nodes.length) return {};
  const orderedIds = nodes.map((node) => node.id).sort();
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const adjacency = new Map<string, string[]>(orderedIds.map((id) => [id, []]));
  for (const edge of edges) {
    if (!byId.has(edge.fromId) || !byId.has(edge.toId)) continue;
    adjacency.get(edge.fromId)?.push(edge.toId);
  }

  const components = stronglyConnected(orderedIds, adjacency);
  const componentOf = new Map<string, number>();
  components.forEach((component, index) =>
    component.forEach((node) => componentOf.set(node, index)),
  );

  const componentNodes = components.map((component) =>
    component.map((id) => byId.get(id)!).sort((a, b) =>
      kindColumn(a) - kindColumn(b) || a.label.localeCompare(b.label) || a.id.localeCompare(b.id),
    ),
  );
  const componentBase = componentNodes.map((items) =>
    Math.max(...items.map(kindColumn)),
  );
  const componentLabel = componentNodes.map((items) =>
    items[0]?.label ?? "",
  );

  const dag = new Map<number, Set<number>>(
    components.map((_, index) => [index, new Set<number>()]),
  );
  const indegree = new Array<number>(components.length).fill(0);
  for (const edge of edges) {
    const from = componentOf.get(edge.fromId);
    const to = componentOf.get(edge.toId);
    if (from === undefined || to === undefined || from === to) continue;
    if (!dag.get(from)!.has(to)) {
      dag.get(from)!.add(to);
      indegree[to] += 1;
    }
  }

  const rank = componentBase.slice();
  const ready = components
    .map((_, index) => index)
    .filter((index) => indegree[index] === 0)
    .sort(
      (a, b) => componentBase[a] - componentBase[b] ||
        componentLabel[a].localeCompare(componentLabel[b]) ||
        a - b,
    );
  while (ready.length) {
    const current = ready.shift()!;
    for (const next of [...(dag.get(current) ?? [])].sort((a, b) => a - b)) {
      rank[next] = Math.max(rank[next], rank[current] + 1);
      indegree[next] -= 1;
      if (indegree[next] === 0) {
        ready.push(next);
        ready.sort(
          (a, b) => componentBase[a] - componentBase[b] ||
            componentLabel[a].localeCompare(componentLabel[b]) ||
            a - b,
        );
      }
    }
  }

  const byRank = new Map<number, number[]>();
  components.forEach((_, index) => {
    const list = byRank.get(rank[index]) ?? [];
    list.push(index);
    byRank.set(rank[index], list);
  });
  for (const list of byRank.values()) {
    list.sort(
      (a, b) => componentBase[a] - componentBase[b] ||
        componentLabel[a].localeCompare(componentLabel[b]) ||
        a - b,
    );
  }

  const predecessors = new Map<number, Set<number>>(
    components.map((_, index) => [index, new Set<number>()]),
  );
  for (const [from, targets] of dag) {
    for (const to of targets) predecessors.get(to)?.add(from);
  }

  // Barycenter pass: align a layer next to the rows of its incoming edges.
  // This keeps a real route actor → matcher → guard → route → handler → sink
  // visually horizontal instead of scattering it across centered columns.
  const rowOf = new Map<number, number>();
  for (const [column, list] of [...byRank.entries()].sort((a, b) => a[0] - b[0])) {
    const scored = list.map((index, order) => {
      const rows = [...(predecessors.get(index) ?? [])]
        .map((parent) => rowOf.get(parent))
        .filter((value): value is number => value !== undefined);
      const score = rows.length
        ? rows.reduce((sum, value) => sum + value, 0) / rows.length
        : order;
      return { index, score, order };
    });
    scored.sort(
      (a, b) => a.score - b.score ||
        componentBase[a.index] - componentBase[b.index] ||
        a.order - b.order,
    );
    let row = 0;
    for (const item of scored) {
      rowOf.set(item.index, row);
      row += componentNodes[item.index].length;
    }
  }

  const positions: Record<string, Position> = {};
  for (const [column, list] of [...byRank.entries()].sort((a, b) => a[0] - b[0])) {
    for (const index of list) {
      const start = rowOf.get(index) ?? 0;
      componentNodes[index].forEach((node, offset) => {
        positions[node.id] = {
          x: column * (NODE_WIDTH + H_GAP),
          y: (start + offset) * (NODE_HEIGHT + V_GAP),
        };
      });
    }
  }
  return positions;
}
const SEVERITY_ORDER: Record<string, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
  info: 4,
};

export function initialFocusNodeIds(
  nodes: readonly ViewNode[],
  edges: readonly ViewEdge[],
  findings: readonly { nodeIds: string[]; severity: string; status: string }[],
): string[] {
  if (!nodes.length) return [];
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const adjacency = new Map<string, Set<string>>(nodes.map((node) => [node.id, new Set()]));
  for (const edge of edges) {
    if (!byId.has(edge.fromId) || !byId.has(edge.toId)) continue;
    adjacency.get(edge.fromId)?.add(edge.toId);
    adjacency.get(edge.toId)?.add(edge.fromId);
  }

  const actorIds = nodes.filter((node) => node.kind === "actor").map((node) => node.id);
  const routeIds = nodes.filter((node) => node.kind === "route").map((node) => node.id);
  const starts = actorIds.length ? actorIds : (routeIds.length ? routeIds : nodes.slice(0, 1).map((node) => node.id));

  const shortestPath = (target: string): string[] => {
    const queue: string[] = [];
    const parent = new Map<string, string | null>();
    for (const start of starts) {
      parent.set(start, null);
      queue.push(start);
    }
    while (queue.length) {
      const current = queue.shift()!;
      if (current === target) {
        const path: string[] = [];
        let cursor: string | null = current;
        while (cursor) {
          path.unshift(cursor);
          cursor = parent.get(cursor) ?? null;
        }
        return path;
      }
      for (const next of adjacency.get(current) ?? []) {
        if (parent.has(next)) continue;
        parent.set(next, current);
        queue.push(next);
      }
    }
    return [target];
  };

  const focus = new Set<string>();
  const preferred = [...findings]
    .filter((finding) => finding.nodeIds.some((id) => byId.has(id)))
    .sort((a, b) =>
      (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9) ||
      a.status.localeCompare(b.status) ||
      a.nodeIds.join("|").localeCompare(b.nodeIds.join("|")),
    )[0];

  const seedPriority: Record<string, number> = {
    sink: 0,
    data: 1,
    function: 2,
    route: 3,
    guard: 4,
    source: 5,
    actor: 6,
  };
  const preferredSeeds = preferred
    ? preferred.nodeIds
        .filter((id) => byId.has(id))
        .sort((a, b) =>
          (seedPriority[byId.get(a)?.kind ?? ""] ?? 9) -
          (seedPriority[byId.get(b)?.kind ?? ""] ?? 9),
        )
    : [];
  const seeds = preferredSeeds.length
    ? preferredSeeds.slice(0, 1)
    : (routeIds.length ? routeIds.slice(0, 1) : starts);

  for (const seed of seeds) {
    for (const id of shortestPath(seed)) focus.add(id);
    for (const next of adjacency.get(seed) ?? []) {
      const kind = byId.get(next)?.kind;
      if (kind === "sink" || kind === "data" || kind === "source") focus.add(next);
    }
  }

  if (!focus.size) {
    const [first] = nodes;
    focus.add(first.id);
    for (const next of adjacency.get(first.id) ?? []) focus.add(next);
  }
  return [...focus].slice(0, 16);
}
