import type {
  Finding,
  Location,
  RunScope,
  SecurityMapDto,
} from '../../../docs/data-contracts/contracts.ts';
import type {
  EvidenceRequest,
  SourceState,
} from '../../../ui/security-map/src/types.ts';
import { API_BASE } from './client';
import { productionModel, validateProduction } from '../../../ui/security-map/src/model';

export type RunSecurityMap = {
  identity: RunScope;
  map: SecurityMapDto;
  findings: Finding[];
};

const EVIDENCE_CODES: Record<string, true> = {
  evidence_not_available: true,
  source_changed: true,
  source_unavailable: true,
  source_redaction_incomplete: true,
};

export class SecurityMapApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(message: string, status: number, code: string) {
    super(message);
    this.name = 'SecurityMapApiError';
    this.status = status;
    this.code = code;
  }
}

const isLocation = (value: unknown): value is Location => {
  if (typeof value !== 'object' || value === null) return false;
  const coordinates = value as Record<string, unknown>;
  if (typeof coordinates.path !== 'string') return false;
  return ['line', 'column', 'endLine', 'endColumn', 'byteStart', 'byteEnd'].every(
    (key) => typeof coordinates[key] === 'number',
  );
};

const errorCodeFrom = async (res: Response): Promise<string> => {
  const body = await res.json().catch(() => null);
  if (typeof body === 'object' && body !== null) {
    const payload = body as Record<string, unknown>;
    const candidate = payload.code ?? payload.detail;
    if (typeof candidate === 'string' && EVIDENCE_CODES[candidate])
      return candidate;
  }
  if (res.status === 404) return 'evidence_not_available';
  if (res.status === 503) return 'source_unavailable';
  return 'evidence_not_available';
};

export async function getRunSecurityMap(
  runId: string,
  signal?: AbortSignal,
): Promise<RunSecurityMap> {
  const res = await fetch(
    `${API_BASE}/runs/${encodeURIComponent(runId)}/security-map`,
    { signal, credentials: 'include', cache: 'no-store', headers: { Accept: 'application/json' } },
  );
  if (!res.ok) {
    throw new SecurityMapApiError(
      `Карта недоступна (HTTP ${res.status})`,
      res.status,
      res.status === 503 ? 'source_unavailable' : 'map_load_failed',
    );
  }
  const body: unknown = await res.json().catch(() => null);
  if (typeof body !== 'object' || body === null || Array.isArray(body)) {
    throw new SecurityMapApiError(
      'Ответ security-map не соответствует контракту',
      res.status,
      'map_contract_mismatch',
    );
  }
  const envelope = body as Record<string, unknown>;
  if (
    typeof envelope.identity !== 'object' ||
    envelope.identity === null ||
    typeof envelope.map !== 'object' ||
    envelope.map === null ||
    !Array.isArray(envelope.findings)
  ) {
    throw new SecurityMapApiError(
      'Ответ security-map не соответствует контракту',
      res.status,
      'map_contract_mismatch',
    );
  }
  // The envelope fields were checked above; the component's production gate
  // validates every nested record against the published contract.
  const identity = envelope.identity as RunScope;
  const map = envelope.map as SecurityMapDto;
  const findings = envelope.findings as Finding[];
  const isObject = (value: unknown): value is Record<string, unknown> =>
    typeof value === 'object' && value !== null;
  if (
    identity.runId !== runId ||
    !map.identity ||
    !Array.isArray(map.nodes) ||
    !Array.isArray(map.edges) ||
    !Array.isArray(map.evidence) ||
    !Array.isArray(map.diagnostics) ||
    !Array.isArray(map.policyInputs) ||
    !Array.isArray(map.findingRefs) ||
    !Array.isArray(map.findingNodeLinks) ||
    !map.coverage ||
    !map.nodes.every(isObject) ||
    !map.edges.every(isObject) ||
    !map.evidence.every(isObject) ||
    !map.diagnostics.every(isObject) ||
    !map.policyInputs.every(isObject) ||
    !map.findingNodeLinks.every(isObject) ||
    !findings.every(isObject)
  ) {
    throw new SecurityMapApiError(
      'Ответ security-map неполный или относится к другому запуску',
      res.status,
      'map_contract_mismatch',
    );
  }
  try {
    const issue = validateProduction(identity, map, findings);
    if (issue) throw new Error(issue);
    productionModel(map, findings);
  } catch {
    throw new SecurityMapApiError(
      'Карта не прошла проверку снимка или содержит повреждённые данные',
      res.status,
      'map_contract_mismatch',
    );
  }
  return { identity, map, findings };
}

const hasForeignScope = (expected: RunScope, items: readonly unknown[]): boolean =>
  items.some((item) => {
    if (typeof item !== 'object' || item === null) return true;
    const { workspaceId, runId, snapshotId } = item as Record<string, unknown>;
    return workspaceId !== expected.workspaceId ||
      runId !== expected.runId ||
      snapshotId !== expected.snapshotId;
  });

/**
 * Comparison items reference canonical finding IDs of one published envelope.
 * The response is usable only when the envelope, the map and every nested
 * record belong to the exact workspace/run/snapshot of the compared side.
 * A foreign or nested finding ID must never resolve a canonical finding, nor
 * satisfy the "missing from envelope" check.
 */
export function validateComparisonScope(
  expected: RunScope,
  payload: RunSecurityMap,
): string | null {
  const { identity } = payload;
  if (
    identity.workspaceId !== expected.workspaceId ||
    identity.runId !== expected.runId ||
    identity.snapshotId !== expected.snapshotId
  )
    return 'identity';
  const mapIdentity = payload.map.identity;
  if (
    mapIdentity.workspaceId !== expected.workspaceId ||
    mapIdentity.runId !== expected.runId ||
    mapIdentity.snapshotId !== expected.snapshotId
  )
    return 'map';
  if (hasForeignScope(expected, payload.map.nodes)) return 'node';
  if (hasForeignScope(expected, payload.map.edges)) return 'edge';
  if (hasForeignScope(expected, payload.map.evidence)) return 'evidence';
  if (hasForeignScope(expected, payload.map.diagnostics)) return 'diagnostic';
  if (hasForeignScope(expected, payload.findings)) return 'finding';
  if (payload.map.policyInputs.some((input) => input.workspaceId !== expected.workspaceId)) {
    return 'policy';
  }
  return null;
}

/**
 * Backend returns SourceState as declared by ui/security-map. We only verify
 * that the payload belongs to the requested scope and evidence; the fragment
 * itself is never derived from the map's redactedSnippet.
 */
export async function getRunEvidence(
  request: EvidenceRequest,
  signal?: AbortSignal,
): Promise<SourceState> {
  const res = await fetch(
    `${API_BASE}/runs/${encodeURIComponent(request.runId)}/evidence/${encodeURIComponent(request.evidenceId)}`,
    { signal, credentials: 'include', cache: 'no-store', headers: { Accept: 'application/json' } },
  );
  if (!res.ok) {
    throw new SecurityMapApiError(
      'Исходник недоступен',
      res.status,
      await errorCodeFrom(res),
    );
  }
  const body: unknown = await res.json().catch(() => null);
  if (typeof body !== 'object' || body === null || Array.isArray(body)) {
    throw new SecurityMapApiError(
      'Ответ evidence не соответствует контракту',
      res.status,
      'evidence_not_available',
    );
  }
  const payload = body as Record<string, unknown>;
  if (typeof payload.request === 'object' && payload.request !== null) {
    const echoed = payload.request as EvidenceRequest;
    if (
      echoed.workspaceId !== request.workspaceId ||
      echoed.runId !== request.runId ||
      echoed.snapshotId !== request.snapshotId ||
      echoed.evidenceId !== request.evidenceId
    ) {
      throw new SecurityMapApiError(
        'Ответ evidence относится к другому запросу',
        res.status,
        'evidence_not_available',
      );
    }
  }
  if (payload.status === 'ready') {
    if (
      typeof payload.sourceHash !== 'string' ||
      typeof payload.maskedFragment !== 'string' ||
      typeof payload.redactionVersion !== 'string' ||
      !isLocation(payload.location)
    ) {
      throw new SecurityMapApiError(
        'Ответ evidence неполный',
        res.status,
        'evidence_not_available',
      );
    }
    return {
      status: 'ready',
      request,
      sourceHash: payload.sourceHash,
      location: payload.location,
      maskedFragment: payload.maskedFragment,
      redactionVersion: payload.redactionVersion,
    };
  }
  if (payload.status === 'error' || payload.status === 'unavailable') {
    const code =
      typeof payload.code === 'string' && EVIDENCE_CODES[payload.code]
        ? payload.code
        : 'evidence_not_available';
    return { status: payload.status, request, code };
  }
  throw new SecurityMapApiError(
    'Неизвестное состояние evidence',
    res.status,
    'evidence_not_available',
  );
}
