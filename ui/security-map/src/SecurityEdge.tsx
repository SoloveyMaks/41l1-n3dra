import { BaseEdge, EdgeLabelRenderer, type EdgeProps } from "@xyflow/react";
import type { EdgeRoute, RoutePoint } from "./edgeRouting";

const distance = (a: RoutePoint, b: RoutePoint): number =>
  Math.hypot(b.x - a.x, b.y - a.y);

function roundedPath(points: RoutePoint[]): string {
  if (points.length < 2) return "";
  let path = `M ${points[0].x} ${points[0].y}`;
  for (let index = 1; index < points.length - 1; index += 1) {
    const previous = points[index - 1];
    const current = points[index];
    const next = points[index + 1];
    const inLength = distance(previous, current);
    const outLength = distance(current, next);
    if (!inLength || !outLength) {
      path += ` L ${current.x} ${current.y}`;
      continue;
    }
    const radius = Math.min(10, inLength / 2, outLength / 2);
    const before = {
      x: current.x + ((previous.x - current.x) / inLength) * radius,
      y: current.y + ((previous.y - current.y) / inLength) * radius,
    };
    const after = {
      x: current.x + ((next.x - current.x) / outLength) * radius,
      y: current.y + ((next.y - current.y) / outLength) * radius,
    };
    path += ` L ${before.x} ${before.y} Q ${current.x} ${current.y} ${after.x} ${after.y}`;
  }
  const last = points[points.length - 1];
  return `${path} L ${last.x} ${last.y}`;
}

export type SecurityEdgeData = Partial<EdgeRoute> & {
  evidenceCount?: number;
  resolution?: string;
};

export function SecurityEdge({
  markerEnd,
  style,
  label,
  data,
  selected,
}: EdgeProps) {
  const route = data as SecurityEdgeData | undefined;
  const points = route?.points ?? [];
  const fallbackMid = points.length
    ? points[Math.floor(points.length / 2)]
    : { x: 0, y: 0 };
  const labelPoint = route?.labelPoint ?? fallbackMid;
  const path = roundedPath(points);
  if (!path) return null;
  const evidenceCount = route?.evidenceCount ?? 0;
  const unresolved = route?.resolution ? route.resolution !== "resolved" : false;

  return (
    <>
      <BaseEdge
        path={path}
        markerEnd={markerEnd}
        className={`sm-edge-path${selected ? " is-selected" : ""}`}
        style={style}
      />
      <path
        d={path}
        fill="none"
        stroke="transparent"
        strokeWidth={18}
        className="sm-edge-hit"
      />
      {label ? (
        <EdgeLabelRenderer>
          <div
            className={`sm-edge-label${selected ? " is-selected" : ""}${
              unresolved ? " is-unresolved" : ""
            }${evidenceCount ? "" : " is-unevidenced"}`}
            style={{
              transform: `translate(-50%, -50%) translate(${labelPoint.x}px, ${labelPoint.y}px)`,
            }}
            title={
              evidenceCount
                ? `Основание связи: ${evidenceCount} сохранённых фрагмента`
                : "У связи нет сохранённого фрагмента-основания"
            }
          >
            <span className="sm-edge-label-text">{label}</span>
            <span className="sm-edge-evidence">
              {evidenceCount ? `${evidenceCount} док.` : "нет док."}
            </span>
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
}

SecurityEdge.displayName = "SecurityEdge";