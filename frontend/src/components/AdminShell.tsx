import React from 'react';
import {
  Activity, Bot, ChevronRight, FolderKanban, Gauge, KeyRound,
  LogOut, Map, Play, Settings2, ShieldCheck, Terminal, Wrench,
} from 'lucide-react';

export type AdminSection =
  | 'overview'
  | 'projects'
  | 'editor'
  | 'schedule'
  | 'live'
  | 'problems'
  | 'map'
  | 'report'
  | 'comparison'
  | 'analyzers'
  | 'ai'
  | 'access';

interface AdminShellProps {
  section: AdminSection;
  onSection: (section: AdminSection) => void;
  onLogout: () => void;
  children: React.ReactNode;
}

const GROUPS: Array<{
  label: string;
  items: Array<{ id: AdminSection; label: string; icon: React.ComponentType<{ size?: number }> }>;
}> = [
  {
    label: 'Обзор',
    items: [
      { id: 'overview', label: 'Обзор', icon: Gauge },
      { id: 'projects', label: 'Проекты и проверки', icon: FolderKanban },
    ],
  },
  {
    label: 'Сценарий',
    items: [
      { id: 'editor', label: 'Конструктор', icon: Wrench },
      { id: 'schedule', label: 'Расписания', icon: Settings2 },
    ],
  },
  {
    label: 'Результаты',
    items: [
      { id: 'live', label: 'Ход проверки', icon: Play },
      { id: 'problems', label: 'Проблемы', icon: Activity },
      { id: 'map', label: 'Карта доступа', icon: Map },
      { id: 'report', label: 'Отчёт', icon: Terminal },
      { id: 'comparison', label: 'Сравнение', icon: ShieldCheck },
    ],
  },
  {
    label: 'Система',
    items: [
      { id: 'analyzers', label: 'Анализаторы и покрытие', icon: Gauge },
      { id: 'ai', label: 'AI и память', icon: Bot },
      { id: 'access', label: 'Доступ и состояние', icon: KeyRound },
    ],
  },
];

export const AdminShell: React.FC<AdminShellProps> = ({
  section,
  onSection,
  onLogout,
  children,
}) => (
  <div className="flex h-screen w-screen flex-col overflow-hidden bg-slate-950 text-slate-100">
    <header className="flex min-h-14 flex-wrap items-center gap-3 border-b border-slate-800 bg-slate-900 px-4 py-2">
      <div className="flex items-center gap-2.5">
        <div className="rounded-xl bg-gradient-to-tr from-indigo-600 to-violet-500 p-2 text-white">
          <ShieldCheck size={18} />
        </div>
        <div>
          <div className="text-sm font-bold text-white">Проверка безопасности кода</div>
          <div className="text-[10px] uppercase tracking-wider text-slate-500">Админ-панель</div>
        </div>
      </div>
      <div className="ml-auto flex items-center gap-2">
        <span className="rounded-full border border-indigo-800 bg-indigo-950 px-2 py-1 text-[10px] font-semibold text-indigo-300">
          Администратор
        </span>
        <button
          onClick={onLogout}
          className="flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-[11px] text-slate-400 hover:bg-slate-800 hover:text-white"
        >
          <LogOut size={13} /> Выйти
        </button>
      </div>
    </header>

    <div className="flex min-h-0 flex-1 flex-col md:flex-row">
      <aside className="flex w-full shrink-0 flex-col border-b border-slate-800 bg-slate-900/80 md:w-60 md:border-b-0 md:border-r">
        <nav className="flex gap-3 overflow-x-auto p-3 md:flex-col md:overflow-y-auto md:gap-4">
          {GROUPS.map((group) => (
            <div key={group.label} className="min-w-max md:min-w-0">
              <div className="mb-1 px-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                {group.label}
              </div>
              <div className="flex gap-1 md:flex-col">
                {group.items.map((item) => {
                  const Icon = item.icon;
                  const active = section === item.id;
                  return (
                    <button
                      key={item.id}
                      onClick={() => onSection(item.id)}
                      className={`flex shrink-0 items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs ${
                        active
                          ? 'bg-indigo-600 text-white'
                          : 'text-slate-400 hover:bg-slate-800 hover:text-slate-100'
                      }`}
                    >
                      <Icon size={14} />
                      <span className="whitespace-nowrap">{item.label}</span>
                      {active && <ChevronRight size={12} className="ml-auto hidden md:block" />}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>
      </aside>
      <main className="flex min-h-0 flex-1 flex-col overflow-hidden">{children}</main>
    </div>
  </div>
);