import {
  ArtifactManifest,
  ComparisonDto,
  Finding,
  NodeDefinition,
  NodeRunRecord,
  ReportData,
  RunSummary,
  SessionState,
  WorkflowDraft
} from '../types';

/**
 * Same-origin by default. Dev can set VITE_API_BASE, but the public judge build
 * must keep `/api` so the HttpOnly session cookie is sent to this origin.
 */
const API_BASE = (((import.meta as any).env?.VITE_API_BASE as string | undefined) || '/api')
  .replace(/\/+$/, '');

export class AuthRequiredError extends Error {
  authRequired = true;
  constructor(message = 'Требуется вход по коду доступа') {
    super(message);
    this.name = 'AuthRequiredError';
  }
}

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers || {});
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    credentials: 'include',
    cache: 'no-store',
  });
  if (res.status === 401) {
    const payload = await res.json().catch(() => ({}));
    throw new AuthRequiredError(detailText(payload) || 'Требуется вход по коду доступа');
  }
  if (!res.ok) {
    const payload = await res.json().catch(() => ({}));
    throw new Error(detailText(payload) || `HTTP ${res.status}`);
  }
  return res;
}

function detailText(payload: any): string {
  if (typeof payload?.detail === 'string') return payload.detail;
  if (payload?.detail && typeof payload.detail === 'object') {
    return payload.detail.message || payload.detail.code || '';
  }
  return '';
}

function jsonInit(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body, (_, value) => (typeof value === 'undefined' ? null : value)),
  };
}

export const apiClient = {
  async getSession(): Promise<SessionState> {
    return (await request('/session')).json();
  },

  async login(accessCode: string): Promise<SessionState> {
    // Код живёт только в состоянии формы; URL/localStorage его не касаются.
    return (await request('/session', jsonInit('POST', { accessCode }))).json();
  },

  async logout(): Promise<void> {
    await request('/session/logout', { method: 'POST' });
  },

  async getNodeTypes(): Promise<NodeDefinition[]> {
    return (await request('/node-types')).json();
  },

  async uploadArtifact(file: File): Promise<ArtifactManifest> {
    const formData = new FormData();
    formData.append('file', file);
    return (await request('/artifacts', { method: 'POST', body: formData })).json();
  },

  async loadSampleArtifact(): Promise<ArtifactManifest> {
    return (await request('/artifacts/sample', { method: 'POST' })).json();
  },

  async listArtifacts(): Promise<ArtifactManifest[]> {
    return (await request('/artifacts')).json();
  },

  async listWorkflows(): Promise<any[]> {
    return (await request('/workflows')).json();
  },

  async getWorkflow(workflowId: string): Promise<any> {
    return (await request(`/workflows/${workflowId}`)).json();
  },

  async saveWorkflowDraft(workflowId: string, draft: WorkflowDraft): Promise<any> {
    return (await request(`/workflows/${workflowId}/draft`, jsonInit('PUT', draft))).json();
  },

  async validateWorkflow(workflowId: string, draft?: WorkflowDraft): Promise<{ valid: boolean; issues: any[] }> {
    return (await request(
      `/workflows/${workflowId}/validate`,
      draft ? jsonInit('POST', draft) : { method: 'POST' },
    )).json();
  },

  async triggerRun(workflowId: string, artifactId?: string, triggerType = 'manual'): Promise<{ runId: string }> {
    return (await request(
      `/workflows/${workflowId}/runs`,
      jsonInit('POST', { artifactId, triggerType }),
    )).json();
  },

  async listRuns(): Promise<RunSummary[]> {
    return (await request('/runs')).json();
  },

  async getRun(runId: string): Promise<RunSummary> {
    return (await request(`/runs/${runId}`)).json();
  },

  async getRunNodes(runId: string): Promise<NodeRunRecord[]> {
    return (await request(`/runs/${runId}/nodes`)).json();
  },

  async getRunFindings(runId: string): Promise<Finding[]> {
    return (await request(`/runs/${runId}/findings`)).json();
  },

  async getRunReport(runId: string): Promise<ReportData> {
    return (await request(`/runs/${runId}/report`)).json();
  },

  async listSchedules(): Promise<any[]> {
    return (await request('/schedules')).json();
  },

  async createSchedule(workflowId: string, artifactId: string, cronOrInterval: string): Promise<any> {
    return (await request(
      '/schedules',
      jsonInit('POST', { workflowId, artifactId, cronOrInterval }),
    )).json();
  },

  async runAiSyntheticTest(runId?: string): Promise<any> {
    return (await request('/ai/synthetic-test', jsonInit('POST', { runId }))).json();
  },

  async compareRuns(baselineRunId: string, currentRunId: string): Promise<ComparisonDto> {
    return (await request(`/comparisons/${baselineRunId}/${currentRunId}`)).json();
  },

  getOfficialJsonUrl(runId: string): string {
    return `${API_BASE}/runs/${runId}/report/official.json`;
  },

  getReadableMarkdownUrl(runId: string): string {
    return `${API_BASE}/runs/${runId}/report/readable.md`;
  },

  createEventSource(runId: string, afterSeq = 0): EventSource {
    return new EventSource(`${API_BASE}/runs/${runId}/events?after=${afterSeq}`, {
      withCredentials: true,
    });
  }
};
