import type { WorkflowDraft } from '../types';

export type ScenarioDraft = WorkflowDraft;

export interface ScenarioPreview {
  draft: ScenarioDraft;
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
  return payload as ScenarioPreview;
}
