import type { Node } from "@xyflow/react";
import type { ViewEdge } from "./types";
import { NODE_WIDTH, estimateNodeHeight } from "./layout";

export type RoutePoint = { x: number; y: number };
export type EdgeRoute = { points: RoutePoint[]; labelPoint: RoutePoint };

type Box = { x: number; y: number; width: number; height: number };

const boxFor = (node: Node): Box => {
  const data = node.data as { kind?: string } | undefined;
  return {
    x: node.position.x,
    y: node.position.y,
    width: node.measured?.width ?? NODE_WIDTH,
    height:
      node.measured?.height ??
      estimateNodeHeight({
        id: node.id,
        kind: data?.kind ?? "data",
        label: typeof node.data?.label === "string" ? node.data.label : node.id,
        symbolKey: "",
        evidenceRefs: [],
        producerVersion: "",
      }),
  };
};

const uniqueSorted = (values: number[]): number[] =>
  [...new Set(values.map((value) => Math.round(value * 100) / 100))].sort((a, b) => a - b);

/**
 * Orthogonal routes that never enter a node card.
 *
 * Adjacent ranks use the corridor between columns. Edges that skip ranks or go
 * backwards use a lane above/below the whole map. The source and target
 * verticals live in the gaps beside the cards, so crosslinks stay factual and
 * visible without cutting through unrelated nodes.
 */
export function buildEdgeRoutes(
  nodes: readonly Node[],
  edges: readonly ViewEdge[],
): Map<string, EdgeRoute> {
  const boxes = new Map<string, Box>();
  for (const node of nodes) boxes.set(node.id, boxFor(node));
  if (!boxes.size) return new Map();

  const all = [...boxes.values()];
  const minY = Math.min(...all.map((box) => box.y));
  const maxY = Math.max(...all.map((box) => box.y + box.height));
  const maxX = Math.max(...all.map((box) => box.x + box.width));
  const columns = uniqueSorted(all.map((box) => box.x));
  const columnOf = (box: Box): number => {
    const found = columns.findIndex((value) => Math.abs(value - box.x) < 1);
    return found === -1 ? 0 : found;
  };

  const routes = new Map<string, EdgeRoute>();
  edges.forEach((edge, index) => {
    const source = boxes.get(edge.fromId);
    const target = boxes.get(edge.toId);
    if (!source || !target) return;

    const sx = source.x + source.width;
    const sy = source.y + source.height / 2;
    const tx = target.x;
    const ty = target.y + target.height / 2;
    const sourceColumn = columnOf(source);
    const targetColumn = columnOf(target);
    const lane = index % 7;
    let points: RoutePoint[];
    let labelPoint: RoutePoint;

    if (targetColumn > sourceColumn + 1) {
      // Long forward link: route above all cards so it cannot cut a column.
      const laneY = minY - 34 - lane * 14;
      points = [
        { x: sx, y: sy },
        { x: sx + 14, y: sy },
        { x: sx + 14, y: laneY },
        { x: tx - 14, y: laneY },
        { x: tx - 14, y: ty },
        { x: tx, y: ty },
      ];
      labelPoint = { x: (sx + tx) / 2, y: laneY };
    } else if (targetColumn < sourceColumn || targetColumn === sourceColumn) {
      // Back edge, cycle or same-column crosslink: route below/right of cards.
      const sameColumn = targetColumn === sourceColumn;
      const laneX = sameColumn ? maxX + 30 + lane * 14 : null;
      if (laneX !== null) {
        points = [
          { x: sx, y: sy },
          { x: laneX, y: sy },
          { x: laneX, y: ty },
          { x: tx, y: ty },
        ];
        labelPoint = { x: laneX, y: (sy + ty) / 2 };
      } else {
        const laneY = maxY + 34 + lane * 14;
        points = [
          { x: sx, y: sy },
          { x: sx + 14, y: sy },
          { x: sx + 14, y: laneY },
          { x: tx - 14, y: laneY },
          { x: tx - 14, y: ty },
          { x: tx, y: ty },
        ];
        labelPoint = { x: (sx + tx) / 2, y: laneY };
      }
    } else {
      // Adjacent ranks: a short orthogonal hop through the column gap.
      const midX = (sx + tx) / 2;
      points = [
        { x: sx, y: sy },
        { x: midX, y: sy },
        { x: midX, y: ty },
        { x: tx, y: ty },
      ];
      labelPoint = { x: midX, y: (sy + ty) / 2 };
    }

    routes.set(edge.id, { points, labelPoint });
  });

  return routes;
}