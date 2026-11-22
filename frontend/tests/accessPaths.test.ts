import assert from 'node:assert/strict';
import { test } from 'node:test';

import { enumerateAccessPaths } from '../../ui/security-map/src/paths.ts';
import type { MapViewModel, ViewEdge, ViewEvidence, ViewFinding, ViewNode } from '../../ui/security-map/src/types.ts';

const scope = { workspaceId: 'w', runId: 'r', snapshotId: 's' };
const evidence = (id: string, path: string): ViewEvidence => ({
  id, location: { path, line: 1, column: 1, endLine: 1, endColumn: 1, byteStart: 0, byteEnd: 1 },
  sourceHash: 'h', producerVersion: 'p', redactedSnippet: '***',
});
const node = (id: string, kind: string, label: string, refs: string[] = []): ViewNode => ({
  id, kind, label, symbolKey: `${kind}:${id}`, evidenceRefs: refs, producerVersion: 'p',
});
const edge = (id: string, fromId: string, toId: string, kind: string): ViewEdge => ({
  id, fromId, toId, kind, evidenceRefs: [], resolution: 'resolved', resolutionReason: '',
  producerVersion: 'p', basis: 'source', policyRef: null,
});

function model(edges: ViewEdge[]): MapViewModel {
  return {
    scope, revision: 'r1', provenance: 'analyzer', policies: [], diagnostics: [],
    coverage: { status: 'partial', summary: '', warnings: [] },
    nodes: [
      node('a', 'actor', 'Аноним'),
      node('r1', 'route', 'GET /one', ['ev1']),
      node('r2', 'route', 'GET /two'),
      node('f1', 'function', 'one', ['ev1']),
      node('f2', 'function', 'two'),
      node('g1', 'guard', '@PreAuthorize'),
      node('d1', 'data', 'table'),
    ],
    edges,
    findings: [{
      id: 'x', label: 'Проблема', status: 'confirmed', severity: 'critical',
      confidence: 'high', reasons: [], evidenceRefs: ['ev1'], nodeIds: ['f1'],
    } as ViewFinding],
    evidence: [evidence('ev1', 'src/One.kt')],
  };
}

test('all simple paths are listed, including guard-only branches', () => {
  const result = enumerateAccessPaths(model([
    edge('e1', 'a', 'r1', 'matches'),
    edge('e2', 'a', 'r2', 'matches'),
    edge('e3', 'r1', 'f1', 'routes_to'),
    edge('e4', 'r1', 'g1', 'requires'),
    edge('e5', 'r2', 'f2', 'routes_to'),
    edge('e6', 'f2', 'd1', 'reads'),
  ]));
  assert.equal(result.total, 3);
  const chains = result.paths.map((path) => path.labels.join(' > '));
  assert.ok(chains.includes('Аноним > GET /one > one'));
  assert.ok(chains.includes('Аноним > GET /one > @PreAuthorize'));
  assert.ok(chains.includes('Аноним > GET /two > two > table'));
  const guardPath = result.paths.find((path) => path.labels.includes('@PreAuthorize'));
  assert.equal(guardPath?.guarded, true);
  const findingPath = result.paths.find((path) => path.labels.includes('one'));
  assert.equal(findingPath?.findings.length, 1);
});

test('cycles stop at the repeated node instead of expanding forever', () => {
  const result = enumerateAccessPaths(model([
    edge('e1', 'a', 'r1', 'matches'),
    edge('e2', 'r1', 'f1', 'routes_to'),
    edge('e3', 'f1', 'r1', 'calls'),
  ]));
  const cyclePath = result.paths.find((path) => path.nodeIds.join('>') === 'a>r1>f1');
  assert.ok(cyclePath);
});

test('path enumeration is bounded', () => {
  const result = enumerateAccessPaths(model([
    edge('e1', 'a', 'r1', 'matches'),
    edge('e2', 'r1', 'f1', 'routes_to'),
    edge('e3', 'r1', 'g1', 'requires'),
  ]), { maxPaths: 1 });
  assert.equal(result.paths.length, 1);
  assert.equal(result.truncated, true);
});
