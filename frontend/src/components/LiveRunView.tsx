import React, { useState } from 'react';
import {
  Play, CheckCircle2, AlertCircle, Loader2, MinusCircle, Clock,
  FileCode2, ShieldAlert, KeyRound, PackageSearch, Sparkles, Send,
  Terminal, Eye, ChevronRight
} from 'lucide-react';
import { NodeRunRecord, RunSummary } from '../types';

interface LiveRunViewProps {
  currentRun: RunSummary | null;
  nodeRuns: NodeRunRecord[];
  events: Array<{ seq: number; timestamp: string; type: string; nodeId?: string; payload: any }>;
  onSelectNode: (nodeId: string) => void;
}

export const LiveRunView: React.FC<LiveRunViewProps> = ({
  currentRun,
  nodeRuns,
  events,
}) => {
  const [selectedNodeRun, setSelectedNodeRun] = useState<NodeRunRecord | null>(null);

  if (!currentRun) {
    return (
      <div className="flex-1 h-full flex flex-col items-center justify-center text-slate-500 space-y-2">
        <Play size={36} className="text-slate-600 animate-pulse" />
        <p className="text-sm">Нет активного или выбранного запуска</p>
        <p className="text-xs text-slate-600">Запустите сценарий из редактора или выберите запуск из истории</p>
      </div>
    );
  }

  const isRunning = currentRun.execution_status === 'running' || currentRun.execution_status === 'queued';
  const isFailed = currentRun.execution_status === 'failed';
  const isSucceeded = currentRun.execution_status === 'succeeded';

  return (
    <div className="flex-1 h-full flex flex-col overflow-hidden bg-slate-950">
      {/* Run Summary Header */}
      <div className="p-4 bg-slate-900 border-b border-slate-800 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div
            className={`p-2 rounded-xl text-white shadow-lg ${
              isRunning
                ? 'bg-blue-600 animate-pulse shadow-blue-500/20'
                : isFailed
                ? 'bg-rose-600 shadow-rose-500/20'
                : 'bg-emerald-600 shadow-emerald-500/20'
            }`}
          >
            {isRunning ? <Loader2 size={18} className="animate-spin" /> : isFailed ? <AlertCircle size={18} /> : <CheckCircle2 size={18} />}
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="font-mono text-sm font-bold text-slate-200">
                Запуск: {currentRun.id}
              </span>
              <span
                className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider ${
                  currentRun.execution_status === 'succeeded'
                    ? 'bg-emerald-950 text-emerald-300 border border-emerald-800'
                    : currentRun.execution_status === 'failed'
                    ? 'bg-rose-950 text-rose-300 border border-rose-800'
                    : 'bg-blue-950 text-blue-300 border border-blue-800 animate-pulse'
                }`}
              >
                {currentRun.execution_status}
              </span>
              <span
                className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider ${
                  currentRun.security_verdict === 'fail'
                    ? 'bg-rose-950 text-rose-300 border border-rose-800'
                    : currentRun.security_verdict === 'pass'
                    ? 'bg-emerald-950 text-emerald-300 border border-emerald-800'
                    : 'bg-yellow-950 text-yellow-300 border border-yellow-800'
                }`}
              >
                Вердикт: {currentRun.security_verdict}
              </span>
            </div>
            <div className="flex items-center gap-3 text-[11px] text-slate-400 mt-0.5">
              <span>Триггер: <strong className="text-slate-300">{currentRun.trigger_type}</strong></span>
              <span>•</span>
              <span>Находок: <strong className="text-slate-300">{currentRun.total_findings}</strong> (Крит: {currentRun.critical_count}, Выс: {currentRun.high_count})</span>
              <span>•</span>
              <span>Событий SSE: <strong className="text-slate-300">{events.length}</strong></span>
            </div>
          </div>
        </div>
      </div>

      {/* Main Execution View */}
      <div className="flex-1 grid grid-cols-12 gap-0 overflow-hidden">
        {/* Left: Node Execution Pipeline */}
        <div className="col-span-4 border-r border-slate-800 flex flex-col h-full overflow-hidden bg-slate-900/40">
          <div className="p-3 border-b border-slate-800 text-xs font-bold text-slate-400 uppercase tracking-wider">
            Исполнение блоков графа ({nodeRuns.length})
          </div>
          <div className="flex-1 overflow-y-auto p-3 space-y-2">
            {nodeRuns.map((nr) => {
              const isSelected = selectedNodeRun?.id === nr.id;
              return (
                <div
                  key={nr.id}
                  onClick={() => setSelectedNodeRun(nr)}
                  className={`p-2.5 rounded-xl border transition-all cursor-pointer ${
                    isSelected
                      ? 'bg-slate-800 border-indigo-500 shadow-md ring-1 ring-indigo-400/50'
                      : 'bg-slate-900/80 hover:bg-slate-800/60 border-slate-800'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      {nr.status === 'running' && <Loader2 size={14} className="text-blue-400 animate-spin" />}
                      {nr.status === 'succeeded' && <CheckCircle2 size={14} className="text-emerald-400" />}
                      {nr.status === 'failed' && <AlertCircle size={14} className="text-rose-400" />}
                      {nr.status === 'skipped' && <MinusCircle size={14} className="text-slate-500" />}
                      <span className="text-xs font-semibold text-slate-200">
                        {nr.node_id}
                      </span>
                    </div>
                    <div className="text-[10px] font-mono text-slate-400">
                      {nr.duration_ms > 0 ? `${Math.round(nr.duration_ms)}ms` : ''}
                    </div>
                  </div>

                  <div className="mt-1 flex items-center justify-between text-[11px] text-slate-400">
                    <span className="capitalize">{nr.kind.replace('_', ' ')}</span>
                    <span className="text-[10px] text-indigo-400 flex items-center gap-0.5">
                      Инспектор данных <ChevronRight size={12} />
                    </span>
                  </div>

                  {nr.error_message && (
                    <div className="mt-1.5 p-1.5 rounded bg-rose-950/80 border border-rose-800 text-[10px] text-rose-200">
                      {nr.error_message}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Center: Data Inspector (Masked JSON payloads) */}
        <div className="col-span-4 border-r border-slate-800 flex flex-col h-full overflow-hidden bg-slate-950">
          <div className="p-3 border-b border-slate-800 flex items-center justify-between">
            <span className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
              <Eye size={14} className="text-indigo-400" />
              <span>Инспектор данных блока</span>
            </span>
            {selectedNodeRun && (
              <span className="font-mono text-[10px] text-slate-400">
                {selectedNodeRun.node_id}
              </span>
            )}
          </div>

          <div className="flex-1 overflow-y-auto p-3 text-xs font-mono space-y-3">
            {selectedNodeRun ? (
              <>
                <div>
                  <div className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1">
                    Входные данные (Входы):
                  </div>
                  <pre className="p-2.5 rounded-lg bg-slate-900 border border-slate-800 text-slate-300 text-[11px] overflow-x-auto leading-relaxed">
                    {JSON.stringify(selectedNodeRun.input_data_masked, null, 2)}
                  </pre>
                </div>

                <div>
                  <div className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1">
                    Выходные данные (Выходы / Передаваемые пакеты):
                  </div>
                  <pre className="p-2.5 rounded-lg bg-slate-900 border border-slate-800 text-indigo-200 text-[11px] overflow-x-auto leading-relaxed">
                    {JSON.stringify(selectedNodeRun.output_data_masked, null, 2)}
                  </pre>
                </div>
              </>
            ) : (
              <div className="h-full flex flex-col items-center justify-center text-slate-600 text-center p-4">
                <Eye size={24} className="mb-2" />
                <p>Выберите блок слева, чтобы просмотреть переданные данные и замаскированные секреты</p>
              </div>
            )}
          </div>
        </div>

        {/* Right: Live SSE Event Stream Timeline */}
        <div className="col-span-4 flex flex-col h-full overflow-hidden bg-slate-900/60">
          <div className="p-3 border-b border-slate-800 flex items-center justify-between">
            <span className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
              <Terminal size={14} className="text-emerald-400" />
              <span>Поток событий SSE ({events.length})</span>
            </span>
            <span className="text-[10px] text-emerald-400 font-mono">
              Live Stream
            </span>
          </div>

          <div className="flex-1 overflow-y-auto p-3 space-y-2 font-mono text-[11px]">
            {events.map((ev) => (
              <div key={ev.seq} className="p-2 rounded bg-slate-950/80 border border-slate-800/80 space-y-0.5">
                <div className="flex items-center justify-between text-slate-500 text-[10px]">
                  <span>#{ev.seq} • {ev.type}</span>
                  <span>{new Date(ev.timestamp).toLocaleTimeString()}</span>
                </div>
                {ev.nodeId && (
                  <div className="text-indigo-400 font-semibold text-[10px]">
                    Нода: {ev.nodeId}
                  </div>
                )}
                <div className="text-slate-300 text-[10px] truncate">
                  {JSON.stringify(ev.payload)}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};
