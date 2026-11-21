import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, Download, FileJson, RefreshCw, ShieldQuestion } from 'lucide-react'
import type { Finding, RunSummary } from '../../types'
import type { MiniReport, MiniRunDetail } from '../api'
import { MiniApiError, errorText, saveBlob } from '../api'
import {
  SEVERITY_LABELS,
  analyzerLabel,
  executionLabel,
  executionTone,
  findingStatusLabel,
  findingsSummary,
  formatWhen,
  isActiveRun,
  reportAvailabilityText,
  reportAvailable,
  severityRank,
  severityTone,
  shortLocation,
  verdictFor,
} from '../status'
import { Alert, EmptyState, Pill, SkeletonList, Spinner } from './ui'

type DetailState =
  | { status: 'loading' }
  | { status: 'ready'; detail: MiniRunDetail; staleError?: string }
  | { status: 'error'; message: string }

interface RunDetailProps {
  runId: string
  sessionKey: number
  onBack: () => void
  loadRun: (runId: string, signal?: AbortSignal) => Promise<MiniRunDetail>
  getReport: (runId: string, signal?: AbortSignal) => Promise<MiniReport>
  onRunUpdated: (run: RunSummary) => void
}

function sortFindings(findings: Finding[]): Finding[] {
  return [...findings].sort((a, b) => {
    const bySeverity = severityRank(a.severity) - severityRank(b.severity)
    if (bySeverity !== 0) return bySeverity
    return shortLocation(a).localeCompare(shortLocation(b), 'ru')
  })
}

function FindingCard({ finding }: { finding: Finding }) {
  const evidence = finding.redactedEvidence?.trim()
  return (
    <article className="mini-finding">
      <div className="mini-finding__head">
        <Pill tone={severityTone(finding.severity)}>{SEVERITY_LABELS[finding.severity] ?? 'Риск'}</Pill>
        <span className="mini-finding__status">{findingStatusLabel(finding)}</span>
      </div>
      <div className="mini-finding__rule">{analyzerLabel(finding.analyzer)}</div>
      <div className="mini-finding__location">{shortLocation(finding)}</div>
      <div className="mini-finding__ruleid">{finding.ruleId}</div>
      {evidence && (
        <details className="mini-details">
          <summary>Показать фрагмент</summary>
          <pre className="mini-evidence">{evidence}</pre>
        </details>
      )}
    </article>
  )
}

export function RunDetail({ runId, sessionKey, onBack, loadRun, getReport, onRunUpdated }: RunDetailProps) {
  const [state, setState] = useState<DetailState>({ status: 'loading' })
  const [reload, setReload] = useState(0)
  const [reportBusy, setReportBusy] = useState(false)
  const [reportError, setReportError] = useState<string | null>(null)
  const [reportSaved, setReportSaved] = useState(false)
  const lastRunRef = useRef<RunSummary | null>(null)
  const reportAbortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    let alive = true
    const controller = new AbortController()
    let timer: number | undefined

    const tick = async () => {
      try {
        const detail = await loadRun(runId, controller.signal)
        if (!alive || controller.signal.aborted) return
        lastRunRef.current = detail.run
        setState({ status: 'ready', detail })
        onRunUpdated(detail.run)
        if (isActiveRun(detail.run)) {
          timer = window.setTimeout(tick, 3000)
        }
      } catch (error) {
        if (!alive || controller.signal.aborted) return
        const message = errorText(error)
        setState((prev) => {
          if (prev.status === 'ready') return { status: 'ready', detail: prev.detail, staleError: message }
          return { status: 'error', message }
        })
        if (isActiveRun(lastRunRef.current)) {
          timer = window.setTimeout(tick, 5000)
        }
      }
    }

    void tick()

    return () => {
      alive = false
      controller.abort()
      if (timer) window.clearTimeout(timer)
    }
  }, [runId, sessionKey, reload, loadRun, onRunUpdated])

  useEffect(() => {
    return () => {
      reportAbortRef.current?.abort()
    }
  }, [])

  function retryLoad() {
    lastRunRef.current = null
    setReportError(null)
    setReportSaved(false)
    setState({ status: 'loading' })
    setReload((value) => value + 1)
  }

  async function downloadReport() {
    if (reportBusy) return
    reportAbortRef.current?.abort()
    const controller = new AbortController()
    reportAbortRef.current = controller
    setReportBusy(true)
    setReportError(null)
    setReportSaved(false)
    try {
      const report = await getReport(runId, controller.signal)
      if (controller.signal.aborted) return
      saveBlob(report.blob, report.filename)
      setReportSaved(true)
    } catch (error) {
      if (controller.signal.aborted) return
      setReportError(error instanceof MiniApiError && error.status === 404 ? 'Отчёт ещё не готов.' : errorText(error))
    } finally {
      if (!controller.signal.aborted) setReportBusy(false)
    }
  }

  const detail = state.status === 'ready' ? state.detail : null
  const run = detail?.run
  const findings = detail ? sortFindings(detail.findings || []) : []
  const verdict = run ? verdictFor(run) : null
  const aiNotes = findings.some((finding) => Boolean(finding.ai_enrichment))

  return (
    <div className="mini-stack">
      <div className="mini-detail-head">
        <button type="button" className="mini-icon-btn" onClick={onBack} aria-label="Назад к проверкам">
          <ArrowLeft size={19} />
        </button>
        <div className="mini-detail-head__text">
          <h1 className="mini-screen-title">Проверка</h1>
          <p className="mini-screen-sub">{run ? formatWhen(run.created_at) : 'Загружаем данные…'}</p>
        </div>
        {run && <Pill tone={executionTone(run.execution_status)} dot={isActiveRun(run)}>
          {executionLabel(run.execution_status)}
        </Pill>}
      </div>

      {state.status === 'loading' && <SkeletonList rows={2} />}

      {state.status === 'error' && (
        <div className="mini-card mini-stack mini-stack--tight">
          <Alert tone="error">{state.message}</Alert>
          <button
            type="button"
            className="mini-btn mini-btn--ghost mini-btn--block"
            onClick={retryLoad}
          >
            <RefreshCw size={16} /> Повторить
          </button>
        </div>
      )}

      {state.status === 'ready' && state.staleError && (
        <Alert tone="error">Не удалось обновить: {state.staleError}</Alert>
      )}

      {run && verdict && (
        <>
          <div className="mini-grid-2">
            <section className="mini-card mini-card--compact" aria-labelledby="mini-exec-title">
              <h2 id="mini-exec-title" className="mini-mini-label">Выполнение</h2>
              <Pill tone={executionTone(run.execution_status)} dot={isActiveRun(run)}>
                {executionLabel(run.execution_status)}
              </Pill>
              <p className="mini-metric__hint">
                {isActiveRun(run) ? 'Обновляем автоматически' : run.finished_at ? `Финиш ${formatWhen(run.finished_at)}` : 'Время финиша неизвестно'}
              </p>
            </section>
            <section className="mini-card mini-card--compact" aria-labelledby="mini-verdict-title">
              <h2 id="mini-verdict-title" className="mini-mini-label">Итог</h2>
              <div className={`mini-verdict mini-verdict--${verdict.tone}`}>{verdict.text}</div>
              <p className="mini-metric__hint">{findingsSummary(run)}</p>
            </section>
          </div>

          <section className="mini-card" aria-labelledby="mini-findings-title">
            <div className="mini-section-head">
              <h2 id="mini-findings-title" className="mini-card__title">Находки</h2>
              <span className="mini-count">{findings.length}</span>
            </div>

            {findings.length > 0 ? (
              <div className="mini-stack mini-stack--tight">
                {findings.map((finding) => <FindingCard finding={finding} key={finding.id} />)}
              </div>
            ) : isActiveRun(run) ? (
              <div className="mini-pending">
                <Spinner size={18} />
                <span>Проверка идёт. Находки появятся, когда она завершится.</span>
              </div>
            ) : run.execution_status === 'succeeded' ? (
              <EmptyState title={run.security_verdict === 'pass' ? 'Проблем не найдено' : 'Находок нет'}>
                {run.security_verdict === 'pass'
                  ? 'Подтверждённых проблем в проверенном коде нет.'
                  : 'Проверка завершилась без конкретных находок.'}
              </EmptyState>
            ) : (
              <EmptyState title="Выводов нет">
                Проверка не дошла до результата — это не «чисто» и не 100%.
              </EmptyState>
            )}

            {aiNotes && (
              <p className="mini-note">Пояснения ИИ — вспомогательные, это не подтверждённые находки.</p>
            )}
          </section>

          <section className="mini-card" aria-labelledby="mini-report-title">
            <div className="mini-card__head">
              <div>
                <h2 id="mini-report-title" className="mini-card__title">Отчёт</h2>
                <p className="mini-card__hint">Полный JSON-отчёт этой проверки.</p>
              </div>
              <div className="mini-card__badge" aria-hidden="true">
                <FileJson size={18} />
              </div>
            </div>

            {reportAvailable(run) ? (
              <button
                type="button"
                className="mini-btn mini-btn--ghost mini-btn--block"
                onClick={downloadReport}
                disabled={reportBusy}
                aria-busy={reportBusy}
              >
                {reportBusy ? <Spinner /> : <Download size={17} />}
                {reportBusy ? 'Готовим файл…' : 'Скачать отчёт'}
              </button>
            ) : (
              <div className="mini-report-locked">
                <ShieldQuestion size={18} aria-hidden="true" />
                <span>{reportAvailabilityText(run)}</span>
              </div>
            )}

            {reportError && <Alert tone="error">{reportError}</Alert>}
            {reportSaved && <Alert tone="success">Файл отчёта сохранён.</Alert>}
          </section>
        </>
      )}
    </div>
  )
}
