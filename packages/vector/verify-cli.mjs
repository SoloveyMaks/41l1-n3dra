#!/usr/bin/env node
// Live check for the demo fixture exact-search mechanics. Reads the same JSON
// the Python seeder reads; no database, no network, no dependencies.
//
// Usage:
//   node packages/vector/verify-cli.mjs [fixture.json]
//
// Exit 0 only when self-queries return distance 0, filters really filter,
// spec mismatch is unavailable, and vetted is refused.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { exactSearch, loadFixture } from './index.mjs';

const defaultFixture = fileURLToPath(new URL('../../db/optional/fixtures/vector_demo_examples.json', import.meta.url));
const fixturePath = process.argv[2] ?? defaultFixture;
const fixture = loadFixture(readFileSync(fixturePath, 'utf8'));
const failures = [];
const checks = [];

for (const example of fixture.examples) {
  const result = exactSearch(fixture, {
    queryVector: example.embedding,
    language: example.language,
    framework: example.framework,
    ruleFamily: example.ruleFamily,
    status: 'draft',
  });
  const first = result.items[0];
  checks.push({
    exampleId: example.exampleId,
    sourceRef: first?.sourceRef,
    status: first?.status,
    distance: first?.distance,
    metric: first?.metric,
  });
  if (result.status !== 'available') failures.push(`${example.sourceRef}: ${result.reason}`);
  if (!first) failures.push(`${example.sourceRef}: no rows`);
  else {
    if (first.exampleId !== example.exampleId) failures.push(`${example.sourceRef}: wrong first row ${first.exampleId}`);
    if (first.distance > 1e-9) failures.push(`${example.sourceRef}: distance ${first.distance} > 1e-9`);
    if (first.status !== 'draft') failures.push(`${example.sourceRef}: status ${first.status} is not draft`);
    if (!first.limitations?.length) failures.push(`${example.sourceRef}: limitations missing`);
    if (!String(first.sourceRef).startsWith('demo/fixture:')) failures.push(`${example.sourceRef}: source is not marked demo/fixture`);
  }
}

const tls = fixture.examples.find(example => example.framework === 'jvm');
if (!tls) failures.push('fixture has no jvm example to exercise the framework filter');
else {
  const wrongFramework = exactSearch(fixture, {
    queryVector: tls.embedding,
    language: tls.language,
    framework: 'spring-boot',
    ruleFamily: tls.ruleFamily,
  });
  if (wrongFramework.items.length !== 0) failures.push('framework filter did not exclude the jvm example');
  const wrongRule = exactSearch(fixture, {
    queryVector: tls.embedding,
    language: tls.language,
    framework: tls.framework,
    ruleFamily: 'sql-injection',
  });
  if (wrongRule.items.length !== 0) failures.push('rule-family filter did not exclude the example');
}

const vetted = exactSearch(fixture, {
  queryVector: fixture.examples[0].embedding,
  language: fixture.examples[0].language,
  framework: fixture.examples[0].framework,
  ruleFamily: fixture.examples[0].ruleFamily,
  status: 'vetted',
});
if (vetted.status !== 'unavailable') failures.push('demo fixture accepted status=vetted');

const mismatchedSpec = exactSearch(fixture, {
  queryVector: fixture.examples[0].embedding,
  language: fixture.examples[0].language,
  framework: fixture.examples[0].framework,
  ruleFamily: fixture.examples[0].ruleFamily,
  spec: { ...fixture.spec, modelVersion: 'demo/other' },
});
if (mismatchedSpec.status !== 'unavailable') failures.push('demo fixture accepted a mismatched embedding spec');

const output = {
  status: failures.length === 0 ? 'ok' : 'failed',
  fixturePath,
  note: 'demo/fixture only; synthetic vectors; draft-only; not vetted; no retrieval-quality claim',
  checks,
  negative: {
    frameworkFilter: 'excluded',
    ruleFamilyFilter: 'excluded',
    vetted: vetted.reason,
    mismatchedSpec: mismatchedSpec.reason,
  },
  failures,
};
process.stdout.write(`${JSON.stringify(output, null, 2)}\n`);
process.exitCode = failures.length === 0 ? 0 : 1;