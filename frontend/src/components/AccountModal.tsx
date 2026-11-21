import React from 'react';
import { History, Loader2, LogOut, ShieldCheck, User, X } from 'lucide-react';
import { ProfileData, RunHistoryItem, SessionState } from '../types';

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
    hour: '2-digit',
    minute: '2-digit',
  });
};

export const AccountModal: React.FC<AccountModalProps> = ({
  open,
  session,
  profile,
  history,
  loading,
  error,
  onClose,
  onLogout,
  onSelectRun,
}) => {
  if (!open) return null;

  const user = profile?.user || session?.user || null;
  const isDemo = (session?.mode || 'demo') === 'demo';
  const displayName = user?.displayName || user?.email || 'Demo-пользователь';
  const summary = profile?.history;

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
          {loading && (
            <div className="flex items-center justify-center gap-2 py-6 text-sm text-slate-400">
              <Loader2 size={16} className="animate-spin" /> Загружаем профиль и историю…
            </div>
          )}

          {error && (
            <div className="rounded-lg border border-amber-900 bg-amber-950/40 px-3 py-2 text-xs text-amber-200">
              {error}
            </div>
          )}

          {isDemo && !loading && (
            <div className="rounded-lg border border-slate-700 bg-slate-950/60 px-3 py-2 text-[11px] leading-relaxed text-slate-400">
              Demo-сессия не является личным аккаунтом: история общая, профиль и счётчики по аккаунту недоступны. Для личной истории войдите или зарегистрируйтесь заново.
            </div>
          )}

          {summary && !isDemo && (
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
          )}

          <section>
            <div className="mb-2 flex items-center gap-2 text-[10px] font-bold uppercase tracking-wider text-slate-500">
              <History size={12} /> История запусков
            </div>
            {history.length === 0 && !loading ? (
              <p className="rounded-lg border border-dashed border-slate-800 px-3 py-4 text-center text-xs text-slate-500">
                Запусков пока нет. Загрузите проект и запустите проверку.
              </p>
            ) : (
              <div className="space-y-1.5">
                {history.map((run) => (
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
                      <span className="font-mono text-[11px] text-slate-300">{run.id.slice(0, 8)}</span>
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
                      <span className="text-slate-600">{run.workflow_id}</span>
                    </div>
                  </button>
                ))}
              </div>
            )}
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