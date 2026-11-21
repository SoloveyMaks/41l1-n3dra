import { memo, useEffect, useState } from 'react';
import { Handle, Position, NodeProps } from '@xyflow/react';
import {
  Play, Clock, FolderArchive, KeyRound, PackageSearch, FileCode2,
  GitMerge, ShieldAlert, GitFork, Sparkles, FileText, Send, CheckCircle2,
  AlertCircle, Loader2, MinusCircle
} from 'lucide-react';
import { NodeKind, NodeRunStatus } from '../types';
import './execution-motion.css';

interface CustomNodeData {
  kind: NodeKind;
  title: string;
  config: Record<string, any>;
  status?: NodeRunStatus;
  durationMs?: number;
  startedAtMs?: number;
  live?: boolean;
  // `active` is false when the last record is still running but the run is
  // already terminal; `stalled` carries that fact for an honest label.
  active?: boolean;
  stalled?: boolean;
  isSelected?: boolean;
}

const KIND_CONFIG: Record<NodeKind, { icon: any; color: string; bg: string; border: string }> = {
  manual_trigger: { icon: Play, color: 'text-purple-400', bg: 'bg-purple-950/40', border: 'border-purple-600' },
  schedule_trigger: { icon: Clock, color: 'text-purple-400', bg: 'bg-purple-950/40', border: 'border-purple-600' },
  project_input: { icon: FolderArchive, color: 'text-blue-400', bg: 'bg-blue-950/40', border: 'border-blue-600' },
  secret_scan: { icon: KeyRound, color: 'text-amber-400', bg: 'bg-amber-950/40', border: 'border-amber-600' },
  sca_scan: { icon: PackageSearch, color: 'text-orange-400', bg: 'bg-orange-950/40', border: 'border-orange-600' },
  kotlin_sast: { icon: FileCode2, color: 'text-rose-400', bg: 'bg-rose-950/40', border: 'border-rose-600' },
  merge_findings: { icon: GitMerge, color: 'text-teal-400', bg: 'bg-teal-950/40', border: 'border-teal-600' },
  policy_gate: { icon: ShieldAlert, color: 'text-emerald-400', bg: 'bg-emerald-950/40', border: 'border-emerald-600' },
  condition: { icon: GitFork, color: 'text-yellow-400', bg: 'bg-yellow-950/40', border: 'border-yellow-600' },
  branch_merge: { icon: GitMerge, color: 'text-indigo-400', bg: 'bg-indigo-950/40', border: 'border-indigo-600' },
  ai_explain: { icon: Sparkles, color: 'text-fuchsia-400', bg: 'bg-fuchsia-950/40', border: 'border-fuchsia-600' },
  ai_scan: { icon: Sparkles, color: 'text-fuchsia-300', bg: 'bg-fuchsia-950/30', border: 'border-fuchsia-700' },
  build_report: { icon: FileText, color: 'text-cyan-400', bg: 'bg-cyan-950/40', border: 'border-cyan-600' },
  notify: { icon: Send, color: 'text-sky-400', bg: 'bg-sky-950/40', border: 'border-sky-600' },
  custom_check: { icon: FileCode2, color: 'text-violet-300', bg: 'bg-violet-950/40', border: 'border-violet-600' },
};

// One interval per visibly running node, not per canvas: parallel branches keep
// independent clocks and do not re-render each other.
function useRunningElapsed(active: boolean, startedAtMs?: number): number | undefined {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active, startedAtMs]);
  if (!active || startedAtMs === undefined) return undefined;
  return Math.max(0, now - startedAtMs);
}

export const CustomNode = memo(({ data, selected }: NodeProps) => {
  const nodeData = data as unknown as CustomNodeData;
  const cfg = KIND_CONFIG[nodeData.kind] || {
    icon: Play, color: 'text-gray-400', bg: 'bg-gray-900', border: 'border-gray-700'
  };
  const Icon = cfg.icon;

  const status = nodeData.status;
  const stalled = Boolean(nodeData.stalled);
  // Live motion needs both a truthful active record and a live subscription.
  const isCurrent = Boolean(nodeData.active) && Boolean(nodeData.live) && status === 'running' && !stalled;
  const elapsedMs = useRunningElapsed(isCurrent, nodeData.startedAtMs);
  const shownMs = isCurrent ? elapsedMs : nodeData.durationMs;

  return (
    <div
      className={`exec-node min-w-[210px] rounded-xl shadow-xl backdrop-blur-md transition-all duration-200 border-2 ${
        selected ? 'ring-2 ring-indigo-400 scale-[1.02]' : ''
      } ${cfg.border} ${cfg.bg} bg-slate-900/90 text-slate-100 overflow-hidden`}
      data-status={status || 'pending'}
      data-current={isCurrent ? 'true' : 'false'}
      data-live={nodeData.live ? 'true' : 'false'}
      data-stalled={stalled ? 'true' : 'false'}
    >
      {/* Input Handles */}
      {renderInputHandles(nodeData.kind)}

      {/* Header */}
      <div className="px-3.5 py-2.5 flex items-center justify-between border-b border-white/10">
        <div className="flex items-center gap-2">
          <div className={`p-1.5 rounded-lg bg-black/40 ${cfg.color}`}>
            <Icon size={16} />
          </div>
          <div>
            <div className="font-semibold text-xs tracking-wide">
              {nodeData.title || nodeData.kind}
            </div>
            <div className="text-[10px] text-slate-400 capitalize">
              {nodeData.kind.replace('_', ' ')}
            </div>
          </div>
        </div>

        {/* Live Status Badge */}
        {status && (
          <div className="flex items-center gap-1 text-[10px] font-medium">
            {status === 'running' && !stalled && (
              <span className="flex items-center gap-1 text-blue-400">
                <Loader2 size={13} className="animate-spin" />
                <span className="exec-node__live-dot" />
              </span>
            )}
            {status === 'running' && stalled && (
              <span
                className="flex items-center text-amber-400/90"
                title="Запуск завершён, итоговый статус блока не пришёл"
              >
                <Clock size={13} />
              </span>
            )}
            {status === 'succeeded' && (
              <span className="text-emerald-400 flex items-center">
                <CheckCircle2 size={13} />
              </span>
            )}
            {status === 'failed' && (
              <span className="text-rose-400 flex items-center">
                <AlertCircle size={13} />
              </span>
            )}
            {status === 'skipped' && (
              <span className="text-slate-500 flex items-center">
                <MinusCircle size={13} />
              </span>
            )}
            {shownMs !== undefined && shownMs > 0 && (
              <span className="exec-node__clock text-[9px] text-slate-400">
                {shownMs < 1000
                  ? `${Math.round(shownMs)}ms`
                  : `${(shownMs / 1000).toFixed(1)}s`}
              </span>
            )}
          </div>
        )}
      </div>

      {/* Body / Config summary */}
      <div className="px-3.5 py-2 text-[11px] text-slate-300 space-y-1 bg-black/20">
        {renderConfigSummary(nodeData.kind, nodeData.config)}
      </div>

      {/* Output Handles */}
      {renderOutputHandles(nodeData.kind)}
    </div>
  );
});

function renderInputHandles(kind: NodeKind) {
  switch (kind) {
    case 'manual_trigger':
    case 'schedule_trigger':
      return null;
    case 'project_input':
      return <Handle type="target" position={Position.Left} id="trigger" className="!bg-purple-500 !w-3 !h-3" />;
    case 'secret_scan':
    case 'sca_scan':
    case 'kotlin_sast':
    case 'custom_check':
      return <Handle type="target" position={Position.Left} id="artifact" className="!bg-blue-500 !w-3 !h-3" />;
    case 'merge_findings':
      return (
        <>
          <Handle type="target" position={Position.Left} id="scan_1" style={{ top: '25%' }} className="!bg-amber-500 !w-2.5 !h-2.5" />
          <Handle type="target" position={Position.Left} id="scan_2" style={{ top: '50%' }} className="!bg-orange-500 !w-2.5 !h-2.5" />
          <Handle type="target" position={Position.Left} id="scan_3" style={{ top: '75%' }} className="!bg-rose-500 !w-2.5 !h-2.5" />
        </>
      );
    case 'policy_gate':
      return <Handle type="target" position={Position.Left} id="findings_bundle" className="!bg-teal-500 !w-3 !h-3" />;
    case 'condition':
      return <Handle type="target" position={Position.Left} id="in" className="!bg-emerald-500 !w-3 !h-3" />;
    case 'branch_merge':
      return (
        <>
          <Handle type="target" position={Position.Left} id="branch_a" style={{ top: '35%' }} className="!bg-yellow-500 !w-2.5 !h-2.5" />
          <Handle type="target" position={Position.Left} id="branch_b" style={{ top: '65%' }} className="!bg-yellow-500 !w-2.5 !h-2.5" />
        </>
      );
    case 'ai_explain':
      return <Handle type="target" position={Position.Left} id="findings_bundle" className="!bg-teal-500 !w-3 !h-3" />;
    case 'ai_scan':
      return (
        <>
          <Handle type="target" position={Position.Left} id="artifact" style={{ top: '35%' }} className="!bg-blue-500 !w-3 !h-3" />
          <Handle type="target" position={Position.Left} id="findings_bundle" style={{ top: '70%' }} className="!bg-teal-500 !w-3 !h-3" />
        </>
      );
    case 'build_report':
      return (
        <>
          <Handle type="target" position={Position.Left} id="findings_bundle" style={{ top: '15%' }} className="!bg-teal-500 !w-2.5 !h-2.5" />
          <Handle type="target" position={Position.Left} id="policy_result" style={{ top: '50%' }} className="!bg-emerald-500 !w-2.5 !h-2.5" />
          <Handle type="target" position={Position.Left} id="explain_result" style={{ top: '85%' }} className="!bg-fuchsia-500 !w-2.5 !h-2.5" />
        </>
      );
    case 'notify':
      return <Handle type="target" position={Position.Left} id="report" className="!bg-cyan-500 !w-3 !h-3" />;
    default:
      return <Handle type="target" position={Position.Left} id="in" className="!bg-gray-500 !w-3 !h-3" />;
  }
}

function renderOutputHandles(kind: NodeKind) {
  switch (kind) {
    case 'manual_trigger':
    case 'schedule_trigger':
      return <Handle type="source" position={Position.Right} id="out" className="!bg-purple-500 !w-3 !h-3" />;
    case 'project_input':
      return <Handle type="source" position={Position.Right} id="artifact" className="!bg-blue-500 !w-3 !h-3" />;
    case 'secret_scan':
    case 'sca_scan':
    case 'kotlin_sast':
      return <Handle type="source" position={Position.Right} id="scan_result" className="!bg-amber-500 !w-3 !h-3" />;
    case 'merge_findings':
      return <Handle type="source" position={Position.Right} id="findings_bundle" className="!bg-teal-500 !w-3 !h-3" />;
    case 'policy_gate':
      return <Handle type="source" position={Position.Right} id="policy_result" className="!bg-emerald-500 !w-3 !h-3" />;
    case 'condition':
      return (
        <>
          <Handle type="source" position={Position.Right} id="true_branch" style={{ top: '30%' }} className="!bg-emerald-400 !w-3 !h-3" />
          <Handle type="source" position={Position.Right} id="false_branch" style={{ top: '70%' }} className="!bg-rose-400 !w-3 !h-3" />
        </>
      );
    case 'branch_merge':
      return <Handle type="source" position={Position.Right} id="out" className="!bg-indigo-400 !w-3 !h-3" />;
    case 'ai_explain':
      return <Handle type="source" position={Position.Right} id="explain_result" className="!bg-fuchsia-400 !w-3 !h-3" />;
    case 'ai_scan':
      return <Handle type="source" position={Position.Right} id="ai_scan_result" className="!bg-fuchsia-300 !w-3 !h-3" />;
    case 'build_report':
      return <Handle type="source" position={Position.Right} id="report" className="!bg-cyan-400 !w-3 !h-3" />;
    case 'notify':
      return <Handle type="source" position={Position.Right} id="delivery_result" className="!bg-sky-400 !w-3 !h-3" />;
    case 'custom_check':
      return <Handle type="source" position={Position.Right} id="custom_findings" className="!bg-violet-400 !w-3 !h-3" />;
    default:
      return <Handle type="source" position={Position.Right} id="out" className="!bg-gray-500 !w-3 !h-3" />;
  }
}

function renderConfigSummary(kind: NodeKind, config: Record<string, any>) {
  switch (kind) {
    case 'schedule_trigger':
      return <div className="text-purple-300 font-mono">Расписание: {config.cron || '*/30 * * * *'}</div>;
    case 'project_input':
      return <div className="truncate text-blue-300">Артефакт: {config.artifactId || 'Авто-выбор'}</div>;
    case 'secret_scan':
      return <div>Сканирование токенов, ключей, PEM</div>;
    case 'sca_scan':
      return <div>Анализ Gradle Kotlin DSL версий</div>;
    case 'kotlin_sast':
      return <div>Taint-анализ (SQL, Path, Command)</div>;
    case 'merge_findings':
      return <div>Слияние и расчет покрытия</div>;
    case 'policy_gate':
      return <div className="text-emerald-300">Порог: Critical 0, High 0</div>;
    case 'condition':
      return (
        <div className="space-y-0.5">
          <div className="text-yellow-300 font-mono">
            {config.field || 'verdict'} {config.operator || '=='} {config.targetValue || 'fail'}
          </div>
          <div className="flex justify-between text-[9px] text-slate-400 font-semibold">
            <span className="text-emerald-400">Верх: True</span>
            <span className="text-rose-400">Низ: False</span>
          </div>
        </div>
      );
    case 'ai_explain':
      return <div className="text-fuchsia-300">Модель: deepseek-v4.1-flash-1017</div>;
    case 'ai_scan':
      return (
        <div className="text-fuchsia-300">
          Гипотезы · {config.profile || 'balanced'} · файлов ≤ {config.maxFiles ?? 6}
        </div>
      );
    case 'build_report':
      return <div>Security Passport + JSON</div>;
    case 'notify':
      return <div className="truncate text-sky-300">Канал: HTTP Webhook</div>;
    case 'custom_check':
      return (
        <div className="truncate text-violet-300">
          {config.definitionId ? `Проверка: ${config.definitionId.slice(0, 8)}…` : 'Выберите проверку'}
        </div>
      );
    default:
      return <div>Готов к работе</div>;
  }
}
