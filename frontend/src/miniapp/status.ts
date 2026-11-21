import type { ExecutionStatus, Finding, RunSummary, SecurityVerdict, Severity } from '../types'

export type Tone = 'active' | 'ok' | 'warn' | 'error' | 'muted'

const EXECUTION_LABELS: Record<ExecutionStatus, string> = {
  queued: 'В очереди',
  running: 'Идёт проверка',
  succeeded: 'Завершено',
  failed: 'Ошибка выполнения',
  interrupted: 'Прервано',
  cancelled: 'Отменено',
}

const VERDICT_LABELS: Record<SecurityVerdict, string> = {
  pending: 'Итог не рассчитан',
  pass: 'Проблем не найдено',
  fail: 'Есть подтверждённые проблемы',
  inconclusive: 'Однозначного вывода нет',
}

export function executionLabel(status: ExecutionStatus | string): string {
  return EXECUTION_LABELS[status as ExecutionStatus] ?? 'Неизвестный статус'
}

export function executionTone(status: ExecutionStatus | string): Tone {
  if (status === 'queued' || status === 'running') return 'active'
  if (status === 'succeeded') return 'ok'
  if (status === 'failed' || status === 'interrupted' || status === 'cancelled') return 'error'
  return 'muted'
}

export function isActiveRun(run: RunSummary | null | undefined): boolean {
  return run?.execution_status === 'queued' || run?.execution_status === 'running'
}

export function isFinishedRun(run: RunSummary | null | undefined): boolean {
  return Boolean(run?.finished_at) && Boolean(run) && !isActiveRun(run)
}

// Статус выполнения и вердикт — разные вещи. Пока проверка не дошла до конца,
// вердикт не показываем как итог, даже если поле уже заполнено.
export function verdictFor(run: RunSummary): { text: string; tone: Tone } {
  if (isActiveRun(run)) return { text: 'Ожидаем результат', tone: 'active' }
  if (run.execution_status !== 'succeeded') {
    return { text: 'Без итога — проверка не завершилась', tone: 'muted' }
  }
  const verdict = run.security_verdict
  return { text: VERDICT_LABELS[verdict] ?? 'Итог не рассчитан', tone: verdictTone(verdict) }
}

function verdictTone(verdict: SecurityVerdict): Tone {
  if (verdict === 'pass') return 'ok'
  if (verdict === 'fail') return 'error'
  if (verdict === 'inconclusive') return 'warn'
  return 'muted'
}

export function reportAvailable(run: RunSummary | null | undefined): boolean {
  return Boolean(run && run.execution_status === 'succeeded' && run.finished_at)
}

export function reportAvailabilityText(run: RunSummary | null | undefined): string | null {
  if (!run || reportAvailable(run)) return null
  if (isActiveRun(run)) return 'Отчёт появится, когда проверка завершится.'
  if (run.execution_status === 'succeeded') return 'Отчёт ещё готовится на сервере.'
  return 'Отчёт недоступен: проверка не завершилась.'
}

export function formatWhen(iso?: string | null): string {
  if (!iso) return 'Дата неизвестна'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return 'Дата неизвестна'
  const now = new Date()
  const sameDay = date.toDateString() === now.toDateString()
  const time = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' }).format(date)
  if (sameDay) return `сегодня, ${time}`
  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  if (date.toDateString() === yesterday.toDateString()) return `вчера, ${time}`
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(date)
}

export function plural(count: number, one: string, few: string, many: string): string {
  const mod10 = count % 10
  const mod100 = count % 100
  if (mod10 === 1 && mod100 !== 11) return one
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few
  return many
}

export function findingsSummary(run: RunSummary): string {
  const total = run.total_findings
  if (!total) return 'Находок нет'
  const parts: string[] = []
  if (run.critical_count) parts.push(`${run.critical_count} критич.`)
  if (run.high_count) parts.push(`${run.high_count} высок.`)
  if (run.medium_count) parts.push(`${run.medium_count} средн.`)
  if (run.low_count) parts.push(`${run.low_count} низк.`)
  if (run.info_count) parts.push(`${run.info_count} инфо`)
  const head = `${total} ${plural(total, 'находка', 'находки', 'находок')}`
  return parts.length ? `${head}: ${parts.join(' · ')}` : head
}

export const SEVERITY_LABELS: Record<Severity, string> = {
  critical: 'Критично',
  high: 'Высокий риск',
  medium: 'Средний риск',
  low: 'Низкий риск',
  info: 'Информация',
}

export function severityTone(severity: Severity | string): Tone {
  if (severity === 'critical' || severity === 'high') return 'error'
  if (severity === 'medium') return 'warn'
  if (severity === 'low') return 'active'
  return 'muted'
}

export function severityRank(severity: Severity | string): number {
  const order: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3, info: 4 }
  return order[severity] ?? 9
}

export function findingStatusLabel(finding: Finding): string {
  if (finding.status === 'confirmed') return 'Подтверждено'
  if (finding.status === 'potential') return 'Возможно'
  return 'Неоднозначно'
}

export function analyzerLabel(analyzer: string): string {
  if (analyzer === 'secret') return 'Секреты'
  if (analyzer === 'sca') return 'Зависимости'
  if (analyzer === 'sast') return 'Код'
  return 'Анализ'
}

export function shortLocation(finding: Finding): string {
  const path = finding.primaryLocation?.path || ''
  const line = finding.primaryLocation?.startLine
  if (!path) return 'Файл не указан'
  const tail = path.split('/').filter(Boolean).slice(-2).join('/')
  return line ? `${tail}:${line}` : tail
}
