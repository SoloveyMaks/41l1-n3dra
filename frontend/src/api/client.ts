import {
  ArtifactManifest,
  Finding,
  NodeDefinition,
  NodeRunRecord,
  ReportData,
  RunSummary,
  WorkflowDraft
} from '../types';

// Same-origin by default; VITE_API_BASE_URL overrides the whole API base
// (e.g. "/api" behind a proxy or "https://host/api" for a split deployment).
const configuredBase = import.meta.env.VITE_API_BASE_URL?.trim();
export const API_BASE = configuredBase
  ? configuredBase.replace(/\/+$/, '')
  : '/api';

export const apiClient = {
  async getNodeTypes(): Promise<NodeDefinition[]> {
    const res = await fetch(`${API_BASE}/node-types`);
    if (!res.ok) throw new Error('Ошибка получения типов нод');
    return res.json();
  },

  async uploadArtifact(file: File): Promise<ArtifactManifest> {
    const formData = new FormData();
    formData.append('file', file);
    const res = await fetch(`${API_BASE}/artifacts`, {
      method: 'POST',
      body: formData,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Ошибка загрузки архива' }));
      throw new Error(err.detail || 'Ошибка загрузки архива');
    }
    return res.json();
  },

  async loadSampleArtifact(): Promise<ArtifactManifest> {
    const res = await fetch(`${API_BASE}/artifacts/sample`, { method: 'POST' });
    if (!res.ok) throw new Error('Ошибка загрузки учебного проекта');
    return res.json();
  },

  async listArtifacts(): Promise<ArtifactManifest[]> {
    const res = await fetch(`${API_BASE}/artifacts`);
    if (!res.ok) throw new Error('Ошибка получения списка артефактов');
    return res.json();
  },

  async listWorkflows(): Promise<any[]> {
    const res = await fetch(`${API_BASE}/workflows`);
    if (!res.ok) throw new Error('Ошибка получения списка сценариев');
    return res.json();
  },

  async getWorkflow(workflowId: string): Promise<any> {
    const res = await fetch(`${API_BASE}/workflows/${workflowId}`);
    if (!res.ok) throw new Error('Сценарий не найден');
    return res.json();
  },

  async saveWorkflowDraft(workflowId: string, draft: WorkflowDraft): Promise<any> {
    const res = await fetch(`${API_BASE}/workflows/${workflowId}/draft`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: jsonStringifySafe(draft),
    });
    if (!res.ok) throw new Error('Ошибка сохранения сценария');
    return res.json();
  },

  async validateWorkflow(workflowId: string, draft?: WorkflowDraft): Promise<{ valid: boolean; issues: any[] }> {
    const res = await fetch(`${API_BASE}/workflows/${workflowId}/validate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: draft ? jsonStringifySafe(draft) : undefined,
    });
    if (!res.ok) throw new Error('Ошибка валидации сценария');
    return res.json();
  },

  async triggerRun(workflowId: string, artifactId?: string, triggerType = 'manual'): Promise<{ runId: string }> {
    const res = await fetch(`${API_BASE}/workflows/${workflowId}/runs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ artifactId, triggerType }),
    });
    if (!res.ok) throw new Error('Ошибка запуска сценария');
    return res.json();
  },

  async listRuns(): Promise<RunSummary[]> {
    const res = await fetch(`${API_BASE}/runs`);
    if (!res.ok) throw new Error('Ошибка получения истории запусков');
    return res.json();
  },

  async getRun(runId: string): Promise<RunSummary> {
    const res = await fetch(`${API_BASE}/runs/${runId}`);
    if (!res.ok) throw new Error('Запуск не найден');
    return res.json();
  },

  async getRunNodes(runId: string): Promise<NodeRunRecord[]> {
    const res = await fetch(`${API_BASE}/runs/${runId}/nodes`);
    if (!res.ok) throw new Error('Ошибка получения состояния нод');
    return res.json();
  },

  async getRunFindings(runId: string): Promise<Finding[]> {
    const res = await fetch(`${API_BASE}/runs/${runId}/findings`);
    if (!res.ok) throw new Error('Ошибка получения находок');
    return res.json();
  },

  async getRunReport(runId: string): Promise<ReportData> {
    const res = await fetch(`${API_BASE}/runs/${runId}/report`);
    if (!res.ok) throw new Error('Отчет еще не сформирован');
    return res.json();
  },

  async listSchedules(): Promise<any[]> {
    const res = await fetch(`${API_BASE}/schedules`);
    if (!res.ok) throw new Error('Ошибка получения расписания');
    return res.json();
  },

  async createSchedule(workflowId: string, artifactId: string, cronOrInterval: string): Promise<any> {
    const res = await fetch(`${API_BASE}/schedules`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ workflowId, artifactId, cronOrInterval }),
    });
    if (!res.ok) throw new Error('Ошибка создания расписания');
    return res.json();
  },

  async runAiSyntheticTest(runId?: string): Promise<any> {
    const res = await fetch(`${API_BASE}/ai/synthetic-test`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ runId }),
    });
    if (!res.ok) throw new Error('Ошибка выполнения синтетического теста AI');
    return res.json();
  },

  getOfficialJsonUrl(runId: string): string {
    return `${API_BASE}/runs/${runId}/report/official.json`;
  },

  getReadableMarkdownUrl(runId: string): string {
    return `${API_BASE}/runs/${runId}/report/readable.md`;
  },

  createEventSource(runId: string, afterSeq = 0): EventSource {
    return new EventSource(`${API_BASE}/runs/${runId}/events?after=${afterSeq}`);
  }
};

function jsonStringifySafe(data: any): string {
  return JSON.stringify(data, (_, v) => (typeof v === 'undefined' ? null : v));
}
