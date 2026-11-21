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
  const spec = fixture.spec;
  if (!spec || typeof spec !== 'object') throw new TypeError('fixture.spec is required');
  assertString(spec.embeddingSpecId, 'spec.embeddingSpecId');
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
  for (let index = 0; index < a.length; index += 1) {
    dot += a[index] * b[index];
    const delta = a[index] - b[index];
    sum += delta * delta;
  }
  if (metric === 'cosine') return 1 - dot;
  if (metric === 'l2') return Math.sqrt(sum);
  return -dot;
}

function specMatches(fixtureSpec, spec) {
  return ['embeddingSpecId', 'provider', 'model', 'modelVersion', 'queryPreparationVersion', 'documentPreparationVersion', 'redactionVersion']
    .every(key => String(fixtureSpec[key]).toLowerCase() === String(spec[key]).toLowerCase())
    && fixtureSpec.dimension === spec.dimension
    && fixtureSpec.metric === spec.metric;
}

/**
 * Exact search over the demo fixture.
 *
 * @param {object} fixture validated demo fixture
 * @param {object} options queryVector, language, framework, ruleFamily,
 *   status='draft', limit=5, spec=null (spec compatibility check)
 * @returns {{status:'available'|'unavailable', reason:string|null, items:object[]}}
 */
export function exactSearch(fixture, options = {}) {
  validateFixture(fixture);
  const spec = fixture.spec;
  if (options.spec && !specMatches(spec, options.spec)) {
    return { status: 'unavailable', reason: 'embedding spec does not match the configured model/versions', items: [] };
  }
  if (options.status !== undefined && options.status !== 'draft') {
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