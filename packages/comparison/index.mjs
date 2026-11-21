import { createHash } from 'node:crypto';
import { validateBundle } from '../../docs/data-contracts/validate-bundle.mjs';

const SIGNATURE_KEYS = [
  'parserVersion', 'parserHash', 'rulesVersion', 'rulesHash',
  'advisoryVersion', 'advisoryHash', 'configVersion', 'scanConfigHash',
  'domainPolicyRevisionId', 'domainPolicyVersion', 'domainPolicyHash',
  'hmacKeyVersion'
];

const STATUS = new Set(['persistent', 'introduced', 'changed', 'resolved', 'inconclusive', 'file_deleted']);

const sha256 = value => createHash('sha256').update(value).digest('hex');

function stable(value) {
  if (Array.isArray(value)) return `[${value.map(stable).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${stable(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

function deterministicId(seed) {
  const bytes = Buffer.from(sha256(seed).slice(0, 32), 'hex');
  bytes[6] = (bytes[6] & 0x0f) | 0x80;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = bytes.toString('hex');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function assertString(value, name) {
  if (typeof value !== 'string' || value.length === 0) throw new TypeError(`${name} must be a non-empty string`);
}

function isEnvelope(input) {
  return Boolean(input && typeof input === 'object' && 'bundle' in input);
}

function unwrap(input, side) {
  const bundle = isEnvelope(input) ? input.bundle : input;
  if (!bundle || !Array.isArray(bundle.scanResults) || !bundle.identity || !bundle.signature) {
    throw new TypeError(`${side} must be an AnalysisBundle or published-bundle envelope`);
  }
  const envelope = isEnvelope(input) ? input : null;
  validateBundle(bundle);
  for (const key of ['workspaceId', 'runId', 'snapshotId', 'workflowRevisionId']) assertUuid(bundle.identity[key], `${side} ${key}`);
  for (const key of ['artifactHash', 'snapshotManifestHash']) assertHash(bundle.identity[key], `${side} ${key}`);
  for (const key of SIGNATURE_KEYS) {
    const value = bundle.signature[key];
    if (key.startsWith('domainPolicy') && value === null) continue;
    if (key.endsWith('Hash')) assertHash(value, `${side} ${key}`);
    else assertString(value, `${side} ${key}`);
  }
  for (const part of [...bundle.scanResults, bundle.codeFacts]) {
    if (part.schemaVersion !== '0.1') throw new TypeError(`${side} unsupported schema version`);
    for (const f of part.findings ?? []) {
      if (f.fingerprintVersion !== 'fp/v1') throw new TypeError(`${side} unsupported fingerprint version`);
      assertUuid(f.id, `${side} finding id`);
      assertHash(f.fingerprint, `${side} fingerprint`);
      assertHash(f.anchor.normalizedContextHash, `${side} context hash`);
      assertString(f.anchor.symbolKey, `${side} symbol`);
      assertString(f.anchor.role, `${side} anchor role`);
    }
  }
  const manifest = envelope ? normalizeManifest(envelope.manifest, bundle, side) : null;
  if (envelope && envelope.provenance?.published !== true) {
    throw new TypeError(`${side} published-bundle envelope is not published`);
  }
  if (envelope) {
    assertHash(envelope.provenance.bundleHash, `${side} published bundle hash`);
    assertString(envelope.provenance.normalizationVersion, `${side} normalization version`);
    if (!['scanner', 'contract_scenario', 'manual_fixture'].includes(envelope.provenance.origin)) throw new TypeError(`${side} provenance origin required`);
  }
  if (bundle.schemaVersion !== '0.1') throw new TypeError(`${side} unsupported schema version`);
  return { bundle, envelope, manifest };
}

const assertHash = (value, name) => {
  if (!/^[0-9a-f]{64}$/.test(value ?? '')) throw new TypeError(`${name} must be SHA-256 lowercase hex`);
};
const assertUuid = (value, name) => {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value ?? '')) throw new TypeError(`${name} must be a canonical lowercase UUID`);
};
const order = (a, b) => Buffer.compare(Buffer.from(a), Buffer.from(b));

// PostgreSQL jsonb::text orders object keys by UTF-8 byte length, then bytes.
function pgJson(value) {
  if (Array.isArray(value)) return `[${value.map(pgJson).join(', ')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort((a, b) => Buffer.byteLength(a) - Buffer.byteLength(b) || order(a, b)).map(key => `${JSON.stringify(key)}: ${pgJson(value[key])}`).join(', ')}}`;
  }
  return JSON.stringify(value);
}

export function manifestHash(files) {
  return sha256(pgJson([...files].sort((a, b) => order(a.path, b.path)).map(({ path, sourceHash, byteLength, mediaType }) => ({ path, sourceHash, byteLength, mediaType }))));
}

function normalizeManifest(manifest, bundle, side) {
  if (!manifest || manifest.complete !== true || !Array.isArray(manifest.files)) {
    throw new TypeError(`${side} manifest must be complete and contain files`);
  }
  const files = new Map();
  for (const file of manifest.files) {
    assertString(file?.path, `${side} manifest path`);
    if (/(^\/|\/$|\\|(^|\/)\.{1,2}(\/|$)|\/\/|^[A-Za-z]:|[\x00-\x1f\x7f])/.test(file.path)) throw new TypeError(`${side} invalid manifest path`);
    assertHash(file?.sourceHash, `${side} source hash for ${file.path}`);
    if (!Number.isSafeInteger(file.byteLength) || file.byteLength < 0 || typeof file.mediaType !== 'string') throw new TypeError(`${side} invalid manifest file metadata`);
    if (files.has(file.path)) throw new TypeError(`${side} manifest contains duplicate path ${file.path}`);
    files.set(file.path, file);
  }
  if (manifest.workspaceId !== bundle.identity.workspaceId || manifest.snapshotId !== bundle.identity.snapshotId ||
    manifest.hash !== bundle.identity.snapshotManifestHash || manifestHash(manifest.files) !== manifest.hash) throw new TypeError(`${side} manifest identity/hash differs from immutable bundle`);
  for (const part of [...bundle.scanResults, bundle.codeFacts]) {
    for (const e of part.evidence) {
      const file = files.get(e.location.path);
      if (!file || file.sourceHash !== e.sourceHash || file.byteLength < e.location.byteEnd) throw new TypeError(`${side} evidence differs from manifest`);
    }
    if (part.coverage.scopes.some(scope => !files.has(scope.path))) throw new TypeError(`${side} coverage path missing from manifest`);
  }
  return files;
}

function signatureDiff(before, after) {
  return SIGNATURE_KEYS.filter(key => before.signature[key] !== after.signature[key]);
}

function allFindings(bundle) {
  return bundle.scanResults.flatMap(scan => scan.findings.map(finding => ({ finding, scan })));
}

function anchorKey(finding) {
  const a = finding.anchor;
  return stable([finding.category, finding.ruleId, a.normalizedPath, a.symbolKey, a.role]);
}

// A candidate can veto disappearance/introduction, never prove a renamed identity.
function symbolRenameKey(finding) {
  const a = finding.anchor;
  return stable([finding.category, finding.ruleId, a.normalizedPath, a.role, a.normalizedContextHash]);
}

function unmatchedScopeKey(finding) {
  return stable([finding.category, finding.ruleId, finding.anchor.normalizedPath, finding.anchor.role]);
}

function fingerprintKey(finding) {
  return finding.fingerprint;
}

function group(items, keyFn) {
  const result = new Map();
  for (const item of items) {
    const key = keyFn(item.finding);
    const list = result.get(key) ?? [];
    list.push(item);
    result.set(key, list);
  }
  return result;
}

function scanner(bundle, category) {
  return bundle.scanResults.find(scan => scan.category === category);
}

function completeOutcome(bundle, category) {
  const scan = scanner(bundle, category);
  if (!scan || scan.status !== 'complete' || scan.coverage.status !== 'complete') return false;
  const c = scan.coverage;
  return c.skippedScopes.length === 0 && c.parserErrors.length === 0 &&
    c.unresolvedSymbols.length === 0 && c.limitsReached.length === 0 &&
    c.scopes.every(scope => scope.status === 'checked');
}

function checkedScope(bundle, finding) {
  const scan = scanner(bundle, finding.category);
  if (!scan) return false;
  return scan.coverage.scopes.some(scope => scope.status === 'checked' &&
    scope.category === finding.category && scope.path === finding.anchor.normalizedPath && scope.ruleId === finding.ruleId);
}

function graphComplete(bundle) {
  const c = bundle.codeFacts?.coverage;
  return Boolean(c && c.status === 'complete' && c.skippedScopes.length === 0 &&
    c.parserErrors.length === 0 && c.unresolvedSymbols.length === 0 && c.limitsReached.length === 0 &&
    c.scopes.every(scope => scope.status === 'checked') &&
    bundle.codeFacts.edges.every(edge => edge.resolution === 'resolved'));
}

function itemId(comparisonId, status, baseline, current) {
  return deterministicId(stable([comparisonId, status, baseline?.finding.id ?? null, current?.finding.id ?? null]));
}

function coverageProblems(bundle, finding, side, requireScope = true) {
  const problems = [];
  const scan = scanner(bundle, finding.category);
  if (!completeOutcome(bundle, finding.category)) {
    problems.push(`${side}: анализ ${finding.category} не завершён полностью (${scan?.status ?? 'нет outcome'}); ошибки, пропуски и лимиты не доказывают отсутствие паттерна`);
    if (scan) {
      const details = [...scan.diagnostics.map(d => d.message), ...scan.coverage.scopes.filter(s => s.status !== 'checked').map(s => `${s.path}: ${s.reason}`), ...scan.coverage.limitsReached];
      if (details.length) problems.push(`${side}: ${details.join('; ')}`);
    }
  }
  if (requireScope && !checkedScope(bundle, finding)) problems.push(`${side}: нет checked для ${finding.anchor.normalizedPath} / ${finding.ruleId}`);
  if (!graphComplete(bundle)) {
    const unresolvedEdges = bundle.codeFacts.edges.filter(edge => edge.resolution !== 'resolved');
    const details = [
      ...bundle.codeFacts.coverage.skippedScopes,
      ...bundle.codeFacts.coverage.unresolvedSymbols,
      ...bundle.codeFacts.coverage.limitsReached,
      ...unresolvedEdges.map(edge => `связь графа ${edge.resolution === 'partial' ? 'разрешена частично' : 'не разрешена'}: ${edge.resolutionReason}`)
    ];
    problems.push(`${side}: покрытие графа неполное${details.length ? `; ${details.join('; ')}` : ''}`);
  }
  if (finding.coverageWarnings.length) problems.push('Находка содержит предупреждения о покрытии');
  return problems;
}

function makeItem(comparisonId, status, basis, reason, baseline, current) {
  if (!STATUS.has(status)) throw new Error(`unsupported comparison status: ${status}`);
  return {
    id: itemId(comparisonId, status, baseline, current),
    baselineFindingId: baseline?.finding.id ?? null,
    currentFindingId: current?.finding.id ?? null,
    status,
    matchingBasis: basis,
    reason
  };
}

function compareFindings(comparisonId, before, after, comparable, beforeManifest, afterManifest) {
  const baseline = allFindings(before);
  const current = allFindings(after);
  const beforeFp = group(baseline, fingerprintKey);
  const afterFp = group(current, fingerprintKey);
  const beforeAnchor = group(baseline, anchorKey);
  const afterAnchor = group(current, anchorKey);
  const beforeSymbolCandidates = group(baseline, symbolRenameKey);
  const afterSymbolCandidates = group(current, symbolRenameKey);
  const usedBefore = new Set();
  const usedAfter = new Set();
  const items = [];
  const add = (status, basis, reason, b, c) => {
    if (b) usedBefore.add(b.finding.id);
    if (c) usedAfter.add(c.finding.id);
    items.push(makeItem(comparisonId, status, basis, reason, b, c));
  };

  // Even distinct fingerprints cannot disambiguate repeated coarse anchors safely.
  for (const key of new Set([...beforeAnchor.keys(), ...afterAnchor.keys()])) {
    if ((beforeAnchor.get(key)?.length ?? 0) > 1 || (afterAnchor.get(key)?.length ?? 0) > 1) {
      for (const b of beforeAnchor.get(key) ?? []) if (!usedBefore.has(b.finding.id)) add('inconclusive', 'ambiguous', 'Несколько находок имеют одинаковый semantic anchor; выбрать одну достоверно нельзя', b);
      for (const c of afterAnchor.get(key) ?? []) if (!usedAfter.has(c.finding.id)) add('inconclusive', 'ambiguous', 'Несколько находок имеют одинаковый semantic anchor; выбрать одну достоверно нельзя', undefined, c);
    }
  }
  for (const fp of new Set([...beforeFp.keys(), ...afterFp.keys()])) {
    if ((beforeFp.get(fp)?.length ?? 0) > 1 || (afterFp.get(fp)?.length ?? 0) > 1) {
      for (const b of beforeFp.get(fp) ?? []) if (!usedBefore.has(b.finding.id)) add('inconclusive', 'ambiguous', 'Fingerprint повторяется; первый кандидат не выбирается', b);
      for (const c of afterFp.get(fp) ?? []) if (!usedAfter.has(c.finding.id)) add('inconclusive', 'ambiguous', 'Fingerprint повторяется; первый кандидат не выбирается', undefined, c);
    }
  }

  for (const [fp, bs] of beforeFp) {
    const cs = afterFp.get(fp) ?? [];
    if (bs.length === 1 && cs.length === 1 && !usedBefore.has(bs[0].finding.id) && !usedAfter.has(cs[0].finding.id)) {
      const gaps = [...coverageProblems(before, bs[0].finding, 'До'), ...coverageProblems(after, cs[0].finding, 'После')];
      const proven = comparable && gaps.length === 0;
      add(proven ? 'persistent' : 'inconclusive', 'unique_anchor',
        !comparable ? 'Основания анализа несовместимы или не подтверждены; совпадения fingerprint недостаточно' : gaps.join('; ') || 'Тот же уникальный fingerprint и anchor; сдвиг строк не меняет находку', bs[0], cs[0]);
    }
  }

  for (const [anchor, bs] of beforeAnchor) {
    const cs = afterAnchor.get(anchor) ?? [];
    const remainingB = bs.filter(x => !usedBefore.has(x.finding.id));
    const remainingC = cs.filter(x => !usedAfter.has(x.finding.id));
    if (bs.length === 1 && cs.length === 1 && remainingB.length === 1 && remainingC.length === 1) {
      const b = remainingB[0];
      const c = remainingC[0];
      const contextChanged = b.finding.anchor.normalizedContextHash !== c.finding.anchor.normalizedContextHash;
      const gaps = [...coverageProblems(before, b.finding, 'До'), ...coverageProblems(after, c.finding, 'После')];
      if (!comparable) add('inconclusive', 'none', 'Основания анализа несовместимы или не подтверждены; изменение anchor нельзя приписать правке', b, c);
      else if (gaps.length) add('inconclusive', 'unique_anchor', gaps.join('; '), b, c);
      else add('changed', 'unique_anchor', contextChanged ? 'Тот же уникальный coarse anchor, но изменился normalizedContextHash; это изменение, а не доказанное исправление' : 'Тот же уникальный coarse anchor, но изменилось основание находки', b, c);
    } else if (remainingB.length > 1 || remainingC.length > 1) {
      for (const b of remainingB) add('inconclusive', 'ambiguous', 'Повторяющийся anchor; выбрать кандидата достоверно нельзя', b, undefined);
      for (const c of remainingC) add('inconclusive', 'ambiguous', 'Повторяющийся anchor; выбрать кандидата достоверно нельзя', undefined, c);
    }
  }

  const unmatchedBefore = group(baseline.filter(x => !usedBefore.has(x.finding.id)), unmatchedScopeKey);
  const unmatchedAfter = group(current.filter(x => !usedAfter.has(x.finding.id)), unmatchedScopeKey);
  for (const [scope, bs] of unmatchedBefore) {
    const cs = unmatchedAfter.get(scope);
    if (!cs) continue;
    const reason = 'Возможное изменение или перенос между символами в том же file/rule/role; identity mapping B отсутствует, исчезновение и появление не доказаны';
    for (const b of bs) add('inconclusive', 'ambiguous', reason, b);
    for (const c of cs) add('inconclusive', 'ambiguous', reason, undefined, c);
  }

  for (const b of baseline.filter(x => !usedBefore.has(x.finding.id))) {
    const path = b.finding.anchor.normalizedPath;
    const pathGone = beforeManifest?.has(path) && afterManifest && !afterManifest.has(path);
    const gaps = [...coverageProblems(before, b.finding, 'До'), ...coverageProblems(after, b.finding, 'После')];
    if (pathGone) add('file_deleted', 'none', 'Путь отсутствует в полном current manifest; удаление отдельно от исправления, переименование не исключено', b, undefined);
    else if (!comparable) add('inconclusive', 'none', 'Основания анализа несовместимы или не подтверждены; исчезновение не доказывает исправление', b, undefined);
    else if (!beforeManifest?.has(path) || !afterManifest?.has(path)) add('inconclusive', 'none', 'Нет двух полных manifests, подтверждающих сохранение файла', b);
    else if (afterSymbolCandidates.get(symbolRenameKey(b.finding))?.some(x => x.finding.anchor.symbolKey !== b.finding.anchor.symbolKey)) {
      add('inconclusive', 'ambiguous', 'Тот же паттерн найден в другом symbolKey; возможно переименование символа, без утверждённой карты исчезновение не доказано', b);
    } else if (gaps.length) {
      add('inconclusive', 'none', gaps.join('; '), b, undefined);
    } else {
      const sameAnchor = afterAnchor.has(anchorKey(b.finding));
      add(sameAnchor ? 'inconclusive' : 'resolved', sameAnchor ? 'ambiguous' : 'unique_anchor',
        sameAnchor ? 'На том же semantic anchor остаётся находка; resolved запрещён' : 'Файл сохранился; оба file/rule scope полностью checked, текущий паттерн отсутствует', b, undefined);
    }
  }

  for (const c of current.filter(x => !usedAfter.has(x.finding.id))) {
    const path = c.finding.anchor.normalizedPath;
    const baselinePathExists = beforeManifest?.has(path);
    const gaps = [...coverageProblems(before, c.finding, 'До', Boolean(baselinePathExists)), ...coverageProblems(after, c.finding, 'После')];
    const possibleRename = beforeManifest && [...beforeManifest.keys()].some(oldPath => !afterManifest?.has(oldPath));
    if (!comparable) add('inconclusive', 'none', 'Основания анализа несовместимы или не подтверждены; появление не доказано', undefined, c);
    else if (!beforeManifest || !afterManifest) add('inconclusive', 'none', 'Без двух полных manifests нельзя отличить новый файл от непроверенного baseline scope', undefined, c);
    else if (beforeAnchor.has(anchorKey(c.finding))) add('inconclusive', 'ambiguous', 'На том же baseline anchor уже была находка; introduced запрещён', undefined, c);
    else if (beforeSymbolCandidates.get(symbolRenameKey(c.finding))?.some(x => x.finding.anchor.symbolKey !== c.finding.anchor.symbolKey)) {
      add('inconclusive', 'ambiguous', 'Тот же паттерн был в другом symbolKey; возможно переименование символа, без утверждённой карты появление не доказано', undefined, c);
    } else if (!baselinePathExists && possibleRename) add('inconclusive', 'none', 'Одновременно исчезли старые пути и появился новый; без явной карты переименований появление не доказано', undefined, c);
    else if (gaps.length) {
      add('inconclusive', 'none', gaps.join('; '), undefined, c);
    } else {
      add('introduced', 'unique_anchor', baselinePathExists ? 'Новая находка в scope, полностью проверенном до и после' : 'Находка в доказуемо новом manifest path (new_scope); baseline category полностью проверена', undefined, c);
    }
  }
  items.sort((a, b) => order(a.id, b.id));
  return items;
}

function deletedFiles(beforeManifest, afterManifest) {
  if (!beforeManifest || !afterManifest) return [];
  return [...beforeManifest.entries()]
    .filter(([path]) => !afterManifest.has(path))
    .map(([path, file]) => ({ path, baselineSourceHash: file.sourceHash, reason: 'Путь отсутствует в полном current manifest; это не доказательство исправления или отсутствия rename' }))
    .sort((a, b) => order(a.path, b.path));
}

/** Compare two immutable bundles. This function never runs a scanner. */
export function compareBundles(baselineInput, currentInput, options = {}) {
  const before = unwrap(baselineInput, 'baseline');
  const after = unwrap(currentInput, 'current');
  const bIdentity = before.bundle.identity;
  const cIdentity = after.bundle.identity;
  if (bIdentity.workspaceId !== cIdentity.workspaceId) throw new Error('Bundles belong to different workspaces');
  if (bIdentity.runId === cIdentity.runId || bIdentity.snapshotId === cIdentity.snapshotId) throw new Error('Baseline and current runs/snapshots must be distinct');
  const comparisonId = options.id ?? deterministicId(stable(['comparison/v1', bIdentity.workspaceId, bIdentity.runId, bIdentity.snapshotId, cIdentity.runId, cIdentity.snapshotId]));
  assertUuid(comparisonId, 'comparison id');
  const signatureChanges = signatureDiff(before.bundle, after.bundle);
  const reasons = signatureChanges.length ? [`Различаются статические версии/hashes: ${signatureChanges.join(', ')}`] : [];
  if (!before.envelope || !after.envelope) reasons.push('Нужны published envelopes с manifests и версией нормализации; raw AnalysisBundle не доказывает публикацию');
  else {
    const bp = before.envelope.provenance;
    const cp = after.envelope.provenance;
    if (bp.normalizationVersion !== cp.normalizationVersion) reasons.push('Различаются версии нормализации fingerprint/context');
    if (bp.origin !== cp.origin || bp.origin === 'manual_fixture') reasons.push('Ручная fixture или разные источники не являются двумя сопоставимыми scanner runs');
    if (bp.expectedScopesVerified !== true || cp.expectedScopesVerified !== true) reasons.push('A/B не подтвердили полноту ожидаемых scopes по manifest/config');
    if ([bp, cp].some(p => !['not_applicable', 'applied'].includes(p.domainPolicyApplicability))) reasons.push('Применимость domain policy не подтверждена');
    if ([before, after].some(x => (x.envelope.provenance.domainPolicyApplicability === 'applied') !== (x.bundle.signature.domainPolicyRevisionId !== null))) reasons.push('Применимость domain policy противоречит signature');
  }
  const beforeRules = group(allFindings(before.bundle), f => stable([f.category, f.ruleId]));
  const afterRules = group(allFindings(after.bundle), f => stable([f.category, f.ruleId]));
  for (const key of new Set([...beforeRules.keys(), ...afterRules.keys()])) {
    const versions = new Set([...(beforeRules.get(key) ?? []), ...(afterRules.get(key) ?? [])].map(x => stable([x.finding.ruleVersion, x.finding.ruleFamily])));
    if (versions.size > 1) reasons.push(`Не совпадают ruleVersion/ruleFamily для ${key}`);
  }
  if (before.envelope?.provenance.origin === 'contract_scenario' || after.envelope?.provenance.origin === 'contract_scenario') reasons.push('contract scenario, not scanner output: синтетические DTO, не настоящий scan и не доказательство fix');
  // comparable is the inverse of every prohibitory reason. Keep this after all
  // reason pushes: provenance/synthetic checks must not be able to leave a
  // comparable=true result that still has a blocking reason.
  const comparable = reasons.length === 0;
  reasons.sort(order);
  const items = compareFindings(comparisonId, before.bundle, after.bundle, comparable, before.manifest, after.manifest);
  return {
    workspaceId: bIdentity.workspaceId,
    id: comparisonId,
    baselineRunId: bIdentity.runId,
    baselineSnapshotId: bIdentity.snapshotId,
    currentRunId: cIdentity.runId,
    currentSnapshotId: cIdentity.snapshotId,
    comparable,
    reasons,
    items,
    deletedFiles: deletedFiles(before.manifest, after.manifest)
  };
}

export { deterministicId };
