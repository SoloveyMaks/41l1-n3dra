import React from 'react';
import {
  Play, Upload, Clock, FileText, Bug,
  Sparkles, Layers, Network, GitCompare, RefreshCw, LogOut, User, Settings
} from 'lucide-react';
import { AppTab, RunSummary, SecurityVerdict, SessionMode, SessionUser } from '../types';

interface NavbarProps {
  activeTab: AppTab;
  setActiveTab: (tab: AppTab) => void;
  onOpenUpload: () => void;
  onTriggerRun: () => void;
  onOpenAiTest: () => void;
  isRunning: boolean;
  latestVerdict?: SecurityVerdict;
  findingsCount?: number;
  runs: RunSummary[];
  currentRunId: string | null;
  runsLoading: boolean;
  onSelectRun: (runId: string) => void;
  onRefreshRuns: () => void;
  onLogout: () => void;
  isAdmin?: boolean;
  user?: SessionUser | null;
  mode?: SessionMode | null;
  onOpenAccount: () => void;
}

const TAB_ITEMS: Array<{ id: AppTab; label: string; icon: React.ComponentType<{ size?: number }> }> = [
  { id: 'editor', label: 'Редактор', icon: Layers },
  { id: 'live', label: 'Ход проверки', icon: Play },
  { id: 'findings', label: 'Проблемы', icon: Bug },
  { id: 'map', label: 'Карта доступа', icon: Network },
  { id: 'report', label: 'Отчёт', icon: FileText },
  { id: 'comparison', label: 'Сравнение', icon: GitCompare },
  { id: 'settings', label: 'Настройки', icon: Settings },
  { id: 'schedule', label: 'Автозапуск', icon: Clock },
];

const STATUS_LABEL: Record<RunSummary['execution_status'], string> = {
  queued: 'В очереди',
  running: 'Выполняется',
  succeeded: 'Завершена',
  failed: 'Ошибка',
  interrupted: 'Остановлена',
  cancelled: 'Отменена',
};

const VERDICT_LABEL: Record<RunSummary['security_verdict'], string> = {
  pending: 'Ожидает',
  pass: 'Нет блокирующих',
  fail: 'Есть проблемы',
  inconclusive: 'Неполная',
};

const shortRun = (run: RunSummary) => {
  const when = new Date(run.created_at);
  const stamp = Number.isNaN(when.getTime())
    ? '—'
    : `${when.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' })} ${when.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}`;
  return `${STATUS_LABEL[run.execution_status] || run.execution_status} · ${VERDICT_LABEL[run.security_verdict] || run.security_verdict} · ${stamp}`;
};

export const Navbar: React.FC<NavbarProps> = ({
  activeTab,
  setActiveTab,
  onOpenUpload,
  onTriggerRun,
  onOpenAiTest,
  isRunning,
  latestVerdict,
  findingsCount,
  runs,
  currentRunId,
  runsLoading,
  onSelectRun,
  onRefreshRuns,
  onLogout,
  isAdmin = true,
  user,
  mode,
  onOpenAccount,
}) => {
  const visibleTabs = TAB_ITEMS.filter((item) => isAdmin || item.id !== 'schedule');
  const accountLabel = user?.displayName || user?.email || (mode === 'demo' ? 'Demo' : 'Аккаунт');
  const accountInitial = accountLabel.slice(0, 1).toUpperCase();
  return (
    <header className="min-h-14 bg-slate-900 border-b border-slate-800 px-3 sm:px-4 py-2 flex flex-wrap xl:flex-nowrap items-center gap-2 xl:gap-3 z-20 select-none">
      <div className="flex items-center gap-2.5 min-w-0">
        <div className="h-9 w-9 rounded-xl overflow-hidden shadow-lg shadow-indigo-500/20 shrink-0">
          <img src="/nedra.svg" alt="NEDRA" className="h-full w-full" />
        </div>
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="font-bold text-sm tracking-tight text-white whitespace-nowrap">
              AI Security Workflow
            </span>
            <span className="hidden sm:inline px-1.5 py-0.5 rounded text-[10px] font-semibold bg-indigo-950 text-indigo-300 border border-indigo-800">
              Cyber Garden 2026
            </span>
          </div>
          <p className="hidden sm:block text-[10px] text-slate-400 truncate">
            Банк «Центр-инвест» • Конструктор проверок безопасности
          </p>
        </div>
      </div>

      <nav className="order-3 xl:order-none w-full xl:w-auto flex items-center gap-1 bg-slate-950/60 p-1 rounded-xl border border-slate-800 overflow-x-auto shrink-0">
        {visibleTabs.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              onClick={() => setActiveTab(item.id)}
              className={`flex items-center gap-1.5 px-2.5 sm:px-3 py-1.5 rounded-lg text-xs font-medium transition cursor-pointer shrink-0 relative ${
                activeTab === item.id
                  ? 'bg-indigo-600 text-white shadow-sm'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
              }`}
            >
              <Icon size={14} />
              <span>{item.label}</span>
              {item.id === 'live' && isRunning && (
                <span className="w-2 h-2 rounded-full bg-blue-400 animate-ping absolute -top-0.5 -right-0.5" />
              )}
              {item.id === 'findings' && findingsCount !== undefined && findingsCount > 0 && (
                <span className="ml-0.5 px-1.5 py-0.2 rounded-full text-[10px] bg-rose-950 text-rose-300 border border-rose-800">
                  {findingsCount}
                </span>
              )}
              {item.id === 'report' && latestVerdict && (
                <span
                  className={`ml-0.5 px-1.5 py-0.2 rounded text-[9px] font-bold uppercase ${
                    latestVerdict === 'fail'
                      ? 'bg-rose-950 text-rose-300 border border-rose-800'
                      : latestVerdict === 'pass'
                      ? 'bg-emerald-950 text-emerald-300 border border-emerald-800'
                      : 'bg-yellow-950 text-yellow-300 border border-yellow-800'
                  }`}
                >
                  {latestVerdict}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      <div className="order-2 xl:order-none ml-auto flex items-center gap-1.5 sm:gap-2 min-w-0">
        <div className="flex items-center gap-1 rounded-lg border border-slate-700 bg-slate-950/60 px-1.5 py-1 min-w-0">
          <span className="hidden 2xl:inline text-[10px] text-slate-500 whitespace-nowrap">История</span>
          <select
            aria-label="История запусков"
            value={currentRunId || ''}
            onChange={(event) => event.target.value && onSelectRun(event.target.value)}
            disabled={runsLoading || runs.length === 0}
            className="max-w-[150px] sm:max-w-[210px] bg-transparent text-[11px] text-slate-200 outline-none disabled:text-slate-500"
            title={currentRunId || 'Выберите запуск из истории'}
          >
            <option value="">{runs.length ? 'Выберите запуск' : 'Нет запусков'}</option>
            {runs.map((run) => (
              <option key={run.id} value={run.id}>
                {shortRun(run)}
              </option>
            ))}
          </select>
          <button
            onClick={onRefreshRuns}
            disabled={runsLoading}
            title="Обновить историю запусков"
            className="p-1 rounded text-slate-400 hover:text-slate-100 hover:bg-slate-800 disabled:opacity-50"
          >
            <RefreshCw size={13} className={runsLoading ? 'animate-spin' : ''} />
          </button>
        </div>

        {isAdmin && (
          <button
            onClick={onOpenAiTest}
            className="flex items-center gap-1.5 px-2 py-1.5 rounded-lg bg-fuchsia-950/60 hover:bg-fuchsia-900 border border-fuchsia-800/80 text-fuchsia-300 text-xs font-medium transition cursor-pointer"
            title="Синтетический тест валидатора AI для жюри"
          >
            <Sparkles size={13} />
            <span className="hidden lg:inline">Тест AI</span>
          </button>
        )}

        <button
          onClick={onOpenUpload}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 text-xs font-medium transition cursor-pointer"
          title="ZIP или публичный репозиторий"
        >
          <Upload size={13} />
          <span className="hidden sm:inline">ZIP или репозиторий</span>
        </button>

        <button
          onClick={onTriggerRun}
          disabled={isRunning}
          className={`flex items-center gap-1.5 px-3 sm:px-4 py-1.5 rounded-lg text-xs font-bold transition shadow-lg cursor-pointer ${
            isRunning
              ? 'bg-slate-700 text-slate-400 cursor-not-allowed'
              : 'bg-emerald-600 hover:bg-emerald-500 text-white shadow-emerald-600/20'
          }`}
        >
          <Play size={13} fill="currentColor" />
          <span>{isRunning ? 'Выполняется…' : 'Запустить проверку'}</span>
        </button>

        <button
          onClick={onOpenAccount}
          className="flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-1.5 py-1 text-[11px] text-slate-300 hover:bg-slate-800 hover:text-white"
          title="Аккаунт и история запусков"
        >
          <span className="flex h-5 w-5 items-center justify-center rounded-full bg-indigo-600/30 text-[10px] font-bold text-indigo-200">
            {mode === 'demo' ? <User size={11} /> : accountInitial}
          </span>
          <span className="hidden xl:inline max-w-[110px] truncate">{accountLabel}</span>
        </button>

        <button
          onClick={onLogout}
          className="flex items-center gap-1 rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5 text-[11px] text-slate-400 hover:bg-slate-800 hover:text-white"
          title="Завершить сессию"
        >
          <LogOut size={13} />
          <span className="hidden lg:inline">Выйти</span>
        </button>
      </div>
    </header>
  );
};
