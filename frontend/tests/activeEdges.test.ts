// Deterministic check of active-edge selection: no browser, no network.
// Run with: node --test tests/activeEdges.test.ts
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { IDLE_EDGE_STATE, selectActiveEdges } from '../src/execution/activeEdges.ts';
import { buildRunTelemetry, type RunTelemetryEvent } from '../src/execution/runTelemetry.ts';

const edgeRefs = [
  { id: 'e_true', source: 'cond', target: 'ok', sourcePort: 'true_branch' },
  { id: 'e_false', source: 'cond', target: 'fail', sourcePort: 'false_branch' },
];

const edgeDataEvent = (seq: number, edgeId: string, source: string, target: string): RunTelemetryEvent => ({
  seq,
  type: 'edge_data',
  payload: { edgeId, source, target, data: { decision: true } },
});

test('live run: only the branch that carried edge_data animates', () => {
  const telemetry = buildRunTelemetry({
    events: [
      { seq: 1, type: 'node_status', payload: { nodeId: 'cond', status: 'succeeded' } },
      edgeDataEvent(2, 'e_true', 'cond', 'ok'),
    ],
    nodeRuns: [],
    runStatus: 'running',
    baselineSeq: 0,
    isRunning: true,
  });

  const active = selectActiveEdges(edgeRefs, telemetry);
  assert.equal(active.e_true.flow, 'flowing');
  assert.equal(active.e_true.pulse, true);
  // The skipped branch received no data: it must not look like it executed.
  assert.deepEqual(active.e_false, IDLE_EDGE_STATE);
});

test('replay: restored history keeps the highlight but never pulses', () => {
  const telemetry = buildRunTelemetry({
    events: [edgeDataEvent(5, 'e_true', 'cond', 'ok')],
    nodeRuns: [],
    runStatus: 'succeeded',
    baselineSeq: 10,
    isRunning: false,
  });

  const active = selectActiveEdges(edgeRefs, telemetry);
  assert.equal(active.e_true.flow, 'traversed');
  assert.equal(active.e_true.pulse, false);
  assert.deepEqual(active.e_false, IDLE_EDGE_STATE);
});

test('no edge_data at all: every edge stays idle', () => {
  const telemetry = buildRunTelemetry({
    events: [{ seq: 1, type: 'node_status', payload: { nodeId: 'cond', status: 'succeeded' } }],
    nodeRuns: [],
    runStatus: 'running',
    baselineSeq: 0,
    isRunning: true,
  });

  const active = selectActiveEdges(edgeRefs, telemetry);
  assert.deepEqual(active.e_true, IDLE_EDGE_STATE);
  assert.deepEqual(active.e_false, IDLE_EDGE_STATE);
});

// Regression: both condition branches feed different inputs of ONE Branch
// Merge, so they share the source/target pair while only e_true carried data.
// A pair-based match would light up the skipped branch too — matching is by
// edge id only.
test('condition branches into one Branch Merge: shared endpoints do not leak motion', () => {
  const mergeEdges = [
    { id: 'e_true', source: 'cond', target: 'merge', sourcePort: 'true_branch', targetPort: 'branch_a' },
    { id: 'e_false', source: 'cond', target: 'merge', sourcePort: 'false_branch', targetPort: 'branch_b' },
  ];
  const telemetry = buildRunTelemetry({
    events: [edgeDataEvent(2, 'e_true', 'cond', 'merge')],
    nodeRuns: [],
    runStatus: 'running',
    baselineSeq: 0,
    isRunning: true,
  });

  const active = selectActiveEdges(mergeEdges, telemetry);
  assert.equal(active.e_true.flow, 'flowing');
  assert.deepEqual(active.e_false, IDLE_EDGE_STATE);
});

test('unknown edge id stays idle even when recorded edges exist', () => {
  const telemetry = buildRunTelemetry({
    events: [edgeDataEvent(2, 'e_true', 'cond', 'ok')],
    nodeRuns: [],
    runStatus: 'running',
    baselineSeq: 0,
    isRunning: true,
  });

  const active = selectActiveEdges([{ id: 'e_renamed' }], telemetry);
  assert.deepEqual(active.e_renamed, IDLE_EDGE_STATE);
});

test('a recorded pulse is dropped unless the edge is flowing now', () => {
  const telemetry = {
    edges: {
      e_true: { id: 'e_true', source: 'cond', target: 'ok', state: 'traversed' as const, pulse: true, dataSeq: 4 },
    },
  };

  const active = selectActiveEdges(edgeRefs, telemetry);
  assert.equal(active.e_true.flow, 'traversed');
  assert.equal(active.e_true.pulse, false);
});
