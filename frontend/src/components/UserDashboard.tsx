import React, { useState } from 'react';
import {
  ArrowRight, CheckCircle2, FileArchive, History, LogOut, Play,
  ShieldAlert, ShieldCheck, Upload, AlertTriangle, RefreshCw, Loader2,
} from 'lucide-react';
import {
  ArtifactManifest, Finding, NodeRunRecord, ReportData, RunSummary, SecurityVerdict,
} from '../types';
import { FindingsView } from './FindingsView';
import { ReportView } from './ReportView';
import { SecurityMapScreen } from './SecurityMapScreen';
import { ComparisonView } from './ComparisonView';
import { coverageLabel, getCanonicalCoverage } from '../coverage';

type UserSection = 'check' | 'problems' | 'map' | 'report' | 'comparison';

interface UserDashboardProps {
  artifacts: ArtifactManifest[];
  currentArtifactId: string;
  runs: RunSummary[];
  currentRun: RunSummary | null;
  currentRunId: string | null;
  findings: Finding[];
  report: ReportData | null;
  nodeRuns: NodeRunRecord[];
  failureReason?: string;
  isRunning: boolean;
  runsLoading: boolean;
  onUpload: () => void;
  onLoadSample: () => Promise<void> | void;
  onSelectArtifact: (artifactId: string) => void;
  onRun: () => void;
  onSelectRun: (runId: string) => void;
  onRefreshRuns: () => void;
  onLogout: () => void;
}

const verdictLabel = (verdict?: SecurityVerdict, findingsCount = 0): string => {
  if (verdict === 'pass') {
    return findingsCount > 0
      ? 'Нет блокирующих проблем по действующей политике'
      : 'Проблем не выявлено в проверенной части';
  }
  if (verdict === 'fail') return 'Найдены проблемы';
  if (verdict === 'inconclusive') return 'Неполная проверка';
  return 'Проверка идёт';
};

const executionLabel = (run: RunSummary | null): string => {
  if (!run) return 'Проверка ещё не запускалась';
  if (run.execution_status === 'queued') return 'В очереди';
  if (run.execution_status === 'running') return 'Выполняется';
  if (run.execution_status === 'succeeded') return 'Завершена';
  if (run.execution_status === 'failed') return 'Завершилась с ошибкой';
  return 'Остановлена';
};

const verdictTone = (verdict?: SecurityVerdict): string => {
  if (verdict === 'fail') return 'border-rose-900 bg-rose-950/40 text-rose-200';
  if (verdict === 'pass') return 'border-emerald-900 bg-emerald-950/40 text-emerald-200';
  if (verdict === 'inconclusive') return 'border-amber-900 bg-amber-950/40 text-amber-200';
  return 'border-slate-800 bg-slate-900 text-slate-300';
};

const formatDate = (value?: string): string => {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? '—'
    : date.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
};

const projectName = (artifactId: string | undefined, artifacts: ArtifactManifest[]): string =>
  artifacts.find((artifact) => artifact.id === artifactId)?.filename || 'Проект не выбран';

export const UserDashboard: React.FC<UserDashboardProps> = ({
  artifacts,
  currentArtifactId,
  runs,
  currentRun,
  currentRunId,
  findings,
  report,
  nodeRuns,
  failureReason,
  isRunning,
  runsLoading,
  onUpload,
  onLoadSample,
  onSelectArtifact,
  onRun,
  onSelectRun,
  onRefreshRuns,
  onLogout,
}) => {
  const [section, setSection] = useState<UserSection>('check');
  const [sampleBusy, setSampleBusy] = useState(false);

  const selectedArtifact = artifacts.find((artifact) => artifact.id === currentArtifactId);
  const visibleArtifacts = artifacts.filter(
    (artifact, index) => artifacts.findIndex((item) => item.filename === artifact.filename) === index,
  );
  if (selectedArtifact && !visibleArtifacts.some((artifact) => artifact.id === selectedArtifact.id)) {
    visibleArtifacts.unshift(selectedArtifact);
  }
  const selectedProjectName = selectedArtifact?.filename || 'Проект не выбран';
  const coverage = getCanonicalCoverage(nodeRuns);
  const recentRuns = runs
    .filter((run) => run.workflow_id === 'wf_secure_pr_gate' && run.trigger_type !== 'schedule')
    .slice(0, 8);

  const handleSample = async () => {
    setSampleBusy(true);
    try {
      await onLoadSample();
    } finally {
      setSampleBusy(false);
    }
  };

  const nav: Array<{ id: UserSection; label: string; count?: number }> = [
    { id: 'check', label: 'Проверка' },
    { id: 'problems', label: 'Проблемы', count: findings.length },
    { id: 'map', label: 'Карта доступа' },
    { id: 'report', label: 'Отчёт' },
  ];

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 bg-slate-900">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-3 px-4 py-3">
          <div className="flex items-center gap-2.5">
            <div className="rounded-xl bg-gradient-to-tr from-indigo-600 to-violet-500 p-2 text-white">
              <ShieldCheck size={19} />
            </div>
            <div>
              <div className="text-sm font-bold text-white">Проверка безопасности кода</div>
              <div className="text-[11px] text-slate-400">Проект → проверка → понятный результат</div>
            </div>
          </div>
          <nav className="order-3 flex w-full gap-1 overflow-x-auto rounded-xl border border-slate-800 bg-slate-950/60 p-1 sm:order-none sm:ml-auto sm:w-auto">
            {nav.map((item) => (
              <button
                key={item.id}
                onClick={() => setSection(item.id)}
                className={`flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium ${
                  section === item.id
                    ? 'bg-indigo-600 text-white'
                    : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
                }`}
              >
                {item.label}
                {item.count ? (
                  <span className="rounded-full border border-rose-800 bg-rose-950 px-1.5 text-[10px] text-rose-300">
                    {item.count}
                  </span>
                ) : null}
              </button>
            ))}
          </nav>
          <button
            onClick={onLogout}
            className="flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-[11px] text-slate-400 hover:bg-slate-800 hover:text-white"
          >
            <LogOut size={13} /> Выйти
          </button>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto">
        {section === 'check' && (
          <div className="mx-auto max-w-6xl space-y-5 px-4 py-6">
            <div>
              <h1 className="text-xl font-bold text-white">Проверка проекта</h1>
              <p className="mt-1 text-sm text-slate-400">
                Три шага: выберите проект, запустите проверку и посмотрите результат.
              </p>
            </div>

            <div className="grid gap-4 lg:grid-cols-3">
              <section className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
                <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-indigo-300">
                  <span className="flex h-6 w-6 items-center justify-center rounded-full bg-indigo-950">1</span>
                  Выберите проект
                </div>
                <div className="rounded-xl border border-slate-800 bg-slate-950 p-3">
                  <div className="flex items-center gap-2 text-sm text-slate-200">
                    <FileArchive size={16} className="text-indigo-400" />
                    <span className="truncate">{selectedProjectName}</span>
                  </div>
                  {selectedArtifact && (
                    <div className="mt-1 text-[11px] text-slate-500">
                      {selectedArtifact.kotlin_file_count} файлов Kotlin · {selectedArtifact.file_count} файлов всего
                    </div>
                  )}
                </div>
                {visibleArtifacts.length > 1 && (
                  <select
                    aria-label="Выбрать проект"
                    value={currentArtifactId}
                    onChange={(event) => onSelectArtifact(event.target.value)}
                    className="mt-3 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200"
                  >
                    {visibleArtifacts.map((artifact) => (
                      <option key={artifact.id} value={artifact.id}>{artifact.filename}</option>
                    ))}
                  </select>
                )}
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    onClick={onUpload}
                    className="flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-xs text-slate-200 hover:bg-slate-700"
                  >
                    <Upload size={13} /> Загрузить ZIP
                  </button>
                  <button
                    onClick={() => void handleSample()}
                    disabled={sampleBusy}
                    className="flex items-center gap-1.5 rounded-lg border border-indigo-900 bg-indigo-950/60 px-3 py-2 text-xs text-indigo-200 hover:bg-indigo-900 disabled:opacity-60"
                  >
                    {sampleBusy ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
                    Попробовать пример
                  </button>
                </div>
              </section>

              <section className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
                <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-emerald-300">
                  <span className="flex h-6 w-6 items-center justify-center rounded-full bg-emerald-950">2</span>
                  Запустите проверку
                </div>
                <div className="text-sm font-semibold text-slate-100">Стандартная проверка</div>
                <p className="mt-1 text-xs leading-relaxed text-slate-400">
                  Проверим секреты, зависимости и код Kotlin. Ничего настраивать не нужно.
                </p>
                <button
                  onClick={onRun}
                  disabled={isRunning || !currentArtifactId}
                  className="mt-4 flex w-full items-center justify-center gap-2 rounded-lg bg-emerald-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
                >
                  {isRunning ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />}
                  {isRunning ? 'Проверяем…' : 'Запустить проверку'}
                </button>
                {!currentArtifactId && (
                  <p className="mt-2 text-[11px] text-amber-300">Сначала выберите или загрузите проект.</p>
                )}
              </section>

              <section className={`rounded-2xl border p-5 ${verdictTone(currentRun?.security_verdict)}`}>
                <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide opacity-80">
                  <span className="flex h-6 w-6 items-center justify-center rounded-full bg-black/20">3</span>
                  Посмотрите результат
                </div>
                {currentRun ? (
                  <>
                    <div className="text-base font-bold">{verdictLabel(currentRun.security_verdict, currentRun.total_findings)}</div>
                    <div className="mt-1 text-xs opacity-80">{executionLabel(currentRun)}</div>
                    <div className="mt-1 text-xs opacity-80">Проект проверки: {projectName(currentRun.artifact_id, artifacts)}</div>
                    <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
                      <div className="rounded-lg bg-black/20 p-2">
                        <div className="opacity-60">Проблемы</div>
                        <div className="text-lg font-bold">{currentRun.total_findings}</div>
                      </div>
                      <div className="rounded-lg bg-black/20 p-2">
                        <div className="opacity-60">Покрытие</div>
                        <div className="text-lg font-bold">
                          {coverageLabel(coverage)}
                        </div>
                      </div>
                    </div>
                    <div className="mt-3 flex flex-wrap gap-1.5">
                      <button onClick={() => setSection('problems')} className="rounded-lg bg-black/20 px-2.5 py-1.5 text-[11px] hover:bg-black/30">
                        Проблемы
                      </button>
                      <button onClick={() => setSection('map')} className="rounded-lg bg-black/20 px-2.5 py-1.5 text-[11px] hover:bg-black/30">
                        Карта доступа
                      </button>
                      <button onClick={() => setSection('comparison')} className="rounded-lg bg-black/20 px-2.5 py-1.5 text-[11px] hover:bg-black/30">
                        Сравнить проверки
                      </button>
                      <button onClick={() => setSection('report')} className="rounded-lg bg-black/20 px-2.5 py-1.5 text-[11px] hover:bg-black/30">
                        Отчёт
                      </button>
                    </div>
                  </>
                ) : (
                  <>
                    <div className="text-sm font-semibold">Результат появится здесь</div>
                    <p className="mt-1 text-xs opacity-80">После проверки покажем проблемы, покрытие и отчёт.</p>
                  </>
                )}
              </section>
            </div>

            <section className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2 text-sm font-semibold text-slate-100">
                  <History size={16} className="text-slate-400" /> История проверок
                </div>
                <button
                  onClick={onRefreshRuns}
                  disabled={runsLoading}
                  className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-2.5 py-1.5 text-[11px] text-slate-400 hover:bg-slate-800 disabled:opacity-50"
                >
                  <RefreshCw size={12} className={runsLoading ? 'animate-spin' : ''} /> Обновить
                </button>
              </div>
              {recentRuns.length === 0 ? (
                <p className="text-xs text-slate-500">Проверок пока нет.</p>
              ) : (
                <div className="space-y-1.5">
                  {recentRuns.map((run) => (
                    <button
                      key={run.id}
                      onClick={() => { onSelectRun(run.id); setSection('check'); }}
                      className={`flex w-full flex-wrap items-center justify-between gap-2 rounded-lg border px-3 py-2 text-left text-xs ${
                        run.id === currentRunId
                          ? 'border-indigo-700 bg-indigo-950/40'
                          : 'border-slate-800 bg-slate-950/50 hover:bg-slate-800/60'
                      }`}
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-slate-200">
                          {projectName(run.artifact_id, artifacts)}
                        </span>
                        <span className="text-[11px] text-slate-500">{formatDate(run.created_at)}</span>
                      </span>
                      <span className="flex items-center gap-2 text-[11px]">
                        <span className={run.security_verdict === 'fail' ? 'text-rose-300' : run.security_verdict === 'pass' ? 'text-emerald-300' : 'text-amber-300'}>
                          {verdictLabel(run.security_verdict, run.total_findings)}
                        </span>
                        <span className="text-slate-500">{run.total_findings} проблем</span>
                        <ArrowRight size={13} className="text-slate-500" />
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </section>
          </div>
        )}

        {section === 'problems' && (
          findings.length > 0 ? (
            <FindingsView findings={findings} />
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center text-slate-400">
              <CheckCircle2 size={34} className="text-emerald-500" />
              <p className="text-sm">Проблем в выбранной проверке нет либо она ещё не завершена.</p>
              <button onClick={() => setSection('check')} className="text-xs text-indigo-300 hover:text-indigo-200">
                Вернуться к проверке
              </button>
            </div>
          )
        )}

        {section === 'map' && <SecurityMapScreen key={currentRunId ?? 'no-run'} runId={currentRunId} />}

        {section === 'report' && (
          <ReportView
            report={report}
            runId={currentRunId}
            runStatus={currentRun?.execution_status}
            failureReason={failureReason}
            nodeRuns={nodeRuns}
          />
        )}

        {section === 'comparison' && (
          <div className="mx-auto max-w-5xl p-4">
            <button onClick={() => setSection('check')} className="mb-3 text-xs text-indigo-300 hover:text-indigo-200">
              ← Назад к проверке
            </button>
            <ComparisonView runs={runs} onRefreshRuns={onRefreshRuns} />
          </div>
        )}
      </main>
    </div>
  );
};