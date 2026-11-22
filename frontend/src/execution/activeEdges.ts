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
}

/**
 * Select the motion state for every canvas edge.
 *
 * Matching is by edge id only. The canvas graph is bound to the run revision
 * (`sameRunGraph`), so ids agree with the recorded run — and an endpoint
 * fallback must not exist: two condition branches can share the same
 * source/target pair (different ports of one Branch Merge) while only one of
 * them carried data, and a pair match would light up the skipped branch too.
 * A pulse additionally requires the live `flowing` state: replayed history
 * restores the highlight without motion.
 */
export function selectActiveEdges(
  edges: readonly EdgeRef[],
  telemetry: Pick<RunTelemetry, 'edges'>,
): Record<string, ActiveEdgeState> {
  const result: Record<string, ActiveEdgeState> = {};
  for (const edge of edges) {
    const record: EdgeTelemetry | undefined = telemetry.edges[edge.id];
    result[edge.id] = record
      ? { flow: record.state, pulse: record.pulse && record.state === 'flowing' }
      : IDLE_EDGE_STATE;
  }
  return result;
}
