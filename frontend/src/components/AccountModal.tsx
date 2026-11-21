import React from 'react';
import { History, Loader2, LogOut, ShieldCheck, User, X } from 'lucide-react';
import { ProfileData, RunHistoryItem, SessionState } from '../types';
import { apiClient } from '../api/client';

interface AccountModalProps {
  open: boolean;
  session: SessionState | null;
  profile: ProfileData | null;
  history: RunHistoryItem[];
  loading: boolean;
  error?: string | null;
  onClose: () => void;
  onLogout: () => void;
  onSelectRun: (runId: string) => void;
}

const STATUS_LABEL: Record<string, string> = {
  queued: 'В очереди',
  running: 'Выполняется',
  succeeded: 'Завершена',
  failed: 'Ошибка',
  interrupted: 'Остановлена',
  cancelled: 'Отменена',
};

const VERDICT_LABEL: Record<string, string> = {
  pending: 'Ожидает',
  pass: 'Нет блокирующих',
  fail: 'Есть проблемы',
  inconclusive: 'Неполная',
};

const formatWhen = (value?: string | null): string => {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
};

export const AccountModal: React.FC<AccountModalProps> = (props) => props.open && props.session?.authenticated
  ? <AccountContents key={`${props.session.mode}:${props.session.user?.userId || props.session.workspaceId}:${props.session.sessionId}`} {...props} />
  : null;

const AccountContents: React.FC<AccountModalProps> = ({
  session,
  profile,
  onClose,
  onLogout,
  onSelectRun,
}) => {
  const user = session?.user || null;
  const isDemo = (session?.mode || 'demo') === 'demo';
  const displayName = user?.displayName || user?.email || 'Demo-пользователь';
  const summary = profile?.user.userId === user?.userId ? profile?.history : null;
  const [filterProject, setFilterProject] = React.useState('');
  const [filterWorkflow, setFilterWorkflow] = React.useState('');
  const [filterStatus, setFilterStatus] = React.useState('');
  const [filterDate, setFilterDate] = React.useState('');
  const [filterUntil, setFilterUntil] = React.useState('');
  const [offset, setOffset] = React.useState(0);
  const [refresh, setRefresh] = React.useState(0);
  const [filteredHistory, setFilteredHistory] = React.useState<RunHistoryItem[]>([]);
  const [filterTotal, setFilterTotal] = React.useState(0);
  const [filterLoading, setFilterLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [filterOptions, setFilterOptions] = React.useState<{ projects: { id: string | null; name: string | null }[]; workflows: { id: string; name: string | null }[]; statuses: string[] }>({ projects: [], workflows: [], statuses: [] });

  React.useEffect(() => {
    let active = true;
    setFilterLoading(true);
    setError(null);
    setFilteredHistory([]);
    const since = filterDate ? new Date(`${filterDate}T00:00:00`) : null;
    const until = filterUntil ? new Date(`${filterUntil}T00:00:00`) : null;
    until?.setDate(until.getDate() + 1);
    if (since && until && since >= until) {
      setError('Дата окончания должна быть не раньше даты начала.');
      setFilterLoading(false);
      return;
    }
    void apiClient.getProfileHistory({
        limit: 20, offset,
        project: filterProject || undefined,
        workflow: filterWorkflow || undefined,
        status: filterStatus || undefined,
        since: since?.toISOString(),
        until: until?.toISOString(),
      }).then((result) => {
        if (!active) return;
        if (result.mode !== session?.mode || (result.mode === 'account' && result.userId !== user?.userId)) {
          setError('Аккаунт сменился. Закройте профиль и войдите заново.');
          return;
        }
        setFilteredHistory(result.items);
        setFilterTotal(result.total);
        setFilterOptions({ projects: result.projects || [], workflows: result.workflows || [], statuses: result.statuses || [] });
      }).catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : 'Не удалось загрузить историю');
      }).finally(() => { if (active) setFilterLoading(false); });
    return () => { active = false; };
  }, [filterProject, filterWorkflow, filterStatus, filterDate, filterUntil, offset, refresh, session?.mode, user?.userId]);

  const resetFilters = () => {
    setFilterProject(''); setFilterWorkflow(''); setFilterStatus(''); setFilterDate(''); setFilterUntil(''); setOffset(0);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-slate-950/75 p-3 sm:p-6" role="dialog" aria-modal="true" aria-label="Аккаунт и история">
      <div className="w-full max-w-2xl max-h-[88vh] overflow-hidden rounded-2xl border border-slate-800 bg-slate-900 shadow-2xl flex flex-col">
        <div className="flex items-start justify-between gap-3 border-b border-slate-800 p-4">
          <div className="flex items-center gap-3 min-w-0">
            <div className="rounded-xl bg-indigo-600/20 border border-indigo-800 p-2 text-indigo-300">
              {isDemo ? <ShieldCheck size={20} /> : <User size={20} />}
            </div>
            <div className="min-w-0">
              <h2 className="truncate text-sm font-semibold text-white">{displayName}</h2>
              <p className="truncate text-[11px] text-slate-400">
                {isDemo ? 'Demo-режим · общий workspace' : user?.email}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
            aria-label="Закрыть аккаунт"
          >
            <X size={18} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {filterLoading && (
            <div className="flex items-center justify-center gap-2 py-6 text-sm text-slate-400">
              <Loader2 size={16} className="animate-spin" /> Загружаем профиль и историю…
            </div>
          )}

          {error && (
            <div className="rounded-lg border border-amber-900 bg-amber-950/40 px-3 py-2 text-xs text-amber-200">
              {error}
            </div>
          )}

          {isDemo && (
            <div className="rounded-lg border border-amber-900/70 bg-amber-950/30 px-3 py-2 text-[11px] leading-relaxed text-amber-200/90">
              <strong className="text-amber-200">Общий demo-workspace.</strong>{' '}
              История ниже одна для всех, кто входит по demo-коду; личного профиля и счётчиков по аккаунту здесь нет.
              Для личной истории войдите или зарегистрируйтесь.
            </div>
          )}

          {summary && !isDemo && (
            <div>
              <div className="mb-1.5 text-[10px] uppercase tracking-wider text-slate-500">
                Всего в аккаунте · без фильтров
              </div>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                <div className="rounded-lg border border-slate-800 bg-slate-950/50 p-2.5">
                  <div className="text-[10px] uppercase tracking-wider text-slate-500">Проверок</div>
                  <div className="mt-0.5 text-lg font-semibold text-slate-100">{summary.runs}</div>
                </div>
                <div className="rounded-lg border border-slate-800 bg-slate-950/50 p-2.5">
                  <div className="text-[10px] uppercase tracking-wider text-slate-500">Находок</div>
                  <div className="mt-0.5 text-lg font-semibold text-slate-100">{summary.totalFindings}</div>
                </div>
                <div className="rounded-lg border border-slate-800 bg-slate-950/50 p-2.5">
                  <div className="text-[10px] uppercase tracking-wider text-slate-500">Ошибок</div>
                  <div className="mt-0.5 text-lg font-semibold text-rose-300">{summary.byStatus?.failed ?? 0}</div>
                </div>
                <div className="rounded-lg border border-slate-800 bg-slate-950/50 p-2.5">
                  <div className="text-[10px] uppercase tracking-wider text-slate-500">Последняя</div>
                  <div className="mt-1 text-[11px] font-medium text-slate-200">{formatWhen(summary.lastRunAt)}</div>
                </div>
              </div>
            </div>
          )}

          <section>
            <div className="mb-2 flex items-center gap-2 text-[10px] font-bold uppercase tracking-wider text-slate-500">
              <History size={12} /> История запусков
            </div>
            <div className="mb-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
              <select aria-label="Проект" value={filterProject} onChange={(event) => { setFilterProject(event.target.value); setOffset(0); }} className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-[11px] text-slate-300">
                <option value="">Все проекты</option>
                {filterOptions.projects.map((item) => <option key={item.id || '__none__'} value={item.id || '__none__'}>{item.name || 'Без проекта'}</option>)}
              </select>
              <select aria-label="Сценарий" value={filterWorkflow} onChange={(event) => { setFilterWorkflow(event.target.value); setOffset(0); }} className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-[11px] text-slate-300">
                <option value="">Все сценарии</option>
                {filterOptions.workflows.map((item) => <option key={item.id} value={item.id}>{item.name || item.id}</option>)}
              </select>
              <select aria-label="Статус" value={filterStatus} onChange={(event) => { setFilterStatus(event.target.value); setOffset(0); }} className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-[11px] text-slate-300">
                <option value="">Все статусы</option>
                {filterOptions.statuses.map((item) => <option key={item} value={item}>{STATUS_LABEL[item] || item}</option>)}
              </select>
              <label className="text-[10px] text-slate-400">С даты<input aria-label="Дата от" type="date" value={filterDate} onChange={(event) => { setFilterDate(event.target.value); setOffset(0); }} className="mt-1 block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-[11px] text-slate-300" /></label>
              <label className="text-[10px] text-slate-400">По дату включительно<input aria-label="Дата до" type="date" value={filterUntil} onChange={(event) => { setFilterUntil(event.target.value); setOffset(0); }} className="mt-1 block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-[11px] text-slate-300" /></label>
              <button type="button" onClick={resetFilters} className="text-xs text-indigo-300">Сбросить фильтры</button>
            </div>
            <div className="mb-2 flex justify-between gap-2 text-[10px] text-slate-500"><span aria-live="polite">{filterLoading ? 'Обновляем историю…' : error ? 'История недоступна' : `Запусков: ${filterTotal} · даты по вашему часовому поясу`}</span><button type="button" onClick={() => setRefresh((value) => value + 1)} disabled={filterLoading} className="text-indigo-300 disabled:opacity-50">Обновить</button></div>
            {filteredHistory.length === 0 && !filterLoading && !error ? (
              <p className="rounded-lg border border-dashed border-slate-800 px-3 py-4 text-center text-xs text-slate-500">
                {filterProject || filterWorkflow || filterStatus || filterDate || filterUntil ? 'По этим фильтрам запусков нет.' : 'Запусков пока нет. Загрузите проект и запустите проверку.'}
              </p>
            ) : (
              <div className="space-y-1.5">
                {filteredHistory.map((run) => (
                  <button
                    key={run.id}
                    type="button"
                    onClick={() => {
                      onSelectRun(run.id);
                      onClose();
                    }}
                    className="w-full rounded-lg border border-slate-800 bg-slate-950/40 px-3 py-2 text-left transition hover:border-indigo-700 hover:bg-slate-800/60"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                      <span className="font-mono text-[11px] text-slate-300">{run.project_name || run.artifact_id || 'Без проекта'}</span>
                      <span className="text-[10px] text-slate-500">{formatWhen(run.created_at)}</span>
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px]">
                      <span
                        className={`rounded border px-1.5 py-0.5 text-[10px] ${
                          run.execution_status === 'failed'
                            ? 'border-rose-800 bg-rose-950/60 text-rose-300'
                            : run.execution_status === 'running' || run.execution_status === 'queued'
                              ? 'border-blue-800 bg-blue-950/60 text-blue-300'
                              : 'border-emerald-800 bg-emerald-950/60 text-emerald-300'
                        }`}
                      >
                        {STATUS_LABEL[run.execution_status] || run.execution_status}
                      </span>
                      <span className="text-slate-400">
                        {VERDICT_LABEL[run.security_verdict] || run.security_verdict}
                      </span>
                      <span className="text-slate-500">находок: {run.total_findings}</span>
                      <span className="text-slate-400">{run.workflow_name || run.workflow_id}</span><span className="font-mono text-slate-500">{run.id.slice(0, 8)}</span>
                    </div>
                  </button>
                ))}
              </div>
            )}
            {!filterLoading && !error && filterTotal > 20 && <div className="mt-3 flex items-center justify-between text-xs text-slate-400"><button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))} className="text-indigo-300 disabled:opacity-40">Назад</button><span>{offset + 1}–{Math.min(offset + 20, filterTotal)} из {filterTotal}</span><button type="button" disabled={offset + 20 >= filterTotal} onClick={() => setOffset(offset + 20)} className="text-indigo-300 disabled:opacity-40">Дальше</button></div>}
          </section>
        </div>

        <div className="flex items-center justify-between gap-2 border-t border-slate-800 p-3">
          <span className="text-[10px] text-slate-600">
            {isDemo ? 'Demo workspace' : user?.role === 'admin' ? 'Администратор' : 'Личный workspace'}
          </span>
          <button
            type="button"
            onClick={onLogout}
            className="flex items-center gap-1.5 rounded-lg border border-rose-900 bg-rose-950/50 px-3 py-1.5 text-xs text-rose-300 hover:bg-rose-900/60"
          >
            <LogOut size={13} /> Выйти
          </button>
        </div>
      </div>
    </div>
  );
};
