import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { compareBundles, deterministicId, manifestHash } from '../../../packages/comparison/index.mjs';
import { validateBundle } from '../../../docs/data-contracts/validate-bundle.mjs';

const ROOT = fileURLToPath(new URL('.', import.meta.url));
const TAG = 'sql-before-after/harness/1';

const sha256 = value => createHash('sha256').update(value).digest('hex');

function rescope(value, identity) {
  if (Array.isArray(value)) return value.map(item => rescope(item, identity));
  if (value && typeof value === 'object') {
    const out = {};
    for (const [key, item] of Object.entries(value)) out[key] = rescope(item, identity);
    if ('workspaceId' in out) out.workspaceId = identity.workspaceId;
    if ('runId' in out) out.runId = identity.runId;
    if ('snapshotId' in out) out.snapshotId = identity.snapshotId;
    return out;
  }
  return value;
}

function envelope(side) {
  const raw = JSON.parse(readFileSync(join(ROOT, `raw-${side}.json`), 'utf8'));
  const files = raw.manifestFiles.map(file => ({
    path: file.path,
    sourceHash: file.sha256,
    byteLength: file.sizeBytes,
    mediaType: file.mediaType,
  }));
  const hash = manifestHash(files);
  const snapshotId = deterministicId(`${TAG}/snapshot/${side}/${hash}`);
  const identity = {
    ...raw.identity,
    snapshotId,
    artifactHash: raw.zip.sha256,
    snapshotManifestHash: hash,
  };
  const scanResults = raw.scanResults.map(part => ({
    ...part,
    identity,
    signature: raw.signature,
  }));
  const codeFacts = {
    ...raw.codeFacts,
    identity,
    signature: raw.signature,
  };
  const bundle = rescope({
    schemaVersion: '0.1',
    identity,
    signature: raw.signature,
    scanResults,
    codeFacts,
  }, identity);
  const sourceFiles = new Map(
    files.map(file => [file.path, readFileSync(join(ROOT, side, file.path))]),
  );
  validateBundle(bundle, sourceFiles);
  return {
    bundle,
    manifest: {
      complete: true,
      workspaceId: identity.workspaceId,
      snapshotId,
      hash,
      files,
    },
    provenance: {
      published: true,
      bundleHash: sha256(JSON.stringify(bundle)),
      normalizationVersion: TAG,
      origin: 'scanner',
      expectedScopesVerified: raw.expectedScopesVerified === true,
      domainPolicyApplicability: 'not_applicable',
    },
  };
}

const before = envelope('before');
const after = envelope('after');
const comparison = compareBundles(before, after);

writeFileSync(join(ROOT, 'baseline.published.json'), `${JSON.stringify(before, null, 2)}\n`);
writeFileSync(join(ROOT, 'current.published.json'), `${JSON.stringify(after, null, 2)}\n`);
writeFileSync(join(ROOT, 'comparison.json'), `${JSON.stringify(comparison, null, 2)}\n`);

const statuses = comparison.items.map(item => `${item.status}:${item.matchingBasis}`);
console.log(JSON.stringify({
  comparable: comparison.comparable,
  reasons: comparison.reasons,
  items: comparison.items.length,
  statuses,
  deletedFiles: comparison.deletedFiles.length,
}, null, 2));