import type { ViewEdge, ViewNode } from "./types";

export const NODE_WIDTH = 248;
export const NODE_MIN_HEIGHT = 132;
export const NODE_HEIGHT = NODE_MIN_HEIGHT;
const H_GAP = 64;
const V_GAP = 36;
const COMPONENT_GAP = 54;

/**
 * Actual cards are 248px wide and clamp their title to two lines. This estimate
 * is deliberately slightly larger than the rendered card so the layered layout
 * never relies on a lucky font or a shorter label. The browser still uses the
 * measured size for fit-view and edge routing.
 */
export function estimateNodeHeight(node: ViewNode): number {
  const length = [...(node.label || "")].length;
  const lines = Math.max(1, Math.min(3, Math.ceil(length / 30)));
  return Math.max(NODE_MIN_HEIGHT, 104 + lines * 17);
}

const KIND_COLUMN: Record<string, number> = {
  actor: 0,
  route: 1,
  security_chain: 2,
  matcher: 2,
  guard: 2,
  function: 3,
  source: 4,
  sink: 5,
  data: 6,
  config: 4,
};
const kindColumn = (node: ViewNode) => KIND_COLUMN[node.kind] ?? 3;

type Position = { x: number; y: number };
type Placement = {
  positions: Record<string, Position>;
  centers: Map<number, number>;
  heights: Map<number, number>;
};

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

/**
 * Deterministic layered layout over the real graph.
 *
 * - SCCs keep cycles together; every known edge remains in the model.
 * - Ranks are the longest path from a kind-aware seed, so who → route → access
 *   → handler → data/operation reads left-to-right.
 * - Components are ordered by predecessor/successor barycenters over several
 *   deterministic sweeps to reduce crossings.
 * - Node heights are estimated per label and stacked, so cards cannot overlap
 *   vertically; columns cannot overlap horizontally.
 */
export function computeMapLayout(
  nodes: readonly ViewNode[],
  edges: readonly ViewEdge[],
): Record<string, Position> {
  if (!nodes.length) return {};
  const orderedIds = nodes.map((node) => node.id).sort();
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const adjacency = new Map<string, string[]>(orderedIds.map((id) => [id, []]));
  const incoming = new Map<string, string[]>(orderedIds.map((id) => [id, []]));
  for (const edge of edges) {
    if (!byId.has(edge.fromId) || !byId.has(edge.toId)) continue;
    adjacency.get(edge.fromId)?.push(edge.toId);
    incoming.get(edge.toId)?.push(edge.fromId);
  }

  const components = stronglyConnected(orderedIds, adjacency);
  const componentOf = new Map<string, number>();
  components.forEach((component, index) =>
    component.forEach((node) => componentOf.set(node, index)),
  );

  const componentNodes = components.map((component) =>
    component
      .map((id) => byId.get(id)!)
      .sort(
        (a, b) =>
          kindColumn(a) - kindColumn(b) ||
          a.label.localeCompare(b.label) ||
          a.id.localeCompare(b.id),
      ),
  );
  const componentBase = componentNodes.map((items) =>
    Math.max(...items.map(kindColumn)),
  );
  const componentLabel = componentNodes.map((items) => items[0]?.label ?? "");
  const componentHeight = componentNodes.map((items) =>
    items.reduce((total, item) => total + estimateNodeHeight(item), 0) +
    Math.max(0, items.length - 1) * V_GAP,
  );

  const dag = new Map<number, Set<number>>(
    components.map((_, index) => [index, new Set<number>()]),
  );
  const inverse = new Map<number, Set<number>>(
    components.map((_, index) => [index, new Set<number>()]),
  );
  const indegree = new Array<number>(components.length).fill(0);
  for (const edge of edges) {
    const from = componentOf.get(edge.fromId);
    const to = componentOf.get(edge.toId);
    if (from === undefined || to === undefined || from === to) continue;
    if (!dag.get(from)!.has(to)) {
      dag.get(from)!.add(to);
      inverse.get(to)!.add(from);
      indegree[to] += 1;
    }
  }

  const rank = componentBase.slice();
  const ready = components
    .map((_, index) => index)
    .filter((index) => indegree[index] === 0)
    .sort(compareComponents(componentBase, componentLabel));
  while (ready.length) {
    const current = ready.shift()!;
    for (const next of [...(dag.get(current) ?? [])].sort((a, b) => a - b)) {
      rank[next] = Math.max(rank[next], rank[current] + 1);
      indegree[next] -= 1;
      if (indegree[next] === 0) {
        ready.push(next);
        ready.sort(compareComponents(componentBase, componentLabel));
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
    list.sort(compareComponents(componentBase, componentLabel));
  }

  const place = (order: Map<number, number[]>): Placement => {
    const positions: Record<string, Position> = {};
    const centers = new Map<number, number>();
    const heights = new Map<number, number>();
    const columnHeights = [...order.entries()].map(([column, list]) => {
      const total = list.reduce((sum, index) => sum + componentHeight[index], 0) +
        Math.max(0, list.length - 1) * COMPONENT_GAP;
      return [column, total] as const;
    });
    const maxHeight = Math.max(1, ...columnHeights.map(([, height]) => height));
    for (const [column, list] of [...order.entries()].sort((a, b) => a[0] - b[0])) {
      const total = columnHeights.find(([rankKey]) => rankKey === column)?.[1] ?? 0;
      let y = (maxHeight - total) / 2;
      for (const index of list) {
        const blockHeight = componentHeight[index];
        const blockTop = y;
        let nodeY = blockTop;
        for (const node of componentNodes[index]) {
          const height = estimateNodeHeight(node);
          positions[node.id] = {
            x: column * (NODE_WIDTH + H_GAP),
            y: nodeY,
          };
          nodeY += height + V_GAP;
        }
        centers.set(index, blockTop + blockHeight / 2);
        heights.set(index, blockHeight);
        y += blockHeight + COMPONENT_GAP;
      }
    }
    return { positions, centers, heights };
  };

  let order = new Map<number, number[]>(
    [...byRank.entries()].map(([column, list]) => [column, [...list]]),
  );
  let placement = place(order);

  const orderedRanks = [...order.keys()].sort((a, b) => a - b);
  for (let sweep = 0; sweep < 4; sweep += 1) {
    // Downward barycenter: align against predecessors above.
    for (const column of [...orderedRanks].reverse()) {
      const list = order.get(column) ?? [];
      list.sort(
        byBarycenter(list, inverse, placement.centers, componentBase, componentLabel),
      );
      order = new Map(order).set(column, list);
    }
    placement = place(order);

    // Upward barycenter: align against successors below.
    for (const column of orderedRanks) {
      const list = order.get(column) ?? [];
      list.sort(
        byBarycenter(list, dag, placement.centers, componentBase, componentLabel),
      );
      order = new Map(order).set(column, list);
    }
    placement = place(order);
  }

  return placement.positions;
}

function compareComponents(
  componentBase: number[],
  componentLabel: string[],
) {
  return (a: number, b: number) =>
    componentBase[a] - componentBase[b] ||
    componentLabel[a].localeCompare(componentLabel[b]) ||
    a - b;
}

function byBarycenter(
  list: number[],
  neighbours: Map<number, Set<number>>,
  centers: Map<number, number>,
  componentBase: number[],
  componentLabel: string[],
) {
  const currentIndex = new Map(list.map((index, order) => [index, order]));
  const weighted = new Map<number, number>();
  for (const index of list) {
    const values = [...(neighbours.get(index) ?? [])]
      .map((parent) => centers.get(parent))
      .filter((value): value is number => value !== undefined);
    weighted.set(
      index,
      values.length
        ? values.reduce((sum, value) => sum + value, 0) / values.length
        : (centers.get(index) ?? currentIndex.get(index) ?? 0),
    );
  }
  return (a: number, b: number) =>
    (weighted.get(a) ?? 0) - (weighted.get(b) ?? 0) ||
    componentBase[a] - componentBase[b] ||
    componentLabel[a].localeCompare(componentLabel[b]) ||
    a - b;
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
  findings: readonly { id: string; nodeIds: string[]; severity: string; status: string }[],
  findingId?: string,
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
  const selectedFinding = findingId
    ? findings.find((finding) => finding.id === findingId)
    : undefined;
  if (findingId && (!selectedFinding || !selectedFinding.nodeIds.some((id) => byId.has(id)))) {
    return [];
  }
  const preferred = findingId
    ? selectedFinding
    : [...findings]
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