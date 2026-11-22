import type { NodeKind, WorkflowDraft } from '../types';

export type ScenarioDraft = WorkflowDraft;

export interface ScenarioPreview {
  draft: ScenarioDraft;
  validation: { valid: boolean; issues: { level: string; message: string }[] };
  summary: string;
  warnings: string[];
  source: 'ai' | string;
  intents: string[];
}

export class ScenarioBuilderApiError extends Error {
  status: number;
  code?: string;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = 'ScenarioBuilderApiError';
    this.status = status;
    this.code = code;
  }
}

const configuredBase = import.meta.env.VITE_API_BASE_URL?.trim();
const API_BASE = configuredBase ? configuredBase.replace(/\/+$/, '') : '/api';

function detailText(payload: unknown): string {
  if (!payload || typeof payload !== 'object') return '';
  const detail = (payload as { detail?: unknown }).detail;
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object') {
    const item = detail as { message?: unknown; code?: unknown };
    if (typeof item.message === 'string') return item.message;
    if (typeof item.code === 'string') return item.code;
  }
  return '';
}

interface NodePortContract {
  inputs: readonly string[];
  outputs: readonly string[];
}

// Mirrors backend/app/contracts/node_registry.py. A preview naming a kind or
// port absent here is not a graph the canvas can render, so it is rejected
// before Apply can touch the current draft.
const NODE_PORTS: Record<NodeKind, NodePortContract> = {
  manual_trigger: { inputs: [], outputs: ['out'] },
  schedule_trigger: { inputs: [], outputs: ['out'] },
  project_input: { inputs: ['trigger'], outputs: ['artifact'] },
  secret_scan: { inputs: ['artifact'], outputs: ['scan_result'] },
  sca_scan: { inputs: ['artifact'], outputs: ['scan_result'] },
  kotlin_sast: { inputs: ['artifact'], outputs: ['scan_result'] },
  merge_findings: { inputs: ['scan_1', 'scan_2', 'scan_3'], outputs: ['findings_bundle'] },
  branch_merge: { inputs: ['branch_a', 'branch_b'], outputs: ['out'] },
  condition: { inputs: ['in'], outputs: ['true_branch', 'false_branch'] },
  policy_gate: { inputs: ['findings_bundle'], outputs: ['policy_result'] },
  ai_explain: { inputs: ['findings_bundle'], outputs: ['explain_result'] },
  ai_scan: { inputs: ['artifact', 'findings_bundle'], outputs: ['ai_scan_result'] },
  build_report: { inputs: ['findings_bundle', 'policy_result', 'explain_result', 'ai_scan_result'], outputs: ['report'] },
  notify: { inputs: ['report'], outputs: ['delivery_result'] },
};

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function isValidation(value: unknown): value is ScenarioPreview['validation'] {
  if (!isObject(value) || typeof value.valid !== 'boolean' || !Array.isArray(value.issues)) return false;
  return value.issues.every((issue) => isObject(issue)
    && typeof issue.level === 'string' && typeof issue.message === 'string');
}

function isPreview(payload: unknown): payload is ScenarioPreview {
  if (!isObject(payload)) return false;
  const { draft, validation, summary, warnings, intents } = payload;
  if (!isObject(draft) || draft.schemaVersion !== 1
    || typeof draft.name !== 'string' || typeof draft.description !== 'string'
    || !Array.isArray(draft.nodes) || !draft.nodes.length || !Array.isArray(draft.edges)) return false;
  if (!isValidation(validation) || validation.valid !== true) return false;
  // valid=true paired with an error issue is self-contradictory: refuse to
  // advertise "graph verified" and never allow Apply.
  if (validation.issues.some((issue) => issue.level !== 'warning' && issue.level !== 'info')) return false;
  if (typeof summary !== 'string' || !Array.isArray(warnings)
    || !warnings.every((warning) => typeof warning === 'string')
    || !Array.isArray(intents) || !intents.every((intent) => typeof intent === 'string')) return false;

  const nodes = new Map<string, NodePortContract>();
  for (const node of draft.nodes) {
    if (!isObject(node) || typeof node.id !== 'string' || !node.id || nodes.has(node.id)
      || typeof node.title !== 'string' || !isObject(node.config)) return false;
    const kind = node.kind;
    if (typeof kind !== 'string' || !Object.prototype.hasOwnProperty.call(NODE_PORTS, kind)) return false;
    nodes.set(node.id, NODE_PORTS[kind as NodeKind]);
    const position = node.position;
    if (!isObject(position) || !isFiniteNumber(position.x) || !isFiniteNumber(position.y)) return false;
    if (node.version !== undefined && (!isFiniteNumber(node.version) || node.version < 1)) return false;
  }

  const edgeIds = new Set<string>();
  for (const edge of draft.edges) {
    if (!isObject(edge) || typeof edge.id !== 'string' || !edge.id || edgeIds.has(edge.id)) return false;
    edgeIds.add(edge.id);
    if (typeof edge.source !== 'string' || typeof edge.target !== 'string') return false;
    const sourcePorts = nodes.get(edge.source);
    const targetPorts = nodes.get(edge.target);
    if (!sourcePorts || !targetPorts) return false;
    if (typeof edge.sourcePort !== 'string' || !sourcePorts.outputs.includes(edge.sourcePort)) return false;
    if (typeof edge.targetPort !== 'string' || !targetPorts.inputs.includes(edge.targetPort)) return false;
  }
  return true;
}

async function requestDraftValidation(
  draft: ScenarioDraft,
  signal?: AbortSignal,
): Promise<ScenarioPreview['validation']> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/scenario-builder/validate`, {
      method: 'POST',
      credentials: 'include',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(draft),
      signal,
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new ScenarioBuilderApiError(
      'Серверная проверка черновика недоступна. Применение отключено, текущий граф не изменён.',
      0,
      'SCENARIO_PREVIEW_UNVERIFIED',
    );
  }
  const payload = await response.json().catch(() => ({}));
  if (response.status === 401) {
    throw new ScenarioBuilderApiError('Требуется вход в аккаунт', 401, 'AUTH_REQUIRED');
  }
  if (!response.ok) {
    throw new ScenarioBuilderApiError(
      response.status === 422
        ? 'Сервер отклонил форму черновика. Применение отключено, текущий граф не изменён.'
        : 'Серверная проверка черновика недоступна. Применение отключено, текущий граф не изменён.',
      response.status,
      'SCENARIO_PREVIEW_UNVERIFIED',
    );
  }
  if (!isValidation(payload)) {
    throw new ScenarioBuilderApiError(
      'Сервер вернул неоднозначный результат проверки. Применение отключено.',
      422,
      'SCENARIO_PREVIEW_UNVERIFIED',
    );
  }
  if (!payload.valid) {
    const firstError = payload.issues.find((issue) => issue.level === 'error');
    throw new ScenarioBuilderApiError(
      firstError
        ? `Черновик не прошёл серверную проверку: ${firstError.message}`
        : 'Черновик не прошёл серверную проверку. Применение отключено.',
      422,
      'SCENARIO_PREVIEW_INVALID',
    );
  }
  if (payload.issues.some((issue) => issue.level !== 'warning' && issue.level !== 'info')) {
    throw new ScenarioBuilderApiError(
      'Сервер вернул неоднозначный результат проверки. Применение отключено.',
      422,
      'SCENARIO_PREVIEW_UNVERIFIED',
    );
  }
  return payload;
}

export async function requestScenarioPreview(prompt: string, signal?: AbortSignal): Promise<ScenarioPreview> {
  const response = await fetch(`${API_BASE}/scenario-builder/preview`, {
    method: 'POST',
    credentials: 'include',
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt }),
    signal,
  });
  const payload = await response.json().catch(() => ({}));
  if (response.status === 401) {
    throw new ScenarioBuilderApiError('Требуется вход в аккаунт', 401, 'AUTH_REQUIRED');
  }
  if (!response.ok) {
    const detail = (payload as { detail?: { code?: string } }).detail;
    const code = typeof detail?.code === 'string' ? detail.code : undefined;
    throw new ScenarioBuilderApiError(
      detailText(payload) || `HTTP ${response.status}`,
      response.status,
      code,
    );
  }
  if (!isPreview(payload)) {
    throw new ScenarioBuilderApiError(
      'Сервер вернул непроверенный или повреждённый черновик. Повторите сборку.',
      422,
      'SCENARIO_PREVIEW_INVALID',
    );
  }
  // The preview crossed an untrusted boundary: re-run the authoritative server
  // validator on the concrete draft before the UI may claim success or Apply.
  const validation = await requestDraftValidation(payload.draft, signal);
  return { ...payload, validation };
}
