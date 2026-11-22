import type { ExecutionStatus } from '../types';
import type { RunTelemetry } from './runTelemetry';

// Pure selection of the editor's run progress: which block is executing right
// now and how much of the plan is done. No React, no browser, no network —
// deterministic for a fixed telemetry snapshot, which is what the tests pin.

export interface EditorProgressNode {
  id: string;
  kind: string;
  title?: string;
}

export type EditorRunPhase = 'idle' | 'running' | 'done';

export interface EditorRunProgress {
  // False when the telemetry carries nothing about this draft (no run yet, or
  // the run belongs to a different graph): the bar must not pretend otherwise.
  visible: boolean;
  phase: EditorRunPhase;
  total: number;
  finished: number;
  failed: number;
  cancelled: number;
  percent: number;
  activeNodeId: string | null;
  activeNodeTitle: string | null;
  verdict: ExecutionStatus | null;
}

const TERMINAL_NODE_STATUSES: Readonly<Record<string, true>> = {
  succeeded: true,
  failed: true,
  skipped: true,
  cancelled: true,
};

const TERMINAL_RUN_STATUSES: Readonly<Record<string, true>> = {
  succeeded: true,
  failed: true,
  cancelled: true,
  interrupted: true,
};

const RUN_STATUS_WORD: Readonly<Record<string, string>> = {
  succeeded: 'ok',
  failed: 'fail',
  cancelled: 'cancelled',
  interrupted: 'interrupted',
};

// The block the run is working on right now: first `running` in draft order,
// falling back to `retrying`. A record left `running` by a run that is already
// over is history (`stalled`), never the current block. Deterministic: draft
// order, no timestamps, no map iteration.
export function selectActiveRunNode(
  nodes: readonly EditorProgressNode[],
  telemetry: RunTelemetry,
): string | null {
  if (!telemetry.live) return null;
  let retrying: string | null = null;
  for (const node of nodes) {
    const info = telemetry.nodes[node.id];
    if (!info || info.stalled) continue;
    if (info.status === 'running') return node.id;
    if (info.status === 'retrying' && retrying === null) retrying = node.id;
  }
  return retrying;
}

export function buildEditorRunProgress(input: {
  nodes: readonly EditorProgressNode[];
  telemetry: RunTelemetry;
  runStatus?: ExecutionStatus;
  isRunning: boolean;
}): EditorRunProgress {
  const { nodes, telemetry, runStatus, isRunning } = input;
  const total = nodes.length;
  let finished = 0;
  let failed = 0;
  let cancelled = 0;
  for (const node of nodes) {
    const info = telemetry.nodes[node.id];
    if (!info) continue;
    if (info.status === 'failed') failed += 1;
    if (info.status === 'cancelled') cancelled += 1;
    if (TERMINAL_NODE_STATUSES[info.status]) finished += 1;
  }

  const verdict = runStatus && TERMINAL_RUN_STATUSES[runStatus] ? runStatus : null;
  const activeNodeId = selectActiveRunNode(nodes, telemetry);
  const activeNode = activeNodeId ? nodes.find((node) => node.id === activeNodeId) : undefined;
  const activeNodeTitle = activeNode ? activeNode.title?.trim() || activeNode.kind : null;
  const phase: EditorRunPhase = verdict
    ? 'done'
    : telemetry.live || isRunning
      ? 'running'
      : 'idle';
  const visible = telemetry.live || Object.keys(telemetry.nodes).length > 0;

  return {
    visible,
    phase,
    total,
    finished,
    failed,
    cancelled,
    percent: total > 0 ? Math.round((finished / total) * 100) : 0,
    activeNodeId,
    activeNodeTitle,
    verdict,
  };
}

// «3/8 · выполняется merge_findings» while the run moves, «Завершено · fail»
// once it settles. null = nothing worth showing on this canvas.
export function formatEditorRunProgress(progress: EditorRunProgress): string | null {
  if (!progress.visible) return null;
  const done = `${progress.finished}/${progress.total}`;
  if (progress.phase === 'done') {
    const word = progress.verdict ? RUN_STATUS_WORD[progress.verdict] ?? progress.verdict : '—';
    return `Завершено · ${word}`;
  }
  if (progress.phase === 'running') {
    return progress.activeNodeTitle
      ? `${done} · выполняется ${progress.activeNodeTitle}`
      : `${done} · в очереди`;
  }
  return `${done} · ожидание`;
}
