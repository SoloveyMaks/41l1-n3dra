import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { compareBundles, deterministicId, manifestHash } from '../../../packages/comparison/index.mjs';
import { validateBundle } from '../../../docs/data-contracts/validate-bundle.mjs';

const ROOT = fileURLToPath(new URL('.', import.meta.url));
const TAG = 'sql-before-after/negative-probe/1';
const sha256 = value => createHash('sha256').update(value).digest('hex');
const read = name => JSON.parse(readFileSync(join(ROOT, name), 'utf8'));

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function refreshBundle(envelope) {
  envelope.bundle.identity.snapshotManifestHash = envelope.manifest.hash;
  for (const part of [...envelope.bundle.scanResults, envelope.bundle.codeFacts]) {
    part.identity = envelope.bundle.identity;
  }
  envelope.provenance.bundleHash = sha256(JSON.stringify(envelope.bundle));
  validateBundle(envelope.bundle);
  return envelope;
}

function compare(before, after, label) {
  const result = compareBundles(before, after, { id: deterministicId(`${TAG}/${label}`) });
  return {
    label,
    comparable: result.comparable,
    statuses: result.items.map(item => item.status),
    deletedFiles: result.deletedFiles.map(item => item.path),
    reasons: result.reasons,
  };
}

const baseline = read('baseline.published.json');
const current = read('current.published.json');
const probes = [];

// 1. Coverage loss: the negative SQL scope is no longer complete.
const coverageLoss = clone(current);
const sast = coverageLoss.bundle.scanResults.find(scan => scan.category === 'SAST');
sast.status = 'partial';
sast.coverage.status = 'partial';
sast.coverage.scopes = [];
sast.coverage.skippedScopes = ['synthetic-coverage-loss-probe'];
refreshBundle(coverageLoss);
probes.push(compare(baseline, coverageLoss, 'coverage-loss'));

// 2. Graph coverage loss: a real graph gap must also veto resolved.
const graphLoss = clone(current);
graphLoss.bundle.codeFacts.coverage.status = 'partial';
graphLoss.bundle.codeFacts.coverage.skippedScopes = ['synthetic-graph-coverage-loss-probe'];
refreshBundle(graphLoss);
probes.push(compare(baseline, graphLoss, 'graph-coverage-loss'));

// 3. Deleted file: the current complete manifest no longer contains the
//    baseline controller path. This is a manifest mutation probe, not a scan.
const deleted = clone(current);
const missingPath = 'src/main/kotlin/demo/RepairLookupController.kt';
deleted.manifest.files = deleted.manifest.files.filter(file => file.path !== missingPath);
deleted.manifest.hash = manifestHash(deleted.manifest.files);

const removedEvidence = new Set();
const removedNodes = new Set();
const bundle = deleted.bundle;
for (const part of [...bundle.scanResults, bundle.codeFacts]) {
  part.evidence = part.evidence.filter(item => {
    if (item.location.path === missingPath) {
      removedEvidence.add(item.id);
      return false;
    }
    return true;
  });
  part.coverage.scopes = part.coverage.scopes.filter(scope => scope.path !== missingPath);
  part.coverage.filesTotal = deleted.manifest.files.length;
  part.coverage.filesVisited = Math.min(part.coverage.filesVisited, part.coverage.filesTotal);
  if (part.findings) {
    part.findings = part.findings.filter(finding => finding.primaryLocation.path !== missingPath);
  }
}
bundle.codeFacts.nodes = bundle.codeFacts.nodes.filter(node => {
  if (node.evidenceRefs.some(id => removedEvidence.has(id))) {
    removedNodes.add(node.id);
    return false;
  }
  return true;
});
bundle.codeFacts.edges = bundle.codeFacts.edges.filter(edge =>
  !removedNodes.has(edge.fromId) &&
  !removedNodes.has(edge.toId) &&
  !edge.evidenceRefs.some(id => removedEvidence.has(id))
);
deleted.bundle.identity.snapshotManifestHash = deleted.manifest.hash;
refreshBundle(deleted);
probes.push(compare(baseline, deleted, 'deleted-file'));

writeFileSync(join(ROOT, 'negative-probes.json'), `${JSON.stringify(probes, null, 2)}\n`);
console.log(JSON.stringify(probes, null, 2));

const coverage = probes.find(probe => probe.label === 'coverage-loss');
const graph = probes.find(probe => probe.label === 'graph-coverage-loss');
const deletion = probes.find(probe => probe.label === 'deleted-file');
if (!coverage.statuses.every(status => status === 'inconclusive')) throw new Error('coverage loss was not inconclusive');
if (!graph.statuses.every(status => status === 'inconclusive')) throw new Error('graph loss was not inconclusive');
if (!deletion.statuses.includes('file_deleted')) throw new Error('deleted path was not file_deleted');
if (!deletion.deletedFiles.includes(missingPath)) throw new Error('deleted path missing from deletedFiles');