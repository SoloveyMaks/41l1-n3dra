import { API_BASE } from '../api/client'
import type { Finding, RunSummary, TelegramDeliveryMode, TelegramScenario, TelegramSettings } from '../types'

// Все методы Mini App живут под одним префиксом бэкенда. Держим его в одном
// месте, чтобы bearer-запросы не уезжали в /api/bootstrap и соседние пути.
const MINI_API_BASE = `${API_BASE}/telegram/mini`

type MiniRunPayload = Omit<RunSummary, 'critical_count' | 'high_count' | 'medium_count' | 'low_count' | 'info_count'> & {
  critical_findings: number
  high_findings: number
}

function normalizeRun(run: MiniRunPayload): RunSummary {
  return {
    ...run,
    critical_count: run.critical_findings,
    high_count: run.high_findings,
    medium_count: 0,
    low_count: 0,
    info_count: 0,
  }
}

export interface MiniSession {
  token: string
  expiresAt: number
}

export interface MiniBootstrap {
  user: { displayName: string }
  settings: TelegramSettings
  scenarios: TelegramScenario[]
  runs: RunSummary[]
}

export interface MiniRunDetail {
  run: RunSummary
  findings: Finding[]
}

export interface MiniSettingsPatch {
  deliveryMode?: TelegramDeliveryMode
  scenario?: string | null
}

export interface MiniRunInput {
  repositoryUrl: string
  scenarioId: string
}

export interface MiniReport {
  blob: Blob
  filename: string
}

export class MiniApiError extends Error {
  status: number
  code?: string

  constructor(message: string, status: number, code?: string) {
    super(message)
    this.name = 'MiniApiError'
    this.status = status
    this.code = code
  }
}

function detail(payload: unknown): { message: string; code?: string } {
  if (!payload || typeof payload !== 'object') return { message: '' }
  if ('detail' in payload) {
    const raw = payload.detail
    if (typeof raw === 'string') return { message: raw }
    if (raw && typeof raw === 'object') {
      return {
        message: 'message' in raw && typeof raw.message === 'string' ? raw.message : '',
        code: 'code' in raw && typeof raw.code === 'string' ? raw.code : undefined,
      }
    }
    return { message: '' }
  }
  return {
    message: 'message' in payload && typeof payload.message === 'string' ? payload.message : '',
    code: 'code' in payload && typeof payload.code === 'string' ? payload.code : undefined,
  }
}

async function failure(res: Response): Promise<MiniApiError> {
  const payload = await res.json().catch(() => null)
  const parsed = detail(payload)
  const fallback = res.status === 401
    ? 'Сессия истекла'
    : res.status === 409
      ? 'Аккаунт не привязан'
      : `Сервис ответил ошибкой ${res.status}`
  return new MiniApiError(parsed.message || fallback, res.status, parsed.code)
}

export function errorText(error: unknown): string {
  if (error instanceof MiniApiError) return error.message
  if (error instanceof DOMException && error.name === 'AbortError') return 'Запрос отменён'
  if (error instanceof TypeError) return 'Нет связи с сервером. Проверьте сеть и повторите.'
  if (error instanceof Error && error.message) return error.message
  return 'Что-то пошло не так. Повторите попытку.'
}

// AbortSignal.any есть не во всех Telegram WebView, поэтому склеиваем вручную:
// запрос отменяется и при уходе с экрана, и при смене сессии.
export function combineSignals(...signals: Array<AbortSignal | undefined>): AbortSignal | undefined {
  const active = signals.filter((signal): signal is AbortSignal => Boolean(signal))
  if (active.length === 0) return undefined
  if (active.length === 1) return active[0]
  if (typeof AbortSignal.any === 'function') return AbortSignal.any(active)
  const controller = new AbortController()
  const abort = () => {
    if (!controller.signal.aborted) controller.abort()
  }
  for (const signal of active) {
    if (signal.aborted) {
      controller.abort()
      break
    }
    signal.addEventListener('abort', abort, { once: true })
  }
  return controller.signal
}

function reportFilename(disposition: string | null, runId: string): string {
  const fallback = `nedra-${runId.slice(0, 12)}.jsonc`
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(disposition || '')
  const quoted = /filename="([^"]+)"/i.exec(disposition || '')
  const plain = /filename=([^;]+)/i.exec(disposition || '')
  let name = fallback
  if (utf8?.[1]) {
    try {
      name = decodeURIComponent(utf8[1].trim())
    } catch {
      name = utf8[1].trim()
    }
  } else {
    name = (quoted?.[1] || plain?.[1] || fallback).trim()
  }
  return name.replace(/[\\/:*?"<>|\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]+/g, '_') || fallback
}

export class MiniApi {
  // Токен живёт только в памяти вкладки: ни localStorage, ни URL.
  private token: string | null = null

  setToken(token: string | null) {
    this.token = token
  }

  async authorize(initData: string, signal?: AbortSignal): Promise<MiniSession> {
    return this.exchange('/auth', { initData }, signal)
  }

  // Код приходит только из памяти формы и уходит только в JSON-теле.
  async bind(initData: string, code: string, signal?: AbortSignal): Promise<MiniSession> {
    return this.exchange('/bind', { initData, code: code.trim() }, signal)
  }

  private async exchange(path: string, body: Record<string, string>, signal?: AbortSignal): Promise<MiniSession> {
    const res = await fetch(`${MINI_API_BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      credentials: 'omit',
      cache: 'no-store',
      signal,
    })
    if (!res.ok) throw await failure(res)
    const payload = (await res.json()) as Partial<MiniSession>
    if (!payload || typeof payload.token !== 'string' || !payload.token || typeof payload.expiresAt !== 'number' || !Number.isFinite(payload.expiresAt)) {
      throw new MiniApiError('Сервер вернул неполный ответ входа', 500)
    }
    this.token = payload.token
    return { token: payload.token, expiresAt: payload.expiresAt }
  }

  private async request(path: string, init: RequestInit = {}): Promise<Response> {
    if (!this.token) throw new MiniApiError('Сессия не активна', 401)
    const headers = new Headers(init.headers || {})
    headers.set('Authorization', `Bearer ${this.token}`)
    const res = await fetch(`${MINI_API_BASE}${path}`, {
      ...init,
      headers,
      credentials: 'omit',
      cache: 'no-store',
    })
    if (!res.ok) throw await failure(res)
    return res
  }

  async bootstrap(signal?: AbortSignal): Promise<MiniBootstrap> {
    const res = await this.request('/bootstrap', { signal })
    const payload = await res.json() as Omit<MiniBootstrap, 'runs'> & { runs: MiniRunPayload[] }
    return { ...payload, runs: payload.runs.map(normalizeRun) }
  }

  async updateSettings(patch: MiniSettingsPatch, signal?: AbortSignal): Promise<TelegramSettings> {
    const res = await this.request('/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
      signal,
    })
    return res.json()
  }

  async createRun(input: MiniRunInput, signal?: AbortSignal): Promise<{ runId: string }> {
    const body: { repositoryUrl: string; scenarioId: string } = {
      repositoryUrl: input.repositoryUrl.trim(),
      scenarioId: input.scenarioId,
    }
    const res = await this.request('/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    })
    const payload = (await res.json()) as { runId?: string }
    if (!payload || typeof payload.runId !== 'string' || !payload.runId) {
      throw new MiniApiError('Сервер не вернул номер проверки', 500)
    }
    return { runId: payload.runId }
  }

  async getRun(runId: string, signal?: AbortSignal): Promise<MiniRunDetail> {
    const res = await this.request(`/runs/${encodeURIComponent(runId)}`, { signal })
    const payload = await res.json() as { run: MiniRunPayload; findings: Finding[] }
    return { ...payload, run: normalizeRun(payload.run) }
  }

  async downloadReport(runId: string, signal?: AbortSignal): Promise<MiniReport> {
    // Отчёт закрыт Bearer-токеном, а openLink не умеет заголовки. Поэтому
    // забираем Blob с авторизацией и отдаём обычное скачивание браузеру.
    const res = await this.request(`/runs/${encodeURIComponent(runId)}/report.jsonc`, { signal })
    const blob = await res.blob()
    return { blob, filename: reportFilename(res.headers.get('content-disposition'), runId) }
  }
}

export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.rel = 'noopener'
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000)
}
