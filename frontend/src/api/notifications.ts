const configuredBase = import.meta.env.VITE_API_BASE_URL?.trim();
const API_BASE = configuredBase ? configuredBase.replace(/\/+$/, '') : '/api';

export type DeliveryStatus = 'pending' | 'sending' | 'delivered' | 'failed' | 'skipped' | 'waiting';
export type TerminalRunStatus = 'succeeded' | 'failed' | 'cancelled' | 'interrupted';
export type SecurityVerdict = 'pass' | 'fail' | 'inconclusive' | 'pending';

export interface NotificationItem {
  run_id: string;
  execution_status: TerminalRunStatus;
  security_verdict: SecurityVerdict;
  delivery_status: DeliveryStatus;
  attempts: number;
  status_code: number | null;
  detail: string;
  created_at: string;
  completed_at: string;
  delivered_at: string | null;
  read_at: string | null;
  workflow_id: string;
  workflow_name: string | null;
  total_findings: number;
  critical_count: number;
  high_count: number;
}

export interface NotificationInbox {
  items: NotificationItem[];
  unread: number;
}

export interface NotificationSettings {
  enabled: boolean;
  webhook_url: string;
  allow_local: boolean;
  allow_external: boolean;
}

export class NotificationApiError extends Error {
  status: number;
  code?: string;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = 'NotificationApiError';
    this.status = status;
    this.code = code;
  }
}

const str = (value: unknown): string | null => (typeof value === 'string' ? value : null);
const num = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;

function detailText(payload: unknown): string {
  if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) return '';
  if (!('detail' in payload)) return '';
  const detail = payload.detail;
  if (typeof detail === 'string') return detail;
  if (typeof detail === 'object' && detail !== null && !Array.isArray(detail)) {
    if ('message' in detail && typeof detail.message === 'string') return detail.message;
    if ('code' in detail && typeof detail.code === 'string') return detail.code;
  }
  return '';
}

function errorCode(payload: unknown, status: number): string | undefined {
  if (typeof payload === 'object' && payload !== null && !Array.isArray(payload) && 'detail' in payload) {
    const detail = payload.detail;
    if (typeof detail === 'object' && detail !== null && !Array.isArray(detail) && 'code' in detail) {
      if (typeof detail.code === 'string') return detail.code;
    }
  }
  return status === 401 ? 'AUTH_REQUIRED' : undefined;
}

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: 'include',
    cache: 'no-store',
  });
  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => ({}));
    throw new NotificationApiError(
      detailText(payload) || `HTTP ${response.status}`,
      response.status,
      errorCode(payload, response.status),
    );
  }
  return response;
}

function jsonInit(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
}

function parseItem(value: unknown): NotificationItem | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  if (!('run_id' in value) || typeof value.run_id !== 'string') return null;
  let execution_status: TerminalRunStatus;
  let security_verdict: SecurityVerdict;
  let delivery_status: DeliveryStatus;
  if ('execution_status' in value && typeof value.execution_status === 'string') {
    switch (value.execution_status) {
      case 'succeeded': case 'failed': case 'cancelled': case 'interrupted':
        execution_status = value.execution_status;
        break;
      default:
        return null;
    }
  } else return null;
  if ('security_verdict' in value && typeof value.security_verdict === 'string') {
    switch (value.security_verdict) {
      case 'pass': case 'fail': case 'inconclusive': case 'pending':
        security_verdict = value.security_verdict;
        break;
      default:
        return null;
    }
  } else return null;
  if ('delivery_status' in value && typeof value.delivery_status === 'string') {
    switch (value.delivery_status) {
      case 'pending': case 'sending': case 'delivered':
      case 'failed': case 'skipped': case 'waiting':
        delivery_status = value.delivery_status;
        break;
      default:
        return null;
    }
  } else return null;

  const completed_at = 'completed_at' in value ? str(value.completed_at) : null;
  const workflow_id = 'workflow_id' in value ? str(value.workflow_id) : null;
  if (!completed_at || workflow_id === null) return null;

  return {
    run_id: value.run_id,
    execution_status: execution_status as TerminalRunStatus,
    security_verdict: security_verdict as SecurityVerdict,
    delivery_status: delivery_status as DeliveryStatus,
    attempts: ('attempts' in value ? num(value.attempts) : null) ?? 0,
    status_code: 'status_code' in value ? num(value.status_code) : null,
    detail: 'detail' in value ? str(value.detail) ?? '' : '',
    created_at: 'created_at' in value ? str(value.created_at) ?? completed_at : completed_at,
    completed_at,
    delivered_at: 'delivered_at' in value ? str(value.delivered_at) : null,
    read_at: 'read_at' in value ? str(value.read_at) : null,
    workflow_id,
    workflow_name: 'workflow_name' in value ? str(value.workflow_name) : null,
    total_findings: ('total_findings' in value ? num(value.total_findings) : null) ?? 0,
    critical_count: ('critical_count' in value ? num(value.critical_count) : null) ?? 0,
    high_count: ('high_count' in value ? num(value.high_count) : null) ?? 0,
  };
}

export async function getNotifications(signal?: AbortSignal): Promise<NotificationInbox> {
  const payload: unknown = await (await request('/notifications', signal ? { signal } : {})).json();
  if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) {
    return { items: [], unread: 0 };
  }
  const rawItems = 'items' in payload && Array.isArray(payload.items) ? payload.items : [];
  return {
    items: rawItems.map(parseItem).filter((item): item is NotificationItem => item !== null),
    unread: 'unread' in payload ? num(payload.unread) ?? 0 : 0,
  };
}

export async function markNotificationRead(runId: string): Promise<void> {
  await request(`/notifications/${encodeURIComponent(runId)}/read`, { method: 'POST' });
}

export async function markAllNotificationsRead(): Promise<void> {
  await request('/notifications/read-all', { method: 'POST' });
}

function parseSettings(payload: unknown): NotificationSettings {
  if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) {
    return { enabled: false, webhook_url: '', allow_local: false, allow_external: false };
  }
  return {
    enabled: 'enabled' in payload && payload.enabled === true,
    webhook_url: 'webhook_url' in payload ? str(payload.webhook_url) ?? '' : '',
    allow_local: 'allow_local' in payload && payload.allow_local === true,
    allow_external: 'allow_external' in payload && payload.allow_external === true,
  };
}

export async function getNotificationSettings(): Promise<NotificationSettings> {
  return parseSettings(await (await request('/notifications/settings')).json());
}

export async function saveNotificationSettings(
  value: NotificationSettings,
): Promise<NotificationSettings> {
  return parseSettings(
    await (await request('/notifications/settings', jsonInit('PUT', value))).json(),
  );
}