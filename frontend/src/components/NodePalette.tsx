import React, { useState } from 'react';
import {
  Play, Clock, FolderArchive, KeyRound, PackageSearch, FileCode2,
  GitMerge, ShieldAlert, GitFork, Sparkles, FileText, Send,
  ChevronLeft, ChevronRight
} from 'lucide-react';
import { NodeKind } from '../types';

const PALETTE_COLLAPSED_KEY = 'nedra.nodePaletteCollapsed';

const readPaletteCollapsed = (): boolean => {
  try {
    return window.localStorage.getItem(PALETTE_COLLAPSED_KEY) === '1';
  } catch {
    return false;
  }
};

interface NodePaletteProps {
  onAddNode: (kind: NodeKind) => void;
}

type IconComponent = React.ComponentType<{ size?: number; className?: string }>;

const PALETTE_ITEMS: Array<{
  category: string;
  items: Array<{ kind: NodeKind; title: string; desc: string; icon: IconComponent; color: string }>;
}> = [
  {
    category: '1. Триггеры',
    items: [
      { kind: 'manual_trigger', title: 'Manual Trigger', desc: 'Запуск по клику', icon: Play, color: 'text-purple-400 border-purple-800/60 bg-purple-950/30' },
      { kind: 'schedule_trigger', title: 'Schedule Trigger', desc: 'Запуск по расписанию', icon: Clock, color: 'text-purple-400 border-purple-800/60 bg-purple-950/30' },
    ],
  },
  {
    category: '2. Проект',
    items: [
      { kind: 'project_input', title: 'Project Input', desc: 'Привязка ZIP проекта', icon: FolderArchive, color: 'text-blue-400 border-blue-800/60 bg-blue-950/30' },
    ],
  },
  {
    category: '3. Собственные анализаторы',
    items: [
      { kind: 'secret_scan', title: 'Secret Scanner', desc: 'Поиск паролей и токенов', icon: KeyRound, color: 'text-amber-400 border-amber-800/60 bg-amber-950/30' },
      { kind: 'sca_scan', title: 'SCA Dependencies', desc: 'Проверка Gradle/KTS CVE', icon: PackageSearch, color: 'text-orange-400 border-orange-800/60 bg-orange-950/30' },
      { kind: 'kotlin_sast', title: 'Kotlin SAST', desc: 'Taint-анализ Tree-sitter', icon: FileCode2, color: 'text-rose-400 border-rose-800/60 bg-rose-950/30' },
    ],
  },
  {
    category: '4. Агрегация и Политика',
    items: [
      { kind: 'merge_findings', title: 'Merge Findings', desc: 'Слияние результатов', icon: GitMerge, color: 'text-teal-400 border-teal-800/60 bg-teal-950/30' },
      { kind: 'policy_gate', title: 'Policy Gate', desc: 'Оценка вердикта (Pass/Fail)', icon: ShieldAlert, color: 'text-emerald-400 border-emerald-800/60 bg-emerald-950/30' },
    ],
  },
  {
    category: '5. Логика графа',
    items: [
      { kind: 'condition', title: 'Condition (If/Else)', desc: 'Ветвление логики и regex', icon: GitFork, color: 'text-yellow-400 border-yellow-800/60 bg-yellow-950/30' },
      { kind: 'branch_merge', title: 'Branch Merge', desc: 'Схождение условных ветвей', icon: GitMerge, color: 'text-indigo-400 border-indigo-800/60 bg-indigo-950/30' },
    ],
  },
  {
    category: '6. AI и Отчёты',
    items: [
      { kind: 'ai_explain', title: 'AI Explain & Fix', desc: 'Объяснение и рекомендации', icon: Sparkles, color: 'text-fuchsia-400 border-fuchsia-800/60 bg-fuchsia-950/30' },
      { kind: 'build_report', title: 'Build Report', desc: 'Security Passport и JSON', icon: FileText, color: 'text-cyan-400 border-cyan-800/60 bg-cyan-950/30' },
      { kind: 'notify', title: 'Notify (Webhook)', desc: 'Отправка оповещения', icon: Send, color: 'text-sky-400 border-sky-800/60 bg-sky-950/30' },
    ],
  },
];

export const NodePalette: React.FC<NodePaletteProps> = ({ onAddNode }) => {
  const [collapsed, setCollapsed] = useState(readPaletteCollapsed);

  const toggleCollapsed = () => {
    setCollapsed((previous) => {
      const next = !previous;
      try {
        window.localStorage.setItem(PALETTE_COLLAPSED_KEY, next ? '1' : '0');
      } catch {
        // Storage unavailable: the choice still works for the current page session.
      }
      return next;
    });
  };

  const totalItems = PALETTE_ITEMS.reduce((total, category) => total + category.items.length, 0);

  return (
    <div
      className={`w-full ${
        collapsed ? 'md:w-12' : 'md:w-64'
      } max-h-44 md:max-h-none bg-slate-900/95 border-b md:border-b-0 md:border-r border-slate-800 flex flex-col md:h-full overflow-hidden select-none shrink-0 transition-[width] duration-200`}
    >
      <div className={`p-3.5 md:p-2.5 border-b border-slate-800 flex items-start justify-between gap-2 ${collapsed ? 'md:flex-col md:items-center' : ''}`}>
        <div className={collapsed ? 'md:hidden' : 'min-w-0'}>
          <h2 className="text-xs font-bold text-slate-300 uppercase tracking-wider">
            Палитра блоков
          </h2>
          <p className="text-[11px] text-slate-500 mt-0.5">
            Нажмите для добавления на канвас
          </p>
        </div>
        <button
          type="button"
          onClick={toggleCollapsed}
          aria-expanded={!collapsed}
          aria-controls="node-palette-list"
          title={collapsed ? 'Развернуть палитру блоков' : 'Свернуть палитру блоков'}
          className="shrink-0 rounded-md border border-slate-700 bg-slate-800 p-1 text-slate-400 hover:text-slate-200 hover:bg-slate-700"
        >
          {collapsed ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
        </button>
      </div>

      {!collapsed && (
        <div id="node-palette-list" className="flex-1 overflow-y-auto p-3 space-y-4">
          {PALETTE_ITEMS.map((cat, idx) => (
            <div key={idx} className="space-y-1.5">
              <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider px-1">
                {cat.category}
              </div>
              <div className="space-y-1">
                {cat.items.map((item) => {
                  const Icon = item.icon;
                  return (
                    <button
                      key={item.kind}
                      onClick={() => onAddNode(item.kind)}
                      className={`w-full text-left p-2 rounded-lg border transition-all duration-150 flex items-start gap-2.5 hover:scale-[1.01] hover:brightness-125 cursor-pointer ${item.color}`}
                    >
                      <Icon size={16} className="mt-0.5 shrink-0" />
                      <div className="min-w-0">
                        <div className="text-xs font-medium text-slate-200 truncate">
                          {item.title}
                        </div>
                        <div className="text-[10px] text-slate-400 truncate">
                          {item.desc}
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}

      {collapsed && (
        <div className="hidden md:flex flex-1 flex-col items-center gap-2 px-1 py-3 text-slate-600">
          <span
            className="text-[10px] font-semibold uppercase tracking-wider text-slate-500"
            style={{ writingMode: 'vertical-rl' }}
          >
            Палитра блоков
          </span>
          <span className="text-[10px] font-mono">{totalItems}</span>
        </div>
      )}
    </div>
  );
};
