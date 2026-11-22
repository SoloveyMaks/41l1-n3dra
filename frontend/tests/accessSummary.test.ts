import assert from 'node:assert/strict';
import { test } from 'node:test';

import { buildAccessSummary } from '../../ui/security-map/src/access.ts';
import type { MapViewModel, ViewEdge, ViewEvidence, ViewFinding, ViewNode } from '../../ui/security-map/src/types.ts';

const scope = {
  workspaceId: 'w', runId: 'r', snapshotId: 's', workflowRevisionId: 'rev',
  artifactHash: 'a', snapshotManifestHash: 'm',
};

const evidence = (id: string, path: string): ViewEvidence => ({
  id,
  location: { path, line: 1, column: 1, endLine: 1, endColumn: 1, byteStart: 0, byteEnd: 1 },
  sourceHash: 'h',
  producerVersion: 'p',
  redactedSnippet: '***',
});

const node = (id: string, kind: string, label: string, evidenceRefs: string[] = []): ViewNode => ({
  id, kind, label, symbolKey: `${kind}:${id}`, evidenceRefs, producerVersion: 'p',
});

const edge = (id: string, fromId: string, toId: string, kind: string): ViewEdge => ({
  id, fromId, toId, kind, evidenceRefs: [], resolution: 'resolved',
  resolutionReason: '', producerVersion: 'p', basis: 'source', policyRef: null,
});

test('unchecked routes sort first and file-linked finding is attached', () => {
  const finding: ViewFinding = {
    id: 'f1', label: 'Проблема', status: 'confirmed', severity: 'critical',
    confidence: 'high', reasons: [], evidenceRefs: ['ev2'], nodeIds: [],
  };
  const model: MapViewModel = {
    scope,
    revision: 'rev-hash',
    provenance: 'analyzer',
    policies: [],
    diagnostics: [],
    coverage: { status: 'partial', summary: '3/3', warnings: [] },
    nodes: [
      node('actor', 'actor', 'Неизвестный'),
      node('guarded', 'route', 'GET /api/users'),
      node('open', 'route', 'GET /api/admin/dump'),
      node('handler1', 'function', 'listUsers', ['ev1']),
      node('handler2', 'function', 'dumpDatabase', ['ev2']),
      node('guard', 'guard', 'Method security'),
    ],
    edges: [
      edge('e1', 'guarded', 'handler1', 'routes_to'),
      edge('e2', 'open', 'handler2', 'routes_to'),
      edge('e3', 'guarded', 'guard', 'requires'),
    ],
    findings: [finding],
    evidence: [evidence('ev1', 'src/Users.kt'), evidence('ev2', 'src/Admin.kt')],
  };

  const summary = buildAccessSummary(model);
  assert.equal(summary.routeCount, 2);
  assert.equal(summary.unknownCount, 1);
  assert.equal(summary.withFindingsCount, 1);
  assert.equal(summary.rows[0].routeId, 'open');
  assert.equal(summary.rows[0].method, 'GET');
  assert.equal(summary.rows[0].path, '/api/admin/dump');
  assert.equal(summary.rows[0].findings.length, 1);
  assert.equal(summary.rows[0].highestSeverity, 'critical');
  assert.equal(summary.rows[1].unchecked, false);
  assert.deepEqual(summary.rows[1].guards, ['Method security']);
});
