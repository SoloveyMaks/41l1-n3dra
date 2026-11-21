import type { ExecutionStatus, NodeRunRecord, NodeRunStatus } from '../types';

// Minimal event shape shared by the live broker and the historical replay.
// Live events carry `nodeId` and no timestamp; replay carries `node_id` and a
// server timestamp. App normalizes both before they reach this reducer.
export interface RunTelemetryEvent {
  seq: number;
  timestamp?: string;
  type: string;
  nodeId?: string;
  payload?: Record<string, any>;
}

export type EdgeMotionState = 'idle' | 'flowing' | 'traversed';

export interface NodeTelemetry {
  status: NodeRunStatus;
  durationMs?: number;
  startedAtMs?: number;
  error?: string;
  lastSeq: number;
  // The last recorded status is still active, but the run summary is already
  // terminal, so the node cannot be executing now. Consumers keep the recorded
  // status (it is a fact) but must not animate it or call the stage "running".
  stalled: boolean;
}

export interface EdgeTelemetry {
  id: string;
  source?: string;
  target?: string;
  state: EdgeMotionState;
  // True only for edge_data that arrived live in this subscription. Replayed
  // history restores the highlighted state without re-running the animation.
  pulse: boolean;
  dataSeq: number;
}

export interface RunTelemetry {
  nodes: Record<string, NodeTelemetry>;
  edges: Record<string, EdgeTelemetry>;
  lastSeq: number;
  live: boolean;
}

const NODE_STATUSES: Readonly<Record<string, true>> = {
  pending: true,
  running: true,
  retrying: true,
  succeeded: true,
  failed: true,
  skipped: true,
  cancelled: true,
};

const TERMINAL_NODE_STATUSES: Readonly<Record<string, true>> = {
  succeeded: true,
  failed: true,
  skipped: true,
  cancelled: true,
};

const ACTIVE_RUN_STATUSES: Readonly<Record<string, true>> = {
  queued: true,
  running: true,
};

const TERMINAL_RUN_STATUSES: Readonly<Record<string, true>> = {
  succeeded: true,
  failed: true,
  cancelled: true,
  interrupted: true,
};

function asString(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined;
}

function asNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

function timeMs(value: unknown): number | undefined {
  if (typeof value !== 'string' || !value) return undefined;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

export function buildRunTelemetry(input: {
  events: RunTelemetryEvent[];
  nodeRuns: NodeRunRecord[];
  runStatus?: ExecutionStatus;
  // Seq present when the subscription opened. Edge data at or below it is
  // restored history, not motion to replay. null = baseline not known yet, so
  // nothing is allowed to animate until it is.
  baselineSeq: number | null;
  isRunning: boolean;
}): RunTelemetry {
  const { events, nodeRuns, runStatus, baselineSeq, isRunning } = input;
  const nodes: Record<string, NodeTelemetry> = {};
  const edges: Record<string, EdgeTelemetry> = {};

  // API node runs are the fallback source: they survive reconnects and give
  // duration/error fields the live broker may have dropped. A terminal record
  // is DB truth and must not be rolled back by an older replayed event.
  const apiTerminal = new Set<string>();
  for (const record of nodeRuns) {
    const status = NODE_STATUSES[record.status] ? record.status : 'pending';
    nodes[record.node_id] = {
      status,
      durationMs: record.duration_ms > 0 ? record.duration_ms : undefined,
      startedAtMs: timeMs(record.started_at),
      error: record.error_message || undefined,
      lastSeq: 0,
      stalled: false,
    };
    if (TERMINAL_NODE_STATUSES[status]) apiTerminal.add(record.node_id);
  }

  let lastSeq = 0;
  let status = runStatus;
  // The run summary is authoritative. A terminal summary must not be rolled
  // back by a stale replay that still says running, and it must not leave old
  // running nodes or edge pulses animating after the run is over.
  const apiTerminalRun = runStatus !== undefined && Boolean(TERMINAL_RUN_STATUSES[runStatus]);
  const ordered = [...events].sort((a, b) => a.seq - b.seq);

  for (const event of ordered) {
    if (!Number.isFinite(event.seq) || event.seq <= 0) continue;
    if (event.seq > lastSeq) lastSeq = event.seq;

    if (event.type === 'run_status') {
      if (apiTerminalRun) continue;
      const next = asString(event.payload?.status);
      if (next) status = next as ExecutionStatus;
      continue;
    }

    if (event.type === 'node_status') {
      const nodeId = asString(event.payload?.nodeId) ?? asString(event.nodeId);
      const nextStatus = event.payload?.status;
      if (!nodeId || typeof nextStatus !== 'string' || !NODE_STATUSES[nextStatus]) continue;
      if (apiTerminal.has(nodeId)) continue;
      // Replay may be incomplete and arrive after the terminal summary; it
      // cannot restart a clock that the run itself already stopped.
      if (apiTerminalRun && !TERMINAL_NODE_STATUSES[nextStatus]) continue;

      const previous = nodes[nodeId];
      if (previous && event.seq < previous.lastSeq) continue;
      const durationMs = asNumber(event.payload?.duration_ms) ?? asNumber(event.payload?.durationMs);
      const eventAt = timeMs(event.timestamp);
      nodes[nodeId] = {
        status: nextStatus as NodeRunStatus,
        durationMs:
          durationMs !== undefined
            ? durationMs
            : nextStatus === 'running'
              ? undefined
              : previous?.durationMs,
        startedAtMs: nextStatus === 'running' ? eventAt ?? previous?.startedAtMs : previous?.startedAtMs,
        error: nextStatus === 'failed' ? asString(event.payload?.error) : undefined,
        lastSeq: event.seq,
        stalled: false,
      };
      continue;
    }

    if (event.type === 'edge_data') {
      const edgeId = asString(event.payload?.edgeId);
      if (!edgeId) continue;
      const previous = edges[edgeId];
      if (previous && event.seq < previous.dataSeq) continue;
      edges[edgeId] = {
        id: edgeId,
        source: asString(event.payload?.source) ?? previous?.source,
        target: asString(event.payload?.target) ?? previous?.target,
        state: 'traversed',
        pulse: false,
        dataSeq: event.seq,
      };
    }
  }

  // A terminal run cannot still be executing anything. Keep the last recorded
  // status as the fact, but flag every non-terminal node as stalled so canvas
  // spinners, live dots, clocks and stage cards stop with the run.
  for (const node of Object.values(nodes)) {
    node.stalled = apiTerminalRun && !TERMINAL_NODE_STATUSES[node.status];
  }

  // A run is live when its last known status is active; before the first run
  // status arrives, App's trigger flag is the only signal. A terminal summary
  // always wins, even when the closing SSE was lost and replay is incomplete.
  const live = apiTerminalRun
    ? false
    : status !== undefined
      ? Boolean(ACTIVE_RUN_STATUSES[status])
      : isRunning;

  for (const edge of Object.values(edges)) {
    const target = edge.target ? nodes[edge.target] : undefined;
    const targetSettled = target ? TERMINAL_NODE_STATUSES[target.status] : false;
    const fresh = baselineSeq !== null && edge.dataSeq > baselineSeq;
    if (live && fresh && !targetSettled) {
      edge.state = 'flowing';
      edge.pulse = true;
    }
  }

  return { nodes, edges, lastSeq, live };
}