import React from 'react';
import {
  Play, Upload, Clock, FileText, Bug,
  Sparkles, Layers, Network, GitCompare, LogOut, User, Settings
} from 'lucide-react';
import { AppTab, SecurityVerdict, SessionMode, SessionUser } from '../types';

interface NavbarProps {
  activeTab: AppTab;
  setActiveTab: (tab: AppTab) => void;
  onOpenUpload: () => void;
  onTriggerRun: () => void;
  onOpenAiTest: () => void;
  isRunning: boolean;
  latestVerdict?: SecurityVerdict;
  findingsCount?: number;
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

export const Navbar: React.FC<NavbarProps> = ({
  activeTab,
  setActiveTab,
  onOpenUpload,
  onTriggerRun,
  onOpenAiTest,
  isRunning,
  latestVerdict,
  findingsCount,
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
    <header className="z-20 shrink-0 select-none border-b border-slate-800 bg-slate-900">
      <div className="flex flex-wrap items-center gap-2 px-3 py-2 sm:px-4 min-[1920px]:flex-nowrap min-[1920px]:gap-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <div className="h-9 w-9 rounded-xl overflow-hidden shadow-lg shadow-indigo-500/20 shrink-0">
            <img src="/nedra.svg" alt="Недра" width={36} height={36} className="h-full w-full" />
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

        <div className="relative order-3 w-full min-w-0 min-[1920px]:order-none min-[1920px]:w-auto min-[1920px]:flex-1">
          <nav
            aria-label="Разделы"
            className="flex w-full min-w-0 items-center gap-1 overflow-x-auto rounded-xl border border-slate-800 bg-slate-950/60 p-1 sm:flex-wrap sm:overflow-visible"
          >

            {visibleTabs.map((item) => {
              const Icon = item.icon;
              return (
                <button
                  type="button"
                  key={item.id}
                  onClick={() => setActiveTab(item.id)}
                  aria-current={activeTab === item.id ? 'page' : undefined}
                  className={`relative flex h-9 shrink-0 cursor-pointer items-center gap-1.5 whitespace-nowrap rounded-lg px-2.5 text-xs font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 sm:px-3 ${
                    activeTab === item.id
                      ? 'bg-indigo-600 text-white shadow-sm'
                      : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-200'
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
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-y-1 right-1 w-12 rounded-r-xl bg-gradient-to-l from-slate-950 via-slate-950/85 to-transparent sm:hidden"
          />
        </div>

        <div className="order-2 ml-auto flex min-w-0 flex-wrap items-center justify-end gap-1.5 sm:gap-2 min-[1920px]:order-none min-[1920px]:flex-nowrap">
          {isAdmin && (
            <button
              type="button"
              onClick={onOpenAiTest}
              className="flex h-9 items-center justify-center gap-1.5 rounded-lg border border-fuchsia-800/80 bg-fuchsia-950/60 px-2.5 text-xs font-medium text-fuchsia-300 transition hover:bg-fuchsia-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-fuchsia-400 sm:px-3"
              title="Синтетический тест валидатора AI для жюри"
              aria-label="Синтетический тест AI"
            >
              <Sparkles size={14} />
              <span className="hidden lg:inline">Тест AI</span>
            </button>
          )}

          <button
            type="button"
            onClick={onOpenUpload}
            className="flex h-9 items-center justify-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800 px-2.5 text-xs font-medium text-slate-200 transition hover:bg-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
            title="ZIP или публичный репозиторий"
            aria-label="Добавить ZIP или репозиторий"
          >
            <Upload size={14} />
            <span className="hidden sm:inline">ZIP или репозиторий</span>
          </button>

          <button
            type="button"
            onClick={onTriggerRun}
            disabled={isRunning}
            title={isRunning ? 'Проверка выполняется' : 'Запустить проверку сценария'}
            className={`flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-lg px-3 text-xs font-bold shadow-lg transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-300 sm:px-4 ${
              isRunning
                ? 'cursor-not-allowed bg-slate-700 text-slate-400'
                : 'cursor-pointer bg-emerald-600 text-white shadow-emerald-600/20 hover:bg-emerald-500'
            }`}
          >
            <Play size={14} fill="currentColor" />
            <span className="whitespace-nowrap">
              <span className="sm:hidden">{isRunning ? 'Идёт…' : 'Запустить'}</span>
              <span className="hidden sm:inline">{isRunning ? 'Выполняется…' : 'Запустить проверку'}</span>
            </span>
          </button>

          <button
            type="button"
            onClick={onOpenAccount}
            className="flex h-9 items-center justify-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-1.5 text-[11px] text-slate-300 transition hover:bg-slate-800 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
            title={`Аккаунт: ${accountLabel}. История запусков и профиль.`}
            aria-label={`Аккаунт: ${accountLabel}. Открыть профиль и историю запусков.`}
          >
            <span className="flex h-6 w-6 items-center justify-center rounded-full bg-indigo-600/30 text-[10px] font-bold text-indigo-200">
              {mode === 'demo' ? <User size={11} /> : accountInitial}
            </span>
            <span className="hidden max-w-[110px] truncate 2xl:inline">{accountLabel}</span>
          </button>

          <button
            type="button"
            onClick={onLogout}
            className="flex h-9 items-center justify-center gap-1 rounded-lg border border-slate-700 bg-slate-900 px-2 text-[11px] text-slate-400 transition hover:bg-slate-800 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
            title="Завершить сессию"
            aria-label="Выйти из аккаунта"
          >
            <LogOut size={14} />
            <span className="hidden lg:inline">Выйти</span>
          </button>
        </div>
      </div>
    </header>
  );
};
