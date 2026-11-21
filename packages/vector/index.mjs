// Dependency-free exact-search harness for the vetted-vector demo fixture.
//
// This module mirrors the SQL contract in db/optional/014 and db/optional/015
// for one purpose: to check the exact distance maths, the mandatory filters,
// the explicit spec compatibility and the draft-only refusal without a
// database. The backend runtime and PostgreSQL remain authoritative.
//
// It never returns vetted examples from the demo fixture and never claims a
// retrieval-quality improvement.

const METRICS = new Set(['cosine', 'l2', 'inner_product']);
const DEMO_PREFIX = 'demo/fixture:';
const DEMO_RULE_PREFIX = 'demo/';

function assertString(value, name) {
  if (typeof value !== 'string' || value.trim() === '') throw new TypeError(`${name} must be a non-empty string`);
}

function assertFiniteArray(value, name, dimension) {
  if (!Array.isArray(value) || value.length !== dimension) throw new TypeError(`${name} must have ${dimension} dimensions`);
  for (const item of value) {
    if (typeof item !== 'number' || !Number.isFinite(item)) throw new TypeError(`${name} must contain finite numbers`);
  }
}

export function validateFixture(fixture) {
  if (!fixture || typeof fixture !== 'object') throw new TypeError('fixture must be an object');
  if (fixture.schemaVersion !== 'vector-demo/1') throw new TypeError('unsupported fixture schemaVersion');
  uuidKey(fixture.workspaceId, 'fixture.workspaceId');
  const spec = fixture.spec;
  if (!spec || typeof spec !== 'object') throw new TypeError('fixture.spec is required');
  assertString(spec.embeddingSpecId, 'spec.embeddingSpecId');
  uuidKey(spec.embeddingSpecId, 'spec.embeddingSpecId');
  assertString(spec.provider, 'spec.provider');
  assertString(spec.model, 'spec.model');
  assertString(spec.modelVersion, 'spec.modelVersion');
  assertString(spec.queryPreparationVersion, 'spec.queryPreparationVersion');
  assertString(spec.documentPreparationVersion, 'spec.documentPreparationVersion');
  assertString(spec.redactionVersion, 'spec.redactionVersion');
  if (!Number.isInteger(spec.dimension) || spec.dimension <= 0) throw new TypeError('spec.dimension must be a positive integer');
  if (!METRICS.has(spec.metric)) throw new TypeError('spec.metric must be cosine, l2 or inner_product');
  if (!Array.isArray(fixture.examples) || fixture.examples.length < 3 || fixture.examples.length > 5) {
    throw new TypeError('fixture must contain 3-5 examples');
  }
  const seen = new Set();
  for (const example of fixture.examples) {
    assertString(example.exampleId, 'example.exampleId');
    if (seen.has(example.exampleId)) throw new TypeError(`duplicate exampleId ${example.exampleId}`);
    seen.add(example.exampleId);
    if (example.status !== 'draft') throw new TypeError('demo fixture examples must be draft-only');
    if (!String(example.sourceRef).startsWith(DEMO_PREFIX)) throw new TypeError('sourceRef must start with demo/fixture:');
    if (!String(example.ruleVersion).startsWith(DEMO_RULE_PREFIX)) throw new TypeError('ruleVersion must start with demo/');
    for (const key of ['language', 'framework', 'ruleFamily', 'redactedBefore', 'redactedFix', 'verificationBasis']) {
      assertString(example[key], `example.${key}`);
    }
    if (!Array.isArray(example.limitations) || example.limitations.length === 0) {
      throw new TypeError('example.limitations must be non-empty');
    }
    assertFiniteArray(example.embedding, 'example.embedding', spec.dimension);
    if (spec.metric !== 'l2' && example.embedding.every(value => value === 0)) {
      throw new TypeError('example.embedding must be non-zero for cosine/inner_product');
    }
  }
  return fixture;
}

export function loadFixture(text) {
  const fixture = typeof text === 'string' ? JSON.parse(text) : text;
  return validateFixture(fixture);
}

function distance(metric, a, b) {
  let dot = 0;
  let sum = 0;
  let normA = 0;
  let normB = 0;
  for (let index = 0; index < a.length; index += 1) {
    dot += a[index] * b[index];
    normA += a[index] * a[index];
    normB += b[index] * b[index];
    const delta = a[index] - b[index];
    sum += delta * delta;
  }
  if (metric === 'cosine') {
    // pgvector <=> is 1 - (a.b)/(|a||b|), not 1 - a.b. The fixture vectors are
    // unit length, but the query is caller-supplied and must not be assumed so.
    return 1 - dot / (Math.sqrt(normA) * Math.sqrt(normB));
  }
  if (metric === 'l2') return Math.sqrt(sum);
  return -dot;
}

function uuidKey(value, name) {
  assertString(value, name);
  const key = value.trim().toLowerCase();
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(key)) {
    throw new TypeError(`${name} must be a UUID`);
  }
  return key;
}

function specMatches(fixtureSpec, spec) {
  // Text fields are compared exactly, as PostgreSQL does. Only UUID identity
  // is normalised; lowercasing a model or version would accept a mismatch.
  return ['provider', 'model', 'modelVersion', 'queryPreparationVersion', 'documentPreparationVersion', 'redactionVersion']
    .every(key => fixtureSpec[key] === spec[key])
    && uuidKey(fixtureSpec.embeddingSpecId, 'spec.embeddingSpecId') === uuidKey(spec.embeddingSpecId, 'spec.embeddingSpecId')
    && fixtureSpec.dimension === spec.dimension
    && fixtureSpec.metric === spec.metric;
}

/**
 * Exact search over the demo fixture.
 *
 * @param {object} fixture validated demo fixture
 * @param {object} options workspaceId, queryVector, language, framework,
 *   ruleFamily, status='draft', limit=5, spec=null (spec compatibility check)
 * @returns {{status:'available'|'unavailable', reason:string|null, items:object[]}}
 */
export function exactSearch(fixture, options = {}) {
  validateFixture(fixture);
  const spec = fixture.spec;
  if (options.workspaceId === undefined) {
    return { status: 'unavailable', reason: 'explicit workspaceId is required', items: [] };
  }
  if (uuidKey(options.workspaceId, 'workspaceId') !== uuidKey(fixture.workspaceId, 'fixture.workspaceId')) {
    return { status: 'unavailable', reason: 'workspace does not match the demo fixture workspace', items: [] };
  }
  if (options.spec && !specMatches(spec, options.spec)) {
    return { status: 'unavailable', reason: 'embedding spec does not match the configured model/versions', items: [] };
  }
  if (options.status === undefined) {
    return { status: 'unavailable', reason: 'explicit status filter is required', items: [] };
  }
  if (options.status !== 'draft') {
    return { status: 'unavailable', reason: 'demo fixture is draft-only; vetted examples do not exist', items: [] };
  }
  const queryVector = options.queryVector;
  assertFiniteArray(queryVector, 'queryVector', spec.dimension);
  if (spec.metric !== 'l2' && queryVector.every(value => value === 0)) {
    return { status: 'unavailable', reason: `zero query vector for ${spec.metric} metric`, items: [] };
  }
  assertString(options.language, 'language');
  assertString(options.framework, 'framework');
  assertString(options.ruleFamily, 'ruleFamily');
  const limit = options.limit ?? 5;
  if (!Number.isInteger(limit) || limit < 1 || limit > 20) {
    return { status: 'unavailable', reason: 'limit must be between 1 and 20', items: [] };
  }

  const items = fixture.examples
    .filter(example => example.status === 'draft'
      && example.language === options.language
      && example.framework === options.framework
      && example.ruleFamily === options.ruleFamily)
    .map(example => ({
      exampleId: example.exampleId,
      sourceRef: example.sourceRef,
      redactedBefore: example.redactedBefore,
      redactedFix: example.redactedFix,
      ruleVersion: example.ruleVersion,
      verificationBasis: example.verificationBasis,
      limitations: [...example.limitations],
      status: example.status,
      distance: distance(spec.metric, example.embedding, queryVector),
      metric: spec.metric,
      embeddingSpecId: spec.embeddingSpecId,
    }))
    .sort((left, right) => (left.distance - right.distance) || left.exampleId.localeCompare(right.exampleId))
    .slice(0, limit);

  return { status: 'available', reason: null, items };
}