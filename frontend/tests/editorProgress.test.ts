// Deterministic check of editor run-progress selection: no browser, no network.
// Run with: node --test tests/editorProgress.test.ts
import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  buildEditorRunProgress,
  formatEditorRunProgress,
  selectActiveRunNode,
} from '../src/execution/editorProgress.ts';
import { buildRunTelemetry, type RunTelemetry, type RunTelemetryEvent } from '../src/execution/runTelemetry.ts';
import type { NodeRunRecord } from '../src/types/index.ts';

const plan = [
  { id: 'trigger', kind: 'manual_trigger' },
  { id: 'secret', kind: 'secret_scan', title: 'Секреты' },
  { id: 'merge', kind: 'merge_findings' },
  { id: 'gate', kind: 'policy_gate' },
];

const EMPTY_TELEMETRY: RunTelemetry = { nodes: {}, edges: {}, lastSeq: 0, live: false };

const nodeEvent = (seq: number, nodeId: string, status: string): RunTelemetryEvent => ({
  seq,
  type: 'node_status',
  payload: { nodeId, status },
});

const record = (nodeId: string, kind: string, status: string): NodeRunRecord => ({
  id: `rec_${nodeId}`,
  run_id: 'run_1',
  node_id: nodeId,
  kind: kind as NodeRunRecord['kind'],
  status: status as NodeRunRecord['status'],
  duration_ms: 10,
  input_data_masked: {},
  output_data_masked: {},
});

test('no run on this canvas: nothing is shown', () => {
  const progress = buildEditorRunProgress({
    nodes: plan,
    telemetry: EMPTY_TELEMETRY,
    isRunning: false,
  });
  assert.equal(progress.visible, false);
  assert.equal(progress.phase, 'idle');
  assert.equal(progress.activeNodeId, null);
  assert.equal(formatEditorRunProgress(progress), null);
});

test('running: current block, finished count and label', () => {
  const telemetry = buildRunTelemetry({
    events: [nodeEvent(1, 'trigger', 'succeeded'), nodeEvent(2, 'secret', 'running')],
    nodeRuns: [],
    runStatus: 'running',
    baselineSeq: 0,
    isRunning: true,
  });
  const progress = buildEditorRunProgress({ nodes: plan, telemetry, runStatus: 'running', isRunning: true });
  assert.equal(progress.visible, true);
  assert.equal(progress.phase, 'running');
  assert.equal(progress.total, 4);
  assert.equal(progress.finished, 1);
  assert.equal(progress.percent, 25);
  assert.equal(progress.activeNodeId, 'secret');
  assert.equal(progress.activeNodeTitle, 'Секреты');
  assert.equal(formatEditorRunProgress(progress), '1/4 · выполняется Секреты');
});

test('parallel branches: draft order picks the current block, event order does not matter', () => {
  const firstOrder = buildRunTelemetry({
    events: [nodeEvent(1, 'secret', 'running'), nodeEvent(2, 'merge', 'running')],
    nodeRuns: [],
    runStatus: 'running',
    baselineSeq: 0,
    isRunning: true,
  });
  const secondOrder = buildRunTelemetry({
    events: [nodeEvent(1, 'merge', 'running'), nodeEvent(2, 'secret', 'running')],
    nodeRuns: [],
    runStatus: 'running',
    baselineSeq: 0,
    isRunning: true,
  });
  assert.equal(selectActiveRunNode(plan, firstOrder), 'secret');
  assert.equal(selectActiveRunNode(plan, secondOrder), 'secret');
});

test('no running block yet: retrying is the fallback', () => {
  const telemetry = buildRunTelemetry({
    events: [nodeEvent(1, 'trigger', 'succeeded'), nodeEvent(2, 'secret', 'retrying')],
    nodeRuns: [],
    runStatus: 'running',
    baselineSeq: 0,
    isRunning: true,
  });
  const progress = buildEditorRunProgress({ nodes: plan, telemetry, runStatus: 'running', isRunning: true });
  assert.equal(progress.activeNodeId, 'secret');
  assert.equal(formatEditorRunProgress(progress), '1/4 · выполняется Секреты');
});

test('queued run with no block started: label reports the queue, not a block', () => {
  const telemetry = buildRunTelemetry({
    events: [{ seq: 1, type: 'run_status', payload: { status: 'queued' } }],
    nodeRuns: [],
    runStatus: 'queued',
    baselineSeq: 0,
    isRunning: true,
  });
  const progress = buildEditorRunProgress({ nodes: plan, telemetry, runStatus: 'queued', isRunning: true });
  assert.equal(progress.phase, 'running');
  assert.equal(progress.activeNodeId, null);
  assert.equal(formatEditorRunProgress(progress), '0/4 · в очереди');
});

test('terminal run: a record stuck on running is history, not the current block', () => {
  const telemetry = buildRunTelemetry({
    events: [],
    nodeRuns: [record('trigger', 'manual_trigger', 'succeeded'), record('gate', 'policy_gate', 'running')],
    runStatus: 'failed',
    baselineSeq: 0,
    isRunning: false,
  });
  const progress = buildEditorRunProgress({ nodes: plan, telemetry, runStatus: 'failed', isRunning: false });
  assert.equal(progress.phase, 'done');
  assert.equal(progress.verdict, 'failed');
  assert.equal(progress.activeNodeId, null);
  assert.equal(progress.finished, 1);
  assert.equal(formatEditorRunProgress(progress), 'Завершено · fail');
});

test('failed and cancelled blocks are counted apart from finished', () => {
  const telemetry = buildRunTelemetry({
    events: [
      nodeEvent(1, 'trigger', 'succeeded'),
      nodeEvent(2, 'secret', 'failed'),
      nodeEvent(3, 'merge', 'cancelled'),
    ],
    nodeRuns: [],
    runStatus: 'cancelled',
    baselineSeq: 0,
    isRunning: false,
  });
  const progress = buildEditorRunProgress({ nodes: plan, telemetry, runStatus: 'cancelled', isRunning: false });
  assert.equal(progress.finished, 3);
  assert.equal(progress.failed, 1);
  assert.equal(progress.cancelled, 1);
  assert.equal(progress.percent, 75);
  assert.equal(formatEditorRunProgress(progress), 'Завершено · cancelled');
});

test('a terminal summary on a foreign graph never shows progress for this draft', () => {
  const progress = buildEditorRunProgress({
    nodes: plan,
    telemetry: EMPTY_TELEMETRY,
    runStatus: 'succeeded',
    isRunning: false,
  });
  assert.equal(progress.visible, false);
  assert.equal(formatEditorRunProgress(progress), null);
});

test('eight-block plan: 3/8 while the fourth block runs', () => {
  const blocks = Array.from({ length: 8 }, (_, index) => ({ id: `n${index}`, kind: 'custom_check' }));
  const events = [
    nodeEvent(1, 'n0', 'succeeded'),
    nodeEvent(2, 'n1', 'succeeded'),
    nodeEvent(3, 'n2', 'succeeded'),
    nodeEvent(4, 'n3', 'running'),
  ];
  const telemetry = buildRunTelemetry({
    events,
    nodeRuns: [],
    runStatus: 'running',
    baselineSeq: 0,
    isRunning: true,
  });
  const progress = buildEditorRunProgress({ nodes: blocks, telemetry, runStatus: 'running', isRunning: true });
  assert.equal(progress.finished, 3);
  assert.equal(progress.percent, 38);
  assert.equal(progress.activeNodeId, 'n3');
  assert.equal(progress.activeNodeTitle, 'custom_check');
  assert.equal(formatEditorRunProgress(progress), '3/8 · выполняется custom_check');
});
