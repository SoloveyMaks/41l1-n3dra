import { createHash } from 'node:crypto';

import { deterministicId, manifestHash } from './index.mjs';
import { validateBundle } from '../../docs/data-contracts/validate-bundle.mjs';

export const SQL_RULE_ID = 'KOTLIN-SQL-INJECTION';
export const SQL_RULE_CATEGORY = 'SAST';
export const SQL_RULE_SCOPE = 'source_pattern';
export const SQL_RULE_STATUSES = Object.freeze([
  'persistent', 'changed', 'pattern_disappeared', 'inconclusive', 'file_deleted',
]);
const SCHEMA_VERSION = 'sql-rule-comparison/1';
const HASH_RE = /^[0-9a-f]{64}$/;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const SAFE_PATH_RE = /^(?!\/)(?!.*\\)(?!.*(?:^|\/)\.{1,2}(?:\/|$))(?!.*\/\/)(?![A-Za-z]:)(?!.*[\x00-\x1f\x7f])(?!.*\/$).+$/;
const SIGNATURE_KEYS = [
  'parserVersion', 'parserHash', 'rulesVersion', 'rulesHash',
  'advisoryVersion', 'advisoryHash', 'configVersion', 'scanConfigHash',
  'domainPolicyRevisionId', 'domainPolicyVersion', 'domainPolicyHash',
  'hmacKeyVersion',
];
const POLICY_APPLICABILITY = new Set(['not_applicable', 'applied', 'unknown']);

const hash = value => createHash('sha256').update(value).digest('hex');
const isObject = value => Boolean(value) && typeof value === 'object' && !Array.isArray(value);

function stable(value) {
  if (Array.isArray(value)) return `[${value.map(stable).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${stable(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

function assertString(value, name) {
  if (typeof value !== 'string' || value.length === 0) throw new TypeError(`${name} must be a non-empty string`);
}
function assertHash(value, name) {
  if (typeof value !== 'string' || !HASH_RE.test(value)) throw new TypeError(`${name} must be SHA-256 lowercase hex`);
}
function assertUuid(value, name) {
  if (typeof value !== 'string' || !UUID_RE.test(value)) throw new TypeError(`${name} must be a canonical lowercase UUID`);
}

function signatureDiff(before, current) {
  return SIGNATURE_KEYS.filter(key => before.signature[key] !== current.signature[key]);
}

function validateEnvelope(input, side) {
  if (!isObject(input) || !isObject(input.bundle) || !isObject(input.manifest) || !isObject(input.provenance)) {
    throw new TypeError(`${side} must be a published scanner envelope with bundle, manifest and provenance`);
  }
  const { bundle, manifest, provenance } = input;
  if (bundle.schemaVersion !== '0.1') throw new TypeError(`${side} unsupported bundle schema`);
  validateBundle(bundle);

  const identity = bundle.identity;
  for (const key of ['workspaceId', 'runId', 'snapshotId', 'workflowRevisionId']) assertUuid(identity[key], `${side} ${key}`);
  assertHash(identity.artifactHash, `${side} artifactHash`);
  assertHash(identity.snapshotManifestHash, `${side} snapshotManifestHash`);

  for (const key of SIGNATURE_KEYS) {
    const value = bundle.signature[key];
    if (key.startsWith('domainPolicy') && value === null) continue;
    if (key.endsWith('Hash')) assertHash(value, `${side} signature.${key}`);
    else assertString(value, `${side} signature.${key}`);
  }
  const policy = ['domainPolicyRevisionId', 'domainPolicyVersion', 'domainPolicyHash'].map(key => bundle.signature[key]);
  if (!(policy.every(value => value === null) || policy.every(value => value !== null))) {
    throw new TypeError(`${side} partial domain policy signature`);
  }

  if (manifest.complete !== true || !Array.isArray(manifest.files)) {
    throw new TypeError(`${side} manifest must be complete`);
  }
  if (manifest.workspaceId !== identity.workspaceId || manifest.snapshotId !== identity.snapshotId) {
    throw new TypeError(`${side} manifest identity differs from bundle`);
  }
  assertHash(manifest.hash, `${side} manifest hash`);
  if (manifestHash(manifest.files) !== manifest.hash) throw new TypeError(`${side} manifest hash recipe differs`);
  if (identity.snapshotManifestHash !== manifest.hash) throw new TypeError(`${side} snapshot manifest hash differs`);

  const files = new Map();
  for (const file of manifest.files) {
    assertString(file?.path, `${side} manifest path`);
    if (!SAFE_PATH_RE.test(file.path)) throw new TypeError(`${side} unsafe manifest path`);
    assertHash(file?.sourceHash, `${side} source hash for ${file.path}`);
    if (!Number.isSafeInteger(file.byteLength) || file.byteLength < 0) {
      throw new TypeError(`${side} invalid byteLength for ${file.path}`);
    }
    assertString(file?.mediaType, `${side} mediaType for ${file.path}`);
    if (files.has(file.path)) throw new TypeError(`${side} duplicate manifest path ${file.path}`);
    files.set(file.path, file);
  }

  for (const part of [...bundle.scanResults, bundle.codeFacts]) {
    for (const evidence of part.evidence) {
      const file = files.get(evidence.location.path);
      if (!file || file.sourceHash !== evidence.sourceHash || file.byteLength < evidence.location.byteEnd) {
        throw new TypeError(`${side} evidence differs from manifest: ${evidence.location.path}`);
      }
    }
    if (part.coverage.scopes.some(scope => !files.has(scope.path))) {
      throw new TypeError(`${side} coverage path missing from manifest`);
    }
  }

  if (provenance.published !== true) throw new TypeError(`${side} provenance is not published`);
  assertHash(provenance.bundleHash, `${side} provenance bundleHash`);
  assertString(provenance.normalizationVersion, `${side} normalizationVersion`);
  assertString(provenance.origin, `${side} provenance origin`);
  if (typeof provenance.expectedScopesVerified !== 'boolean') {
    throw new TypeError(`${side} expectedScopesVerified must be boolean`);
  }
  if (!POLICY_APPLICABILITY.has(provenance.domainPolicyApplicability)) {
    throw new TypeError(`${side} invalid domainPolicyApplicability`);
  }
  for (const key of ['envelopeHash', 'canonicalHash', 'documentHash']) {
    if (input[key] !== undefined) assertHash(input[key], `${side} ${key}`);
  }
  if (input.publishedAt !== undefined) assertString(input.publishedAt, `${side} publishedAt`);
  if (provenance.domainPolicyApplicability === 'applied' && policy.some(value => value === null)) {
    throw new TypeError(`${side} applied policy lacks signature policy`);
  }
  if (provenance.domainPolicyApplicability === 'not_applicable' && policy.some(value => value !== null)) {
    throw new TypeError(`${side} not_applicable policy has signature policy`);
  }

  return { bundle, manifest, provenance, files };
}

function sastScan(bundle) {
  return bundle.scanResults.find(scan => scan.category === SQL_RULE_CATEGORY);
}

function selectedScopes(scan, path) {
  return (scan.coverage.scopes ?? []).filter(scope =>
    scope.category === SQL_RULE_CATEGORY && scope.ruleId === SQL_RULE_ID && scope.path === path
  );
}

function checkedScopePaths(scan) {
  return [...new Set((scan.coverage.scopes ?? [])
    .filter(scope => scope.category === SQL_RULE_CATEGORY && scope.ruleId === SQL_RULE_ID && scope.status === 'checked')
    .map(scope => scope.path))].sort();
}

function scopeResult(scan, path) {
  const scopes = selectedScopes(scan, path);
  if (scopes.length === 0) return { error: 'missing', scopes };
  if (scopes.length > 1) return { error: 'ambiguous', scopes };
  if (scopes[0].status !== 'checked') return { error: 'not_checked', scope: scopes[0], scopes };
  return { scope: scopes[0], scopes };
}

function anchorKey(finding) {
  const anchor = finding.anchor;
  return stable([finding.category, finding.ruleId, anchor.normalizedPath, anchor.symbolKey, anchor.role]);
}

function groupFindings(findings) {
  const groups = new Map();
  for (const finding of findings) {
    const key = anchorKey(finding);
    const values = groups.get(key) ?? [];
    values.push(finding);
    groups.set(key, values);
  }
  return groups;
}

function findingsForPath(scan, path) {
  return (scan.findings ?? []).filter(finding =>
    finding.category === SQL_RULE_CATEGORY &&
    finding.ruleId === SQL_RULE_ID &&
    finding.anchor?.normalizedPath === path
  );
}

function anchorSummary(finding) {
  if (!finding) return null;
  return {
    path: finding.anchor.normalizedPath,
    symbolKey: finding.anchor.symbolKey,
    role: finding.anchor.role,
    normalizedContextHash: finding.anchor.normalizedContextHash,
  };
}

function scopeSummary(scope) {
  if (!scope) return null;
  return {
    path: scope.path,
    ruleId: scope.ruleId,
    category: scope.category,
    status: scope.status,
    reason: scope.reason,
  };
}

function scanProblem(scan, side) {
  if (!scan) return `${side}: SAST scan отсутствует`;
  if (!['complete', 'partial'].includes(scan.status)) {
    return `${side}: SAST status=${scan.status}, scoped proof не строится`;
  }
  const parserErrors = scan.coverage.parserErrors ?? [];
  if (parserErrors.length) return `${side}: parserErrors=${parserErrors.length}; scoped proof запрещён`;
  const limits = scan.coverage.limitsReached ?? [];
  if (limits.length) return `${side}: limitsReached=${limits.length}; scoped proof запрещён`;
  return null;
}

function baseResult({ before, after, path, status, reasons, matchingBasis, baselineFinding, currentFinding, baselineScope, currentScope, candidatePaths, deletedFile, signaturesCompatible, id }) {
  const bIdentity = before.bundle.identity;
  const cIdentity = after.bundle.identity;
  const normalizedReasons = reasons.filter(Boolean);
  return {
    schemaVersion: SCHEMA_VERSION,
    id: id ?? deterministicId(stable([
      SCHEMA_VERSION, bIdentity.workspaceId, bIdentity.runId, bIdentity.snapshotId,
      cIdentity.runId, cIdentity.snapshotId, path ?? '', status,
    ])),
    scope: SQL_RULE_SCOPE,
    ruleId: SQL_RULE_ID,
    workspaceId: bIdentity.workspaceId,
    baselineRunId: bIdentity.runId,
    baselineSnapshotId: bIdentity.snapshotId,
    currentRunId: cIdentity.runId,
    currentSnapshotId: cIdentity.snapshotId,
    path: path ?? null,
    status,
    matchingBasis: matchingBasis ?? null,
    reason: normalizedReasons.join('; '),
    globalComparable: false,
    signaturesCompatible: signaturesCompatible !== false,
    reasons: normalizedReasons,
    baselineFindingId: baselineFinding?.id ?? null,
    currentFindingId: currentFinding?.id ?? null,
    baselineFingerprint: baselineFinding?.fingerprint ?? null,
    currentFingerprint: currentFinding?.fingerprint ?? null,
    baselineRuleVersion: baselineFinding?.ruleVersion ?? null,
    currentRuleVersion: currentFinding?.ruleVersion ?? null,
    baselineAnchor: anchorSummary(baselineFinding),
    currentAnchor: anchorSummary(currentFinding),
    baselineCheckedScope: scopeSummary(baselineScope),
    currentCheckedScope: scopeSummary(currentScope),
    candidatePaths: candidatePaths ?? [],
    deletedFile: deletedFile ?? null,
    expectedScopesVerified: {
      baseline: before.provenance.expectedScopesVerified,
      current: after.provenance.expectedScopesVerified,
    },
    provenance: {
      baselineBundleHash: before.provenance.bundleHash,
      currentBundleHash: after.provenance.bundleHash,
      normalizationVersion: before.provenance.normalizationVersion,
      origin: before.provenance.origin,
    },
    manifestHashes: {
      baseline: before.manifest.hash,
      current: after.manifest.hash,
    },
  };
}

/**
 * Compare one concrete KOTLIN-SQL-INJECTION source pattern in two published
 * scanner envelopes. This is a narrow source-pattern proof, not a full
 * ComparisonDto and not a runtime-fix or whole-run comparable claim.
 */
export function compareSqlRuleScopes(baselineInput, currentInput, options = {}) {
  const requestedPath = options?.path;
  if (requestedPath !== undefined) {
    assertString(requestedPath, 'options.path');
  }
  const requestedId = options?.id;
  if (requestedId !== undefined) {
    assertUuid(requestedId, 'options.id');
  }
  const before = validateEnvelope(baselineInput, 'baseline');
  const after = validateEnvelope(currentInput, 'current');
  const bIdentity = before.bundle.identity;
  const cIdentity = after.bundle.identity;
  if (bIdentity.workspaceId !== cIdentity.workspaceId) throw new TypeError('Bundles belong to different workspaces');
  if (bIdentity.runId === cIdentity.runId || bIdentity.snapshotId === cIdentity.snapshotId) {
    throw new TypeError('Baseline and current runs/snapshots must be distinct');
  }
  const emit = fields => baseResult({ ...fields, id: requestedId });

  const bScan = sastScan(before.bundle);
  const cScan = sastScan(after.bundle);
  if (!bScan || !cScan) {
    return emit({
      before, after, path: requestedPath ?? null, status: 'inconclusive',
      matchingBasis: 'none', reasons: ['SAST scan отсутствует в одном из published envelopes'],
      baselineScope: null, currentScope: null,
    });
  }

  const signatureChanges = signatureDiff(before.bundle, after.bundle);
  const normalizationDiff = before.provenance.normalizationVersion !== after.provenance.normalizationVersion;
  const origins = [before.provenance, after.provenance].map(item => item.origin);
  const policyUnknown = [before.provenance, after.provenance]
    .some(item => item.domainPolicyApplicability === 'unknown');
  const signaturesCompatible = signatureChanges.length === 0 && !normalizationDiff && !policyUnknown &&
    origins.every(origin => origin === 'scanner');

  let path = requestedPath ?? null;
  if (!path) {
    const paths = checkedScopePaths(bScan);
    if (paths.length === 0) {
      return emit({
        before, after, path: null, status: 'inconclusive', matchingBasis: 'none',
        reasons: ['Нет ни одного checked scope SAST/KOTLIN-SQL-INJECTION'],
        signaturesCompatible,
      });
    }
    if (paths.length > 1) {
      return emit({
        before, after, path: null, status: 'inconclusive', matchingBasis: 'ambiguous',
        reasons: [`Несколько checked SQL scopes; укажите options.path: ${paths.join(', ')}`],
        candidatePaths: paths, signaturesCompatible,
      });
    }
    path = paths[0];
  }

  if (!before.files.has(path)) throw new TypeError(`baseline manifest has no selected path ${path}`);
  if (!origins.every(origin => origin === 'scanner')) {
    return emit({
      before, after, path, status: 'inconclusive', matchingBasis: 'none',
      reasons: ['Provenance origin не scanner; scoped SQL proof не строится'],
      baselineScope: scopeResult(bScan, path).scope ?? null,
      currentScope: scopeResult(cScan, path).scope ?? null,
      signaturesCompatible: false,
    });
  }
  const deleted = !after.files.has(path);

  if (deleted) {
    return emit({
      before, after, path, status: 'file_deleted', matchingBasis: 'none',
      reasons: [
        'Путь отсутствует в полном current manifest; удаление не является исправлением SQL-паттерна.',
        ...signatureChanges.map(key => `Подписи различаются: ${key}`),
        ...(normalizationDiff ? ['Различаются normalizationVersion'] : []),
        ...(!signaturesCompatible ? ['Provenance/scanner или policy attestation несовместимы; удаление не делает run comparable'] : []),
      ],
      baselineFinding: findingsForPath(bScan, path).length === 1 ? findingsForPath(bScan, path)[0] : null,
      baselineScope: scopeResult(bScan, path).scope ?? null,
      deletedFile: {
        path,
        baselineSourceHash: before.files.get(path).sourceHash,
        reason: 'Путь отсутствует в полном current manifest; rename не исключён.',
      },
      signaturesCompatible,
    });
  }

  if (!signaturesCompatible) {
    return emit({
      before, after, path, status: 'inconclusive', matchingBasis: 'none',
      reasons: [
        ...signatureChanges.map(key => `Подписи различаются: ${key}`),
        ...(normalizationDiff ? ['Различаются normalizationVersion'] : []),
        ...(!origins.every(origin => origin === 'scanner') ? ['Provenance origin не scanner'] : []),
        ...(policyUnknown ? ['domainPolicyApplicability=unknown'] : []),
      ],
      baselineScope: scopeResult(bScan, path).scope ?? null,
      currentScope: scopeResult(cScan, path).scope ?? null,
      signaturesCompatible,
    });
  }

  const scanIssue = scanProblem(bScan, 'baseline') ?? scanProblem(cScan, 'current');
  if (scanIssue) {
    return emit({
      before, after, path, status: 'inconclusive', matchingBasis: 'none',
      reasons: [scanIssue], baselineScope: null, currentScope: null,
      signaturesCompatible,
    });
  }

  const baselineScope = scopeResult(bScan, path);
  const currentScope = scopeResult(cScan, path);
  if (baselineScope.error || currentScope.error) {
    return emit({
      before, after, path, status: 'inconclusive', matchingBasis: 'none',
      reasons: [
        `baseline scope: ${baselineScope.error ?? 'ok'}`,
        `current scope: ${currentScope.error ?? 'ok'}`,
      ],
      baselineScope: baselineScope.scope ?? null,
      currentScope: currentScope.scope ?? null,
      signaturesCompatible,
    });
  }

  const baselineFindings = findingsForPath(bScan, path);
  const currentFindings = findingsForPath(cScan, path);
  const baselineGroups = groupFindings(baselineFindings);
  const currentGroups = groupFindings(currentFindings);
  if (baselineGroups.size !== 1) {
    return emit({
      before, after, path, status: 'inconclusive', matchingBasis: baselineGroups.size > 1 ? 'ambiguous' : 'none',
      reasons: [baselineGroups.size > 1
        ? 'Baseline findings не уникальны по stable anchor'
        : 'В baseline нет finding для выбранного SQL scope'],
      baselineScope: baselineScope.scope, currentScope: currentScope.scope,
      signaturesCompatible,
    });
  }

  const [baselineAnchorKey, baselineGroup] = [...baselineGroups.entries()][0];
  if (baselineGroup.length !== 1) {
    return emit({
      before, after, path, status: 'inconclusive', matchingBasis: 'ambiguous',
      reasons: ['Baseline anchor повторяется; выбрать finding нельзя'],
      baselineScope: baselineScope.scope, currentScope: currentScope.scope,
      signaturesCompatible,
    });
  }
  const baselineFinding = baselineGroup[0];

  if (currentGroups.size > 1) {
    return emit({
      before, after, path, status: 'inconclusive', matchingBasis: 'ambiguous',
      reasons: ['Current SQL findings не уникальны по stable anchor'],
      baselineFinding, baselineScope: baselineScope.scope, currentScope: currentScope.scope,
      signaturesCompatible,
    });
  }
  if (currentGroups.size === 0) {
    return emit({
      before, after, path, status: 'pattern_disappeared', matchingBasis: 'unique_anchor',
      reasons: [
        'Файл сохранился; выбранный SAST/KOTLIN-SQL-INJECTION scope checked на обеих сторонах; baseline anchor уникален, current findings/anchor отсутствуют. Это статическое исчезновение, не runtime fix.',
      ],
      baselineFinding, baselineScope: baselineScope.scope, currentScope: currentScope.scope,
      signaturesCompatible,
    });
  }

  const [currentAnchorKey, currentGroup] = [...currentGroups.entries()][0];
  if (currentGroup.length !== 1) {
    return emit({
      before, after, path, status: 'inconclusive', matchingBasis: 'ambiguous',
      reasons: ['Current anchor повторяется; выбрать finding нельзя'],
      baselineFinding, baselineScope: baselineScope.scope, currentScope: currentScope.scope,
      signaturesCompatible,
    });
  }
  const currentFinding = currentGroup[0];
  if (baselineFinding.ruleVersion !== currentFinding.ruleVersion) {
    return emit({
      before, after, path, status: 'inconclusive', matchingBasis: 'none',
      reasons: [`Rule version изменилась: ${baselineFinding.ruleVersion} → ${currentFinding.ruleVersion}`],
      baselineFinding, currentFinding, baselineScope: baselineScope.scope, currentScope: currentScope.scope,
      signaturesCompatible,
    });
  }
  if (baselineAnchorKey !== currentAnchorKey) {
    return emit({
      before, after, path, status: 'inconclusive', matchingBasis: 'ambiguous',
      reasons: ['Current finding в том же path/rule/role имеет другой stable anchor; возможен rename или ambiguous anchor, disappearance не доказан'],
      baselineFinding, currentFinding, baselineScope: baselineScope.scope, currentScope: currentScope.scope,
      signaturesCompatible,
    });
  }
  const status = baselineFinding.fingerprint === currentFinding.fingerprint ? 'persistent' : 'changed';
  return emit({
    before, after, path, status, matchingBasis: 'unique_anchor',
    reasons: [status === 'persistent'
      ? 'Тот же уникальный stable anchor и fingerprint; сдвиг строк не меняет находку.'
      : 'Тот же уникальный stable anchor, но fingerprint/context изменились; это changed, не disappeared.'],
    baselineFinding, currentFinding, baselineScope: baselineScope.scope, currentScope: currentScope.scope,
    signaturesCompatible,
  });
}