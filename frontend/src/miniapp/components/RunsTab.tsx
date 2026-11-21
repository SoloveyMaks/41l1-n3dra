import { useMemo } from 'react'
import { ChevronRight, RefreshCw } from 'lucide-react'
import type { RunSummary } from '../../types'
import {
  executionLabel,
  executionTone,
  findingsSummary,
  formatWhen,
  isActiveRun,
  verdictFor,
} from '../status'
import { Alert, EmptyState, Pill, SkeletonList, Spinner } from './ui'

interface RunsTabProps {
  runs: RunSummary[]
  refreshing: boolean
  syncError: string | null
  onOpenRun: (runId: string) => void
  onRefresh: () => void
}

export function RunsTab({ runs, refreshing, syncError, onOpenRun, onRefresh }: RunsTabProps) {
  const ordered = useMemo(() => {
    return [...runs].sort((a, b) => {
      const left = new Date(a.created_at).getTime() || 0
      const right = new Date(b.created_at).getTime() || 0
      return right - left
    })
  }, [runs])

  return (
    <div className="mini-stack">
      <div className="mini-screen-head">
        <div>
          <h1 className="mini-screen-title">Проверки</h1>
          <p className="mini-screen-sub">Реальные запуски и их находки.</p>
        </div>
        <button
          type="button"
          className="mini-icon-btn"
          onClick={onRefresh}
          disabled={refreshing}
          aria-label="Обновить список проверок"
        >
          {refreshing ? <Spinner /> : <RefreshCw size={17} />}
        </button>
      </div>

      {syncError && <Alert tone="error">{syncError}</Alert>}

      {refreshing && ordered.length === 0 ? (
        <SkeletonList />
      ) : ordered.length === 0 ? (
        <div className="mini-card">
          <EmptyState title="Проверок пока нет">
            Запустите проверку на главной — она появится здесь.
          </EmptyState>
        </div>
      ) : (
        <div className="mini-stack mini-stack--tight">
          {ordered.map((run) => (
            <button
              type="button"
              className="mini-run mini-run--list"
              key={run.id}
              onClick={() => onOpenRun(run.id)}
            >
              <div className="mini-run__main">
                <div className="mini-run__meta">
                  <Pill tone={executionTone(run.execution_status)} dot={isActiveRun(run)}>
                    {executionLabel(run.execution_status)}
                  </Pill>
                  <span className="mini-run__when">{formatWhen(run.created_at)}</span>
                </div>
                <div className="mini-run__verdict">{verdictFor(run).text}</div>
                <div className="mini-run__summary">{findingsSummary(run)}</div>
              </div>
              <ChevronRight size={18} className="mini-run__chevron" aria-hidden="true" />
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
