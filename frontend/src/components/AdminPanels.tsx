import React from 'react';
import { Activity, AlertTriangle, Bot, Database, FolderKanban, Gauge, KeyRound, ShieldCheck } from 'lucide-react';
import { AdminOverview, ArtifactManifest, Finding, NodeRunRecord, ReportData, RunSummary, SessionState } from '../types';
import { coverageLabel, coverageReason, getCanonicalCoverage, scopeLabel } from '../coverage';
import { AdminSection } from './AdminShell';

const dateLabel = (value?: string): string => {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
};

const verdictLabel = (run?: RunSummary | null): string => {
  if (!run) return 'Нет проверки';
  if (run.security_verdict === 'fail') return 'Найдены проблемы';
  if (run.security_verdict === 'pass') return 'Проблем не выявлено';
  if (run.security_verdict === 'inconclusive') return 'Неполная проверка';
  return 'Проверка идёт';
};

const Stat: React.FC<{ label: string; value: React.ReactNode; tone?: string }> = ({ label, value, tone }) => (
  <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-3">
    <div className="text-[11px] text-slate-500">{label}</div>
    <div className={`mt-1 text-xl font-bold ${tone || 'text-slate-100'}`}>{value}</div>
  </div>
);

export const AdminOverviewPanel: React.FC<{
  overview: AdminOverview | null;
  artifacts: ArtifactManifest[];
  runs: RunSummary[];
  findings: Finding[];
  currentRun: RunSummary | null;
  report: ReportData | null;
  nodeRuns: NodeRunRecord[];
}> = ({ overview, artifacts, runs, findings, currentRun, report, nodeRuns }) => {
  const latestRun = runs[0] || null;
  return (
  <div className="overflow-y-auto p-5">
    <div className="mb-4 flex flex-wrap items-end justify-between gap-2">
      <div>
        <h1 className="text-lg font-bold text-white">Обзор</h1>
        <p className="text-xs text-slate-400">Реальные счётчики текущего workspace.</p>
      </div>
      <span className="rounded-full border border-slate-700 px-2 py-1 text-[10px] text-slate-400">админ-режим</span>
    </div>
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <Stat label="Проекты (workspace)" value={overview?.counts?.projects ?? artifacts.length} />
      <Stat label="Проверки (workspace)" value={overview?.counts?.runs ?? runs.length} />
      <Stat label="Проблемы (выбранная)" value={findings.length} tone={findings.some((f) => f.severity === 'critical') ? 'text-rose-300' : 'text-slate-100'} />
      <Stat label="Покрытие (canonical)" value={coverageLabel(getCanonicalCoverage(nodeRuns))} />
    </div>
    <div className="mt-4 grid gap-4 lg:grid-cols-2">
      <section className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">Последняя проверка</h2>
        {latestRun ? (
          <div className="space-y-1 text-sm">
            <div className="font-semibold text-slate-100">{verdictLabel(latestRun)}</div>
            <div className="text-xs text-slate-400">
              {artifacts.find((a) => a.id === latestRun.artifact_id)?.filename || 'Проект не указан'} · {dateLabel(latestRun.created_at)}
            </div>
            <div className="text-xs text-slate-400">Проблем: {latestRun.total_findings} · критичных: {latestRun.critical_count} · высоких: {latestRun.high_count}</div>
            {currentRun && currentRun.id !== latestRun.id && (
              <div className="mt-2 border-t border-slate-800 pt-2 text-[11px] text-amber-300">
                Сейчас выбрана другая проверка: {artifacts.find((a) => a.id === currentRun.artifact_id)?.filename || 'проект не указан'} · {dateLabel(currentRun.created_at)}
              </div>
            )}
          </div>
        ) : (
          <p className="text-xs text-slate-500">Проверок ещё нет.</p>
        )}
      </section>
      <section className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">Состояние backend</h2>
        {overview ? (
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
            <dt className="text-slate-500">База</dt><dd className="text-slate-200">{overview.db?.healthy ? `ok${overview.db.latencyMs != null ? ` · ${overview.db.latencyMs} ms` : ''}` : 'недоступна'}</dd>
            <dt className="text-slate-500">Модель</dt><dd className="text-slate-200">{overview.ai?.model || '—'}</dd>
            <dt className="text-slate-500">Вектор</dt><dd className="text-slate-200">{overview.vector?.status === 'disabled' ? 'выключен' : overview.vector?.status || (overview.vector?.enabled ? 'включён' : 'выключен')}</dd>
            <dt className="text-slate-500">Админка</dt><dd className="text-slate-200">{overview.auth?.adminAvailable ? 'доступна' : 'выключена'}</dd>
          </dl>
        ) : (
          <p className="text-xs text-slate-500">DTO `/api/admin/overview` пока не отдаётся этим backend; счётчики выше построены по реальным данным приложения.</p>
        )}
      </section>
    </div>
  </div>
  );
};

export const AdminProjectsPanel: React.FC<{
  artifacts: ArtifactManifest[];
  currentArtifactId: string;
  onSelectArtifact: (id: string) => void;
  runs: RunSummary[];
  currentRunId: string | null;
  onSelectRun: (id: string) => void;
  onSection: (section: AdminSection) => void;
  onRefreshRuns: () => void;
}> = ({ artifacts, currentArtifactId, onSelectArtifact, runs, currentRunId, onSelectRun, onSection, onRefreshRuns }) => (
  <div className="overflow-y-auto p-5">
    <div className="mb-4 flex items-center justify-between gap-2">
      <div>
        <h1 className="text-lg font-bold text-white">Проекты и проверки</h1>
        <p className="text-xs text-slate-400">Выберите проект или откройте сохранённую проверку.</p>
      </div>
      <button onClick={onRefreshRuns} className="rounded-lg border border-slate-700 px-2.5 py-1.5 text-xs text-slate-300 hover:bg-slate-800">Обновить</button>
    </div>
    <section className="mb-4 rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">Проекты</h2>
      {artifacts.length === 0 ? <p className="text-xs text-slate-500">Проектов нет.</p> : (
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {artifacts.map((artifact) => (
            <button
              key={artifact.id}
              onClick={() => onSelectArtifact(artifact.id)}
              className={`rounded-lg border p-3 text-left text-xs ${artifact.id === currentArtifactId ? 'border-indigo-700 bg-indigo-950/40' : 'border-slate-800 bg-slate-950/50 hover:bg-slate-800/60'}`}
            >
              <div className="font-medium text-slate-200">{artifact.filename}</div>
              <div className="mt-1 text-[11px] text-slate-500">{artifact.kotlin_file_count} Kotlin · {artifact.file_count} файлов</div>
            </button>
          ))}
        </div>
      )}
    </section>
    <section className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">История проверок</h2>
      {runs.length === 0 ? <p className="text-xs text-slate-500">Проверок нет.</p> : (
        <div className="space-y-1.5">
          {runs.slice(0, 30).map((run) => (
            <div key={run.id} className={`flex flex-wrap items-center justify-between gap-2 rounded-lg border px-3 py-2 text-xs ${run.id === currentRunId ? 'border-indigo-700 bg-indigo-950/30' : 'border-slate-800'}`}>
              <button onClick={() => onSelectRun(run.id)} className="min-w-0 text-left">
                <div className="truncate text-slate-200">{artifacts.find((a) => a.id === run.artifact_id)?.filename || 'Проект не указан'}</div>
                <div className="text-[11px] text-slate-500">{dateLabel(run.created_at)} · проблем: {run.total_findings}</div>
              </button>
              <div className="flex items-center gap-2">
                <span className={run.security_verdict === 'fail' ? 'text-rose-300' : run.security_verdict === 'pass' ? 'text-emerald-300' : 'text-amber-300'}>{verdictLabel(run)}</span>
                <button onClick={() => { onSelectRun(run.id); onSection('report'); }} className="rounded border border-slate-700 px-2 py-1 text-[10px] text-slate-300 hover:bg-slate-800">Отчёт</button>
                <button onClick={() => { onSelectRun(run.id); onSection('problems'); }} className="rounded border border-slate-700 px-2 py-1 text-[10px] text-slate-300 hover:bg-slate-800">Проблемы</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  </div>
);

export const AdminAnalyzersPanel: React.FC<{ report: ReportData | null; nodeRuns: NodeRunRecord[] }> = ({ report, nodeRuns }) => (
  <div className="overflow-y-auto p-5">
    <h1 className="text-lg font-bold text-white">Анализаторы и покрытие</h1>
    <p className="mb-4 text-xs text-slate-400">Только фактические данные выбранной проверки; настройки здесь не меняются.</p>
    {!report ? (
      <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4 text-xs text-slate-500">Для выбранной проверки нет отчёта. Выберите завершённую проверку.</div>
    ) : (
      <div className="grid gap-4 lg:grid-cols-2">
        <section className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">Покрытие</h2>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
            <dt className="text-slate-500">Статус покрытия</dt><dd>{coverageLabel(getCanonicalCoverage(nodeRuns))}</dd>
            <dt className="text-slate-500">Файлов проверено</dt><dd>{report.passport.files_scanned} из {report.passport.files_scanned + report.passport.files_skipped} <span className="text-slate-500">(не полнота правил)</span></dd>
            <dt className="text-slate-500">Пропущенные области</dt><dd title={getCanonicalCoverage(nodeRuns)?.skippedScopes.join(', ')}>{getCanonicalCoverage(nodeRuns)?.skippedScopes.length ? getCanonicalCoverage(nodeRuns)?.skippedScopes.map(scopeLabel).join(', ') : '—'}</dd>
            <dt className="text-slate-500">Почему</dt><dd>{coverageReason(getCanonicalCoverage(nodeRuns))}</dd>
            <dt className="text-slate-500">Строк кода</dt><dd>{report.passport.lines_of_code}</dd>
            <dt className="text-slate-500">Ошибок парсера</dt><dd>{report.passport.parser_errors}</dd>
            <dt className="text-slate-500">Неразрешённых зависимостей</dt><dd>{report.passport.unresolved_dependencies}</dd>
                      </dl>
        </section>
        <section className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">Правила</h2>
          <div className="max-h-48 overflow-y-auto text-[11px] text-slate-300">
            {report.passport.rules_executed.length ? report.passport.rules_executed.map((rule) => <div key={rule} className="font-mono">{rule}</div>) : <span className="text-slate-500">Нет данных</span>}
          </div>
        </section>
        <section className="rounded-xl border border-slate-800 bg-slate-900/60 p-4 lg:col-span-2">
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">Предупреждения покрытия</h2>
          {report.passport.coverage_warnings.length ? (
            <ul className="list-disc space-y-1 pl-5 text-xs text-amber-200">{report.passport.coverage_warnings.map((w) => <li key={w}>{w}</li>)}</ul>
          ) : <p className="text-xs text-slate-500">Предупреждений нет.</p>}
          <p className="mt-3 text-[11px] text-slate-500">Выполнено узлов: {nodeRuns.length}; завершено: {nodeRuns.filter((n) => n.status === 'succeeded').length}; с ошибкой: {nodeRuns.filter((n) => n.status === 'failed').length}.</p>
        </section>
      </div>
    )}
  </div>
);

export const AdminAiPanel: React.FC<{ report: ReportData | null }> = ({ report }) => (
  <div className="overflow-y-auto p-5">
    <h1 className="text-lg font-bold text-white">AI и память исправлений</h1>
    <p className="mb-4 text-xs text-slate-400">Фактический статус выбранной проверки; поиск исправлений здесь не изображается.</p>
    {!report ? (
      <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4 text-xs text-slate-500">Нет отчёта для выбранной проверки.</div>
    ) : (
      <div className="grid gap-4 lg:grid-cols-2">
        <section className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">AI-объяснения</h2>
          <div className="text-sm text-slate-200">Статус: {report.ai_status || 'нет данных'}</div>
          <div className="mt-1 text-xs text-slate-400">Объяснений: {report.ai_items.length}</div>
          {report.ai_summary && <p className="mt-2 text-xs leading-relaxed text-slate-400">{report.ai_summary}</p>}
        </section>
        <section className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">Память исправлений</h2>
          <p className="text-xs text-slate-400">В текущем backend нет отдельного статуса памяти исправлений. Поиск не запускается и не имитируется.</p>
        </section>
      </div>
    )}
  </div>
);

export const AdminAccessPanel: React.FC<{ session: SessionState | null }> = ({ session }) => (
  <div className="overflow-y-auto p-5">
    <h1 className="text-lg font-bold text-white">Доступ и состояние</h1>
    <p className="mb-4 text-xs text-slate-400">Без секретов: только роль, сессия и доступность админки.</p>
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Stat label="Роль" value={session?.role === 'admin' ? 'Администратор' : 'Пользователь'} />
      <Stat label="Админка" value={session?.adminAvailable === false ? 'Недоступна' : 'Доступна'} />
      <Stat label="Сессия" value={session?.authenticated ? 'Активна' : 'Нет'} />
      <Stat label="Осталось" value={session ? `${Math.max(0, Math.round(session.expiresInSec / 60))} мин` : '—'} />
    </div>
    <div className="mt-4 rounded-xl border border-slate-800 bg-slate-900/60 p-4 text-xs text-slate-400">
      <div className="flex items-center gap-2 text-slate-300"><KeyRound size={14} /> Workspace</div>
      <div className="mt-1 break-all font-mono text-[11px] text-slate-500">{session?.workspaceId || '—'}</div>
    </div>
  </div>
);