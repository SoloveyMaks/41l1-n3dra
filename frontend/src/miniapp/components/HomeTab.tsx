import { useMemo, useState } from 'react'
import { ChevronRight, GitBranch, Play, RefreshCw, ShieldAlert } from 'lucide-react'
import type { RunSummary, TelegramScenario } from '../../types'
import type { MiniRunInput } from '../api'
import { errorText } from '../api'
import {
  executionLabel,
  executionTone,
  findingsSummary,
  formatWhen,
  isActiveRun,
  verdictFor,
} from '../status'
import { Alert, BrandHeader, EmptyState, Pill, Spinner } from './ui'

interface HomeTabProps {
  displayName: string
  runs: RunSummary[]
  scenarios: TelegramScenario[]
  defaultScenario: string | null
  refreshing: boolean
  syncError: string | null
  onCreate: (input: MiniRunInput) => Promise<string>
  onOpenRun: (runId: string) => void
  onRefresh: () => void
}

function validateRepositoryLink(value: string): string | null {
  const trimmed = value.trim()
  if (!trimmed) return 'Вставьте ссылку на репозиторий.'
  let parsed: URL
  try {
    parsed = new URL(trimmed)
  } catch {
    return 'Ссылка выглядит неполной. Пример: https://github.com/org/repo'
  }
  if (parsed.protocol !== 'https:') return 'Нужна HTTPS-ссылка на репозиторий.'
  if (!parsed.hostname) return 'В ссылке не хватает адреса сайта.'
  return null
}

export function HomeTab({
  displayName,
  runs,
  scenarios,
  defaultScenario,
  refreshing,
  syncError,
  onCreate,
  onOpenRun,
  onRefresh,
}: HomeTabProps) {
  const initialScenario = scenarios.some((item) => item.id === defaultScenario) ? defaultScenario || '' : ''
  const [repositoryUrl, setRepositoryUrl] = useState('')
  const [scenarioId, setScenarioId] = useState(initialScenario)
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  const recent = useMemo(() => {
    return [...runs].sort((a, b) => {
      const left = new Date(a.created_at).getTime() || 0
      const right = new Date(b.created_at).getTime() || 0
      return right - left
    })[0] ?? null
  }, [runs])

  const scenarioMissing = scenarios.length === 0
  const canSubmit = !submitting && !scenarioMissing && scenarioId !== '' && repositoryUrl.trim() !== ''

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (submitting) return
    const validation = validateRepositoryLink(repositoryUrl)
    if (validation) {
      setFormError(validation)
      return
    }
    if (!scenarioId) {
      setFormError('Выберите сценарий проверки.')
      return
    }
    setFormError(null)
    setSubmitting(true)
    try {
      await onCreate({ repositoryUrl: repositoryUrl.trim(), scenarioId })
      setRepositoryUrl('')
    } catch (error) {
      setFormError(errorText(error))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="mini-stack">
      <BrandHeader subtitle={`Привет, ${displayName}. Здесь ваши проверки.`} />

      <section aria-labelledby="mini-recent-title" className="mini-card">
        <div className="mini-section-head">
          <h2 id="mini-recent-title" className="mini-card__title">Последняя проверка</h2>
          <button
            type="button"
            className="mini-icon-btn"
            onClick={onRefresh}
            disabled={refreshing}
            aria-label="Обновить данные"
          >
            <RefreshCw size={16} className={refreshing ? 'mini-spin' : undefined} />
          </button>
        </div>
        {syncError && <Alert tone="error">{syncError}</Alert>}
        {recent ? (
          <button type="button" className="mini-run" onClick={() => onOpenRun(recent.id)}>
            <div className="mini-run__main">
              <div className="mini-run__meta">
                <Pill tone={executionTone(recent.execution_status)} dot={isActiveRun(recent)}>
                  {executionLabel(recent.execution_status)}
                </Pill>
                <span className="mini-run__when">{formatWhen(recent.created_at)}</span>
              </div>
              <div className="mini-run__verdict">{verdictFor(recent).text}</div>
              <div className="mini-run__summary">{findingsSummary(recent)}</div>
            </div>
            <ChevronRight size={18} className="mini-run__chevron" aria-hidden="true" />
          </button>
        ) : (
          <EmptyState title="Пока нет проверок">
            Запустите первую — результат появится здесь.
          </EmptyState>
        )}
      </section>

      <section aria-labelledby="mini-launch-title" className="mini-card">
        <div className="mini-card__head">
          <div>
            <h2 id="mini-launch-title" className="mini-card__title">Новая проверка</h2>
            <p className="mini-card__hint">Ссылка на репозиторий и сценарий — этого достаточно.</p>
          </div>
          <div className="mini-card__badge" aria-hidden="true">
            <ShieldAlert size={18} />
          </div>
        </div>

        <form className="mini-form" onSubmit={submit} noValidate>
          <div className="mini-field">
            <label className="mini-label" htmlFor="mini-repo-url">Ссылка на репозиторий</label>
            <div className="mini-input-wrap">
              <GitBranch size={17} className="mini-input-icon" aria-hidden="true" />
              <input
                id="mini-repo-url"
                className="mini-input mini-input--with-icon"
                type="url"
                inputMode="url"
                autoComplete="off"
                autoCapitalize="none"
                spellCheck={false}
                enterKeyHint="go"
                placeholder="https://github.com/org/repo"
                value={repositoryUrl}
                onChange={(event) => {
                  setRepositoryUrl(event.target.value)
                  if (formError) setFormError(null)
                }}
                disabled={submitting}
                aria-describedby="mini-repo-hint"
              />
            </div>
            <p id="mini-repo-hint" className="mini-help">Только HTTPS-ссылки GitHub или GitLab.</p>
          </div>

          <div className="mini-field">
            <label className="mini-label" htmlFor="mini-scenario">Сценарий</label>
            <div className="mini-select-wrap">
              <select
                id="mini-scenario"
                className="mini-select"
                value={scenarioId}
                onChange={(event) => {
                  setScenarioId(event.target.value)
                  if (formError) setFormError(null)
                }}
                disabled={submitting || scenarioMissing}
              >
                <option value="">{scenarioMissing ? 'Сценариев пока нет' : 'Выберите сценарий'}</option>
                {scenarios.map((scenario) => (
                  <option value={scenario.id} key={scenario.id}>{scenario.name}</option>
                ))}
              </select>
            </div>
          </div>

          {scenarioMissing && (
            <Alert tone="info">
              Сценарии ещё не настроены. Создайте сценарий в веб-кабинете NEDRA и обновите экран.
            </Alert>
          )}
          {formError && <Alert tone="error">{formError}</Alert>}

          <button type="submit" className="mini-btn mini-btn--primary mini-btn--block" disabled={!canSubmit} aria-busy={submitting}>
            {submitting ? <Spinner /> : <Play size={17} aria-hidden="true" />}
            {submitting ? 'Запускаем…' : 'Запустить проверку'}
          </button>
        </form>
      </section>
    </div>
  )
}
