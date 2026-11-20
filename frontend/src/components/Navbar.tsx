import React from 'react';
import {
  ShieldCheck, Play, Upload, Clock, FileText, Bug,
  Sparkles, CheckCircle2, AlertTriangle, Layers, Network
} from 'lucide-react';
import { AppTab, SecurityVerdict } from '../types';

interface NavbarProps {
  activeTab: AppTab;
  setActiveTab: (tab: AppTab) => void;
  onOpenUpload: () => void;
  onTriggerRun: () => void;
  onOpenAiTest: () => void;
  isRunning: boolean;
  latestVerdict?: SecurityVerdict;
  findingsCount?: number;
}

export const Navbar: React.FC<NavbarProps> = ({
  activeTab,
  setActiveTab,
  onOpenUpload,
  onTriggerRun,
  onOpenAiTest,
  isRunning,
  latestVerdict,
  findingsCount,
}) => {
  return (
    <header className="h-14 bg-slate-900 border-b border-slate-800 px-4 flex items-center justify-between z-20 select-none">
      {/* Brand */}
      <div className="flex items-center gap-3">
        <div className="p-2 rounded-xl bg-gradient-to-tr from-indigo-600 to-violet-500 text-white shadow-lg shadow-indigo-500/20">
          <ShieldCheck size={20} />
        </div>
        <div>
          <div className="flex items-center gap-2">
            <span className="font-bold text-sm tracking-tight text-white">
              AI Security Workflow
            </span>
            <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-indigo-950 text-indigo-300 border border-indigo-800">
              Cyber Garden 2026
            </span>
          </div>
          <p className="text-[10px] text-slate-400">
            Банк «Центр-инвест» • Конструктор проверок безопасности
          </p>
        </div>
      </div>

      {/* Tabs */}
      <nav className="flex items-center gap-1 bg-slate-950/60 p-1 rounded-xl border border-slate-800">
        <button
          onClick={() => setActiveTab('editor')}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition cursor-pointer ${
            activeTab === 'editor'
              ? 'bg-indigo-600 text-white shadow-sm'
              : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
          }`}
        >
          <Layers size={14} />
          <span>Редактор</span>
        </button>

        <button
          onClick={() => setActiveTab('live')}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition cursor-pointer relative ${
            activeTab === 'live'
              ? 'bg-indigo-600 text-white shadow-sm'
              : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
          }`}
        >
          <Play size={14} />
          <span>Живое выполнение</span>
          {isRunning && (
            <span className="w-2 h-2 rounded-full bg-blue-400 animate-ping absolute -top-0.5 -right-0.5" />
          )}
        </button>

        <button
          onClick={() => setActiveTab('findings')}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition cursor-pointer ${
            activeTab === 'findings'
              ? 'bg-indigo-600 text-white shadow-sm'
              : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
          }`}
        >
          <Bug size={14} />
          <span>Находки и Evidence</span>
          {findingsCount !== undefined && findingsCount > 0 && (
            <span className="ml-1 px-1.5 py-0.2 rounded-full text-[10px] bg-rose-950 text-rose-300 border border-rose-800">
              {findingsCount}
            </span>
          )}
        </button>

        <button
          onClick={() => setActiveTab('map')}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition cursor-pointer ${
            activeTab === 'map'
              ? 'bg-indigo-600 text-white shadow-sm'
              : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
          }`}
        >
          <Network size={14} />
          <span>Карта безопасности</span>
        </button>

        <button
          onClick={() => setActiveTab('report')}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition cursor-pointer ${
            activeTab === 'report'
              ? 'bg-indigo-600 text-white shadow-sm'
              : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
          }`}
        >
          <FileText size={14} />
          <span>Отчёт</span>
          {latestVerdict && (
            <span
              className={`ml-1 px-1.5 py-0.2 rounded text-[9px] font-bold uppercase ${
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

        <button
          onClick={() => setActiveTab('schedule')}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition cursor-pointer ${
            activeTab === 'schedule'
              ? 'bg-indigo-600 text-white shadow-sm'
              : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
          }`}
        >
          <Clock size={14} />
          <span>Расписание</span>
        </button>
      </nav>

      {/* Actions */}
      <div className="flex items-center gap-2">
        <button
          onClick={onOpenAiTest}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-fuchsia-950/60 hover:bg-fuchsia-900 border border-fuchsia-800/80 text-fuchsia-300 text-xs font-medium transition cursor-pointer"
          title="Синтетический тест валидатора AI для жюри"
        >
          <Sparkles size={13} />
          <span>Тест AI Trust Layer</span>
        </button>

        <button
          onClick={onOpenUpload}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 text-xs font-medium transition cursor-pointer"
        >
          <Upload size={13} />
          <span>Загрузить ZIP</span>
        </button>

        <button
          onClick={onTriggerRun}
          disabled={isRunning}
          className={`flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-xs font-bold transition shadow-lg cursor-pointer ${
            isRunning
              ? 'bg-slate-700 text-slate-400 cursor-not-allowed'
              : 'bg-emerald-600 hover:bg-emerald-500 text-white shadow-emerald-600/20'
          }`}
        >
          <Play size={13} fill="currentColor" />
          <span>{isRunning ? 'Выполняется...' : 'Запустить workflow'}</span>
        </button>
      </div>
    </header>
  );
};
