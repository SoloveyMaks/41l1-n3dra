import type { ViewEdge, ViewNode } from "./types";
import type { ExplorationTree } from "./tree";

export const NODE_WIDTH = 248;
export const NODE_MIN_HEIGHT = 132;
export const NODE_HEIGHT = NODE_MIN_HEIGHT;
const H_GAP = 64;
const V_GAP = 36;
const LANE_WIDTH = NODE_WIDTH + H_GAP;
const TREE_GAP_X = 56;
const TREE_LEVEL_HEIGHT = 196;

export type LaneDefinition = {
  index: number;
  key: string;
  title: string;
  hint: string;
};

/**
 * The access story is read in the same direction as the analyzer graph:
 * who → access rule → route → handler → operation/source → data.
 * A rule sits left of the route because the analyzer records "rule applies to
 * route" before the route is dispatched; the UI never reverses an edge.
 */
export const MAP_LANES: readonly LaneDefinition[] = [
  {
    index: 0,
    key: "actor",
    title: "1 · Кто обращается",
    hint: "пользователь, роль или анонимный клиент",
  },
  {
    index: 1,
    key: "access",
    title: "2 · Проверка доступа",
    hint: "правило, шаблон и проверка перед маршрутом",
  },
  {
    index: 2,
    key: "route",
    title: "3 · Маршрут",
    hint: "метод и путь запроса",
  },
  {
    index: 3,
    key: "handler",
    title: "4 · Обработчик",
    hint: "код, который принимает запрос",
  },
  {
    index: 4,
    key: "operation",
    title: "5 · Операция и вход",
    hint: "SQL/IO-вызов и входное значение",
  },
  {
    index: 5,
    key: "data",
    title: "6 · Данные",
    hint: "таблица, поле или объект",
  },
  {
    index: 6,
    key: "problem",
    title: "7 · Проблема",
    hint: "находка, привязанная к узлу",
  },
] as const;

const LANE_BY_KIND: Record<string, number> = {
  actor: 0,
  matcher: 1,
  security_chain: 1,
  guard: 1,
  route: 2,
  function: 3,
  source: 4,
  sink: 4,
  config: 4,
  data: 5,
};

export const laneIndexForKind = (kind: string): number =>
  LANE_BY_KIND[kind] ?? 3;

export const laneDefinition = (index: number): LaneDefinition =>
  MAP_LANES.find((lane) => lane.index === index) ?? MAP_LANES[3];

export const laneX = (index: number): number => index * LANE_WIDTH;

export const PROBLEM_LANE = MAP_LANES.length - 1;

/**
 * Estimate is deliberately slightly larger than the rendered card: 112px for a
 * short label, up to 155px for three lines. The browser still measures the
 * real height for fit-view and edge routing.
 */
export function estimateNodeHeight(node: ViewNode): number {
  const length = [...(node.label || "")].length;
  const lines = Math.max(1, Math.min(3, Math.ceil(length / 30)));
  return Math.max(NODE_MIN_HEIGHT, 104 + lines * 17);
}

type Position = { x: number; y: number };
type NodeBox = { node: ViewNode; height: number; center: number };

/** Deterministic stacked order inside one lane: labels, then ids. */
const stackOrder = (a: ViewNode, b: ViewNode) =>
  a.label.localeCompare(b.label) || a.id.localeCompare(b.id);



/**
 * Deterministic lane layout over the real graph.
 *
 * - x is fixed by node kind, so the chain never wanders between columns and
 *   two routes always share the same vertical line.
 * - y is packed per lane, then ordered by aligned barycenters of real
 *   predecessors/successors. Cards never overlap and crossings drop because
 *   shared actors/rules stay centred between their routes.
 * - Every edge keeps its original source → target direction; no edge or node
 *   is invented and cycles remain visible.
 */
export function computeMapLayout(
  nodes: readonly ViewNode[],
  edges: readonly ViewEdge[],
): Record<string, Position> {
  if (!nodes.length) return {};
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const incoming = new Map<string, Set<string>>(
    nodes.map((node) => [node.id, new Set<string>()]),
  );
  const outgoing = new Map<string, Set<string>>(
    nodes.map((node) => [node.id, new Set<string>()]),
  );
  for (const edge of edges) {
    if (!byId.has(edge.fromId) || !byId.has(edge.toId)) continue;
    outgoing.get(edge.fromId)?.add(edge.toId);
    incoming.get(edge.toId)?.add(edge.fromId);
  }

  const laneOf = new Map<string, number>();
  const lanes = new Map<number, NodeBox[]>();
  for (const node of nodes) {
    const index = laneIndexForKind(node.kind);
    laneOf.set(node.id, index);
    const list = lanes.get(index) ?? [];
    list.push({ node, height: estimateNodeHeight(node), center: 0 });
    lanes.set(index, list);
  }
  for (const lane of lanes.values()) lane.sort((a, b) => stackOrder(a.node, b.node));

  const centers = new Map<string, number>();
  for (const lane of lanes.values()) {
    let y = 0;
    for (const box of lane) {
      box.center = y + box.height / 2;
      centers.set(box.node.id, box.center);
      y += box.height + V_GAP;
    }
    // Centre every lane around zero so the first sweep starts balanced.
    const shift = (y - V_GAP) / 2;
    for (const box of lane) {
      box.center -= shift;
      centers.set(box.node.id, box.center);
    }
  }

  const placeLane = (
    lane: NodeBox[],
    desired: Map<string, number>,
    ordered: Map<string, number>,
  ): number[] => {
    lane.sort((a, b) => {
      const da = desired.get(a.node.id) ?? centers.get(a.node.id) ?? 0;
      const db = desired.get(b.node.id) ?? centers.get(b.node.id) ?? 0;
      return (
        da - db ||
        stackOrder(a.node, b.node) ||
        (ordered.get(a.node.id) ?? 0) - (ordered.get(b.node.id) ?? 0)
      );
    });
    const total =
      lane.reduce((sum, box) => sum + box.height, 0) +
      Math.max(0, lane.length - 1) * V_GAP;
    const target =
      lane.reduce(
        (sum, box) =>
          sum + (desired.get(box.node.id) ?? centers.get(box.node.id) ?? 0),
        0,
      ) / Math.max(1, lane.length);
    let y = target - total / 2;
    const placed: number[] = [];
    for (const box of lane) {
      box.center = y + box.height / 2;
      centers.set(box.node.id, box.center);
      placed.push(box.center);
      y += box.height + V_GAP;
    }
    return placed;
  };

  const orderedLanes = [...lanes.keys()].sort((a, b) => a - b);
  const order = new Map<string, number>();
  for (const index of orderedLanes) {
    lanes.get(index)!.forEach((box, position) => order.set(box.node.id, position));
  }

  const desiredFor = (ids: Set<string>, fallback: number): number => {
    if (!ids.size) return fallback;
    return (
      [...ids].reduce((sum, id) => sum + (centers.get(id) ?? 0), 0) / ids.size
    );
  };

  // Alternating sweeps: align each lane against the already-placed neighbours.
  for (let sweep = 0; sweep < 6; sweep += 1) {
    const forward = sweep % 2 === 0;
    const sequence = forward ? orderedLanes : [...orderedLanes].reverse();
    for (const index of sequence) {
      const lane = lanes.get(index)!;
      const desired = new Map<string, number>();
      for (const box of lane) {
        const reference = forward ? incoming : outgoing;
        desired.set(
          box.node.id,
          desiredFor(
            reference.get(box.node.id) ?? new Set<string>(),
            centers.get(box.node.id) ?? 0,
          ),
        );
      }
      placeLane(lane, desired, order);
    }
  }

  const positions: Record<string, Position> = {};
  for (const index of orderedLanes) {
    for (const box of lanes.get(index)!) {
      positions[box.node.id] = {
        x: laneX(index),
        y: Math.round(box.center - box.height / 2),
      };
    }
  }
  return positions;
}

/**
 * Tidy top-down tree layout for "Дерево" mode. Only the first occurrence of a
 * node becomes a card; repeated rows stay in the rail as crosslinks, so the
 * canvas never draws the same fact twice.
 */
export function computeTreeLayout(
  tree: ExplorationTree,
  nodes: readonly ViewNode[],
): { positions: Record<string, Position>; minY: number } {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const firstRow = new Map<string, { parentId: string | null; depth: number }>();
  for (const row of tree.rows) {
    if (row.cycle || row.crosslink) continue;
    if (!firstRow.has(row.node.id)) {
      firstRow.set(row.node.id, { parentId: row.parentId, depth: row.depth });
    }
  }
  const children = new Map<string, string[]>();
  for (const id of firstRow.keys()) children.set(id, []);
  const roots: string[] = [];
  for (const [id, row] of firstRow) {
    if (row.parentId && firstRow.has(row.parentId)) {
      children.get(row.parentId)?.push(id);
    } else {
      roots.push(id);
    }
  }
  for (const list of children.values()) list.sort();
  roots.sort();

  const widths = new Map<string, number>();
  const measure = (id: string, path: Set<string>): number => {
    if (path.has(id)) return 1;
    const known = widths.get(id);
    if (known !== undefined) return known;
    const nextPath = new Set(path).add(id);
    const kids = (children.get(id) ?? []).filter((kid) => !nextPath.has(kid));
    const width = kids.length
      ? kids.reduce((sum, kid) => sum + measure(kid, nextPath), 0)
      : 1;
    widths.set(id, width);
    return width;
  };
  for (const root of roots) measure(root, new Set());

  const positions: Record<string, Position> = {};
  const place = (id: string, cursor: number, depth: number, path: Set<string>): number => {
    if (path.has(id)) {
      positions[id] = {
        x: cursor * (NODE_WIDTH + TREE_GAP_X),
        y: depth * TREE_LEVEL_HEIGHT,
      };
      return cursor + 1;
    }
    const nextPath = new Set(path).add(id);
    const kids = (children.get(id) ?? []).filter((kid) => !nextPath.has(kid));
    const width = kids.length
      ? kids.reduce((sum, kid) => sum + (widths.get(kid) ?? 1), 0)
      : 1;
    const centre = cursor + width / 2;
    positions[id] = {
      x: Math.round(centre * (NODE_WIDTH + TREE_GAP_X) - NODE_WIDTH / 2),
      y: depth * TREE_LEVEL_HEIGHT,
    };
    let next = cursor;
    for (const kid of kids) {
      next = place(kid, next, depth + 1, nextPath);
    }
    // A leaf still occupies one horizontal slot. Without this the second leaf
    // sibling is placed at the same x as the first one.
    return Math.max(next, cursor + width);
  };

  let cursor = 0;
  for (const root of roots) cursor = place(root, cursor, 0, new Set());

  // Unknown tree nodes (only reachable through a cycle) still get a slot.
  for (const id of firstRow.keys()) {
    if (!positions[id] && byId.has(id)) {
      positions[id] = {
        x: cursor * (NODE_WIDTH + TREE_GAP_X),
        y: 0,
      };
      cursor += 1;
    }
  }

  return { positions, minY: 0 };
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