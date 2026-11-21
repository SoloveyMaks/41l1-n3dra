import {
  AccountCredentials,
  ArtifactManifest,
  AdminOverview,
  ComparisonDto,
  Finding,
  HistoryResponse,
  NodeDefinition,
  NodeRunRecord,
  ProfileData,
  ReportData,
  ResponsibilityData,
  RepositoryCheckResult,
  RepositoryWatch,
  RepositoryWebhookSetup,
  RunSummary,
  RunRevisionResponse,
  SessionRole,
  SessionState,
  SqlPatternResult,
  TelegramBinding,
  TelegramMe,
  TelegramScenario,
  TelegramSettings,
  WorkflowDraft
} from '../types';

// Same-origin by default; VITE_API_BASE_URL overrides the whole API base
// (e.g. "/api" behind a proxy or "https://host/api" for a split deployment).
const configuredBase = import.meta.env.VITE_API_BASE_URL?.trim();
export const API_BASE = configuredBase
  ? configuredBase.replace(/\/+$/, '')
  : '/api';

export class AuthRequiredError extends Error {
  authRequired = true;
  constructor(message = 'Требуется вход по коду доступа') {
    super(message);
    this.name = 'AuthRequiredError';
  }
}

export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
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
    const code = typeof payload?.detail?.code === 'string' ? payload.detail.code : typeof payload?.code === 'string' ? payload.code : undefined;
    throw new ApiError(detailText(payload) || `HTTP ${res.status}`, res.status, code);
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

  async login(accessCode: string, role: SessionRole = 'user'): Promise<SessionState> {
    // Код живёт только в состоянии формы; URL/localStorage его не касаются.
    return (await request('/session', jsonInit('POST', { accessCode, role }))).json();
  },

  async registerAccount(input: AccountCredentials): Promise<SessionState> {
    return (await request('/auth/register', jsonInit('POST', input))).json();
  },

  async loginAccount(email: string, password: string): Promise<SessionState> {
    return (await request('/auth/login', jsonInit('POST', { email, password }))).json();
  },

  async getMe(): Promise<SessionState> {
    return (await request('/auth/me')).json();
  },

  async getHistory(limit = 20): Promise<HistoryResponse> {
    return (await request(`/auth/history?limit=${encodeURIComponent(String(limit))}`)).json();
  },

  async getProfile(): Promise<ProfileData> {
    return (await request('/profile')).json();
  },

  async downloadReportExport(
    runId: string,
    format: 'docx' | 'jsonc',
  ): Promise<{ blob: Blob; filename: string }> {
    const res = await request(`/runs/${encodeURIComponent(runId)}/report/export.${format}`);
    const blob = await res.blob();
    const fallback = `nedra-${runId.slice(0, 12)}.${format}`;
    const disposition = res.headers.get('content-disposition') || '';
    const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
    let filename = fallback;
    if (utf8?.[1]) {
      try {
        filename = decodeURIComponent(utf8[1].trim());
      } catch {
        filename = utf8[1].trim();
      }
    } else {
      const quoted = /filename="([^"]+)"/i.exec(disposition);
      const plain = /filename=([^;]+)/i.exec(disposition);
      filename = (quoted?.[1] || plain?.[1] || fallback).trim();
    }
    return { blob, filename };
  },

  async getFindingResponsibility(
    runId: string,
    findingId: string,
    signal?: AbortSignal,
  ): Promise<ResponsibilityData> {
    return (await request(
      `/runs/${encodeURIComponent(runId)}/findings/${encodeURIComponent(findingId)}/responsibility`,
      signal ? { signal } : {},
    )).json();
  },

  async getTelegramMe(): Promise<TelegramMe> {
    return (await request('/telegram/me')).json();
  },

  async createTelegramBinding(): Promise<TelegramBinding> {
    return (await request('/telegram/binding', { method: 'POST' })).json();
  },

  async unbindTelegram(): Promise<{ bound: boolean; removed: boolean }> {
    return (await request('/telegram/unbind', { method: 'POST' })).json();
  },

  async listTelegramScenarios(): Promise<{ items: TelegramScenario[] }> {
    return (await request('/telegram/scenarios')).json();
  },

  async updateTelegramSettings(settings: TelegramSettings): Promise<TelegramSettings> {
    return (await request('/telegram/settings', jsonInit('PUT', settings))).json();
  },

  async getAdminOverview(): Promise<AdminOverview> {
    return (await request('/admin/overview')).json();
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

  /** ZIP с реальным прогрессом загрузки и возможностью отмены. */
  uploadArtifactWithProgress(
    file: File,
    onProgress: (percent: number) => void,
    signal?: AbortSignal,
  ): Promise<ArtifactManifest> {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', `${API_BASE}/artifacts`);
      xhr.withCredentials = true;
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
      };
      xhr.onerror = () => reject(new ApiError('Сервер недоступен', 0));
      xhr.onabort = () => reject(new DOMException('Загрузка отменена', 'AbortError'));
      xhr.onload = () => {
        let payload: unknown = null;
        try {
          payload = JSON.parse(xhr.responseText);
        } catch {
          payload = null;
        }
        if (xhr.status === 401) {
          reject(new AuthRequiredError(detailText(payload) || 'Требуется вход по коду доступа'));
          return;
        }
        if (xhr.status < 200 || xhr.status >= 300) {
          const record = payload && typeof payload === 'object' ? (payload as Record<string, unknown>) : null;
          const detail = record?.detail && typeof record.detail === 'object' ? (record.detail as Record<string, unknown>) : null;
          const code = typeof detail?.code === 'string' ? detail.code : typeof record?.code === 'string' ? record.code : undefined;
          reject(new ApiError(detailText(payload) || `HTTP ${xhr.status}`, xhr.status, code));
          return;
        }
        resolve(payload as ArtifactManifest);
      };
      signal?.addEventListener('abort', () => xhr.abort(), { once: true });
      const formData = new FormData();
      formData.append('file', file);
      xhr.send(formData);
    });
  },

  async importRepository(url: string, branch?: string, signal?: AbortSignal): Promise<ArtifactManifest> {
    return (await request(
      '/artifacts/repository',
      { ...jsonInit('POST', branch ? { url, branch } : { url }), signal },
    )).json();
  },

  async listRepositoryWatches(): Promise<RepositoryWatch[]> {
    return (await request('/repository-watches')).json();
  },

  async createRepositoryWatch(input: {
    workflowId: string;
    url: string;
    branch?: string;
    cronOrInterval?: string;
    triggerMode?: 'poll' | 'webhook';
  }): Promise<RepositoryWatch> {
    const body: Record<string, unknown> = {
      workflowId: input.workflowId,
      url: input.url,
      triggerMode: input.triggerMode || 'poll',
    };
    if (input.branch) body.branch = input.branch;
    if (input.cronOrInterval) body.cronOrInterval = input.cronOrInterval;
    return (await request('/repository-watches', jsonInit('POST', body))).json();
  },

  /**
   * Выпускает (или перевыпускает) секрет доставки. Это единственный вызов,
   * который возвращает секрет: вызывать только по явному действию администратора
   * и не сохранять ответ в браузере.
   */
  async setupRepositoryWebhook(watchId: string, rotate: boolean): Promise<RepositoryWebhookSetup> {
    return (await request(
      `/repository-watches/${encodeURIComponent(watchId)}/webhook/setup`,
      jsonInit('POST', { rotate }),
    )).json();
  },

  async setRepositoryWatchEnabled(watchId: string, enabled: boolean): Promise<RepositoryWatch> {
    return (await request(`/repository-watches/${watchId}`, jsonInit('PATCH', { enabled }))).json();
  },

  async checkRepositoryWatchNow(watchId: string): Promise<RepositoryCheckResult> {
    return (await request(`/repository-watches/${watchId}/check`, { method: 'POST' })).json();
  },

  async loadSampleArtifact(): Promise<ArtifactManifest> {
    return (await request('/artifacts/sample', { method: 'POST' })).json();
  },

  async loadTrainingArtifact(): Promise<ArtifactManifest> {
    return (await request('/artifacts/sample/training', { method: 'POST' })).json();
  },

  async listArtifacts(): Promise<ArtifactManifest[]> {
    return (await request('/artifacts')).json();
  },

  async getArtifact(artifactId: string): Promise<ArtifactManifest> {
    return (await request(`/artifacts/${encodeURIComponent(artifactId)}`)).json();
  },

  async listWorkflows(): Promise<any[]> {
    return (await request('/workflows')).json();
  },

  async getWorkflow(workflowId: string): Promise<any> {
    return (await request(`/workflows/${workflowId}`)).json();
  },

  async createWorkflow(draft: WorkflowDraft): Promise<{ id: string; latest_revision_id: string }> {
    return (await request('/workflows', jsonInit('POST', draft))).json();
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

  async getRunRevision(runId: string): Promise<RunRevisionResponse> {
    return (await request(`/runs/${runId}/revision`)).json();
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

  async setScheduleEnabled(scheduleId: string, enabled: boolean): Promise<any> {
    return (await request(
      `/schedules/${scheduleId}`,
      jsonInit('PATCH', { enabled }),
    )).json();
  },

  async runAiSyntheticTest(runId?: string): Promise<any> {
    return (await request('/ai/synthetic-test', jsonInit('POST', { runId }))).json();
  },

  async compareRuns(baselineRunId: string, currentRunId: string): Promise<ComparisonDto> {
    return (await request(`/comparisons/${baselineRunId}/${currentRunId}`)).json();
  },
  async getSqlPattern(baselineRunId: string, currentRunId: string): Promise<SqlPatternResult> {
    return (await request(`/comparisons/${baselineRunId}/${currentRunId}/sql-pattern`)).json();
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
