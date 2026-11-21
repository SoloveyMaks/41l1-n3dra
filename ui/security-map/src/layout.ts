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

  const maxRows = Math.max(
    1,
    ...[...byRank.values()].map((list) =>
      list.reduce((sum, index) => sum + componentNodes[index].length, 0),
    ),
  );
  const positions: Record<string, Position> = {};
  for (const [column, list] of [...byRank.entries()].sort((a, b) => a[0] - b[0])) {
    const rows = list.reduce((sum, index) => sum + componentNodes[index].length, 0);
    const offset = ((maxRows - rows) * (NODE_HEIGHT + V_GAP)) / 2;
    let row = 0;
    for (const index of list) {
      for (const node of componentNodes[index]) {
        positions[node.id] = {
          x: column * (NODE_WIDTH + H_GAP),
          y: offset + row * (NODE_HEIGHT + V_GAP),
        };
        row += 1;
      }
    }
  }
  return positions;
}