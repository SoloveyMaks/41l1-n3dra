import type { Severity } from '../types';

export type CustomCheckSourceKind = 'script' | 'template';

export interface CustomCheckSpec {
  name: string;
  version: string;
  language: 'python';
  runner: 'bwrap-python3';
  sourceKind: CustomCheckSourceKind;
  script: string;
  templateId?: string | null;
  timeoutSeconds: number;
  memoryMb: number;
  maxOutputBytes: number;
}

export interface CustomCheckDefinition {
  id: string;
  name: string;
  version: string;
  language: 'python';
  runner: 'bwrap-python3';
  sourceKind: CustomCheckSourceKind;
  templateId?: string | null;
  sourceSha256: string;
  timeoutSeconds: number;
  memoryMb: number;
  maxOutputBytes: number;
  createdBy: string;
  createdAt?: string | null;
  updatedAt?: string | null;
  lastValidatedAt?: string | null;
}

export interface CustomCheckPreview {
  definition: CustomCheckDefinition;
  inputContract: string;
  outputContract: string;
  warnings: string[];
}

export interface CustomFinding {
  id: string;
  origin: 'custom_check';
  ruleId: string;
  severity: Severity;
  message: string;
  path: string;
  startLine: number;
  endLine: number;
}

export interface CustomCheckRunResult {
  id: string;
  definitionId: string;
  artifactId?: string | null;
  status: 'succeeded' | 'failed' | 'timeout' | 'output_limit' | 'contract_error' | 'error' | string;
  exitCode?: number | null;
  durationMs: number;
  stdout: string;
  stderr: string;
  summary: string;
  findings: CustomFinding[];
  diagnostics: Array<{ code?: string; message?: string }>;
  limits: { timeoutSeconds?: number; memoryMb?: number; maxOutputBytes?: number };
  countsAsFindings: false;
  origin: 'custom_check';
}

export interface CustomCheckValidation {
  valid: boolean;
  preview: CustomCheckPreview;
  run: CustomCheckRunResult;
}

export interface CustomCheckTemplate {
  id: string;
  title: string;
  language: 'python';
  runner: 'bwrap-python3';
}

export interface CustomCheckRunSummary {
  id: string;
  definitionId: string;
  definitionName: string;
  definitionVersion: string;
  status: string;
  durationMs: number;
  createdAt?: string | null;
  findings: CustomFinding[];
  countsAsFindings: false;
  origin: 'custom_check';
}

export interface CustomCheckStatus {
  available: boolean;
  language: 'python';
  runner: 'bwrap-python3';
  reason: string;
}

export class CustomCheckApiError extends Error {
  status: number;
  code?: string;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = 'CustomCheckApiError';
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

async function request<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}/custom-checks${path}`, {
    credentials: 'include',
    cache: 'no-store',
    ...init,
  });
  const payload = await response.json().catch(() => ({}));
  if (response.status === 401) {
    throw new CustomCheckApiError('Требуется вход в аккаунт', 401, 'AUTH_REQUIRED');
  }
  if (!response.ok) {
    const detail = (payload as { detail?: { code?: string } }).detail;
    throw new CustomCheckApiError(
      detailText(payload) || `HTTP ${response.status}`,
      response.status,
      typeof detail?.code === 'string' ? detail.code : undefined,
    );
  }
  return payload as T;
}

export async function listCustomChecks(signal?: AbortSignal): Promise<CustomCheckDefinition[]> {
  return request<CustomCheckDefinition[]>('', { method: 'GET', signal });
}

export async function getCustomCheckStatus(signal?: AbortSignal): Promise<CustomCheckStatus> {
  return request<CustomCheckStatus>('/status', { method: 'GET', signal });
}

export async function listCustomFindings(
  workflowRunId: string,
  signal?: AbortSignal,
): Promise<CustomCheckRunSummary[]> {
  return request<CustomCheckRunSummary[]>(
    `/runs/${encodeURIComponent(workflowRunId)}/findings`,
    { method: 'GET', signal },
  );
}

export async function listCustomCheckTemplates(signal?: AbortSignal): Promise<CustomCheckTemplate[]> {
  return request<CustomCheckTemplate[]>('/templates', { method: 'GET', signal });
}

export async function previewCustomCheck(spec: CustomCheckSpec, signal?: AbortSignal): Promise<CustomCheckPreview> {
  return request<CustomCheckPreview>('/preview', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(spec),
    signal,
  });
}

export async function validateCustomCheck(spec: CustomCheckSpec, signal?: AbortSignal): Promise<CustomCheckValidation> {
  return request<CustomCheckValidation>('/validate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(spec),
    signal,
  });
}

export async function createCustomCheck(spec: CustomCheckSpec, signal?: AbortSignal): Promise<CustomCheckDefinition> {
  return request<CustomCheckDefinition>('', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(spec),
    signal,
  });
}

export async function runCustomCheck(
  definitionId: string,
  artifactId: string,
  signal?: AbortSignal,
): Promise<CustomCheckRunResult> {
  return request<CustomCheckRunResult>(`/${encodeURIComponent(definitionId)}/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ artifactId }),
    signal,
  });
}
