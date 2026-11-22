import { API_BASE } from './client'

export interface WorkflowTemplateSummary {
  id: string
  name: string
  description: string
  publishedBy: string
  publishedAt: string | null
  nodeCount: number
}

export interface WorkflowTemplateDetail extends WorkflowTemplateSummary {
  draft: Record<string, unknown>
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  })
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(payload?.detail || 'Библиотека шаблонов недоступна')
  }
  return payload as T
}

export function listWorkflowTemplates(): Promise<{ items: WorkflowTemplateSummary[] }> {
  return request('/workflow-templates')
}

export function publishWorkflowTemplate(workflowId: string, name?: string): Promise<WorkflowTemplateSummary> {
  return request('/workflow-templates', {
    method: 'POST',
    body: JSON.stringify({ workflowId, name }),
  })
}

export function copyWorkflowTemplate(templateId: string, name?: string): Promise<{ workflowId: string; revisionId: string; name: string }> {
  return request(`/workflow-templates/${encodeURIComponent(templateId)}/copy`, {
    method: 'POST',
    body: JSON.stringify({ name }),
  })
}
