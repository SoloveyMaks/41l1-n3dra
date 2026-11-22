import type { EdgeTelemetry, RunTelemetry } from './runTelemetry';

/**
 * Motion the canvas may show for one edge of the current run.
 *
 * The only source of truth is `edge_data` the engine emitted for this run
 * (collected into `RunTelemetry.edges` by `buildRunTelemetry`): an edge that
 * never carried data — a skipped condition branch — stays `idle` and never
 * gets a pulse, on live, replay and reload alike.
 */
export interface ActiveEdgeState {
  flow: EdgeTelemetry['state'];
  pulse: boolean;
}

export const IDLE_EDGE_STATE: ActiveEdgeState = { flow: 'idle', pulse: false };

/** The parts of a canvas edge needed to match it against recorded edge data. */
export interface EdgeRef {
  id: string;
  source: string;
  target: string;
}

const endpointKey = (source: string, target: string): string => `${source}\u0000${target}`;

/**
 * Select the motion state for every canvas edge.
 *
 * Match order: edge id first (the normal path — the canvas graph is bound to
 * the run revision, so ids agree), then a unique source/target pair for the
 * case where the recorded id differs. Parallel edges sharing endpoints make
 * the endpoint match ambiguous, so they fall back to `idle` instead of
 * guessing which one carried data. A pulse additionally requires the live
 * `flowing` state: replayed history restores the highlight without motion.
 */
export function selectActiveEdges(
  edges: readonly EdgeRef[],
  telemetry: Pick<RunTelemetry, 'edges'>,
): Record<string, ActiveEdgeState> {
  const byId = new Map<string, EdgeTelemetry>();
  const byEndpoints = new Map<string, EdgeTelemetry | null>();
  for (const record of Object.values(telemetry.edges)) {
    if (record.id) byId.set(record.id, record);
    if (!record.source || !record.target) continue;
    const key = endpointKey(record.source, record.target);
    byEndpoints.set(key, byEndpoints.has(key) ? null : record);
  }

  const result: Record<string, ActiveEdgeState> = {};
  for (const edge of edges) {
    const record = byId.get(edge.id) ?? byEndpoints.get(endpointKey(edge.source, edge.target)) ?? null;
    result[edge.id] = record
      ? { flow: record.state, pulse: record.pulse && record.state === 'flowing' }
      : IDLE_EDGE_STATE;
  }
  return result;
}
