import React from 'react';
import {
  ShieldAlert, KeyRound, PackageSearch, FileCode2,
  ExternalLink, Sparkles, AlertTriangle, ArrowRight, CheckCircle2
} from 'lucide-react';
import { Finding } from '../types';

interface EvidenceCardProps {
  finding: Finding | null;
  onClose: () => void;
}

const SEVERITY_LABEL: Record<string, string> = {
  critical: 'Критично', high: 'Высокий', medium: 'Средний', low: 'Низкий', info: 'Инфо',
};
const CONFIDENCE_LABEL: Record<string, string> = {
  confirmed: 'подтверждено', potential: 'возможно', inconclusive: 'не подтверждено',
};

export const EvidenceCard: React.FC<EvidenceCardProps> = ({ finding, onClose }) => {
  if (!finding) return null;

  const isCritical = finding.severity === 'critical';
  const isHigh = finding.severity === 'high';

  return (
    <div className="w-full lg:w-[450px] max-h-[55vh] lg:max-h-none bg-slate-900 border-t lg:border-t-0 lg:border-l border-slate-800 flex flex-col h-full overflow-hidden shadow-2xl shrink-0">
      {/* Header */}
      <div className="p-4 border-b border-slate-800 flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span
              className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider ${
                isCritical
                  ? 'bg-rose-950 text-rose-300 border border-rose-800'
                  : isHigh
                  ? 'bg-orange-950 text-orange-300 border border-orange-800'
                  : 'bg-yellow-950 text-yellow-300 border border-yellow-800'
              }`}
            >
              {SEVERITY_LABEL[finding.severity] || finding.severity}
            </span>
            <span className="font-mono text-[10px] text-slate-400">
              {finding.analyzer.toUpperCase()}
            </span>
          </div>
          <h2 className="text-sm font-bold text-slate-100">
            {finding.ruleId}
          </h2>
        </div>
        <button
          onClick={onClose}
          className="text-slate-400 hover:text-slate-200 text-sm p-1 rounded-md hover:bg-slate-800 cursor-pointer"
        >
          ✕
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4 text-xs text-slate-300">
        {/* Location */}
        <div>
          <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">
            Где это
          </div>
          <div className="p-2.5 rounded-lg bg-slate-800/80 border border-slate-700 font-mono text-[11px] text-indigo-300 flex items-center justify-between">
            <span className="truncate">{finding.primaryLocation.path}</span>
            <span className="text-slate-400 text-[10px]">
              строка {finding.primaryLocation.startLine}
            </span>
          </div>
        </div>

        {/* Severity Basis */}
        <div>
          <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">
            Почему это проблема
          </div>
          <p className="text-slate-300 leading-relaxed bg-slate-800/40 p-2.5 rounded-lg border border-slate-800">
            {finding.severityBasis}
          </p>
        </div>

        {/* Confidence Basis */}
        <div>
          <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">
            Насколько подтверждено: {CONFIDENCE_LABEL[finding.confidence] || finding.confidence}
          </div>
          <p className="text-slate-400 leading-relaxed text-[11px]">
            {finding.confidenceBasis}
          </p>
        </div>

        {/* Evidence Snippet */}
        {finding.redactedEvidence && (
          <div>
            <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">
              Фрагмент кода
            </div>
            <pre className="p-2.5 rounded-lg bg-slate-950 border border-slate-800 font-mono text-[11px] text-amber-300 overflow-x-auto whitespace-pre-wrap">
              {finding.redactedEvidence}
            </pre>
          </div>
        )}

        {/* Taint Trace Graph */}
        {finding.trace && finding.trace.length > 0 && (
          <div>
            <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-2 flex items-center gap-1.5">
              <span>Цепочка вызовов</span>
            </div>
            <div className="space-y-2 border-l-2 border-indigo-500/40 pl-3 ml-1">
              {finding.trace.map((step, idx) => (
                <div key={idx} className="relative">
                  <div className="text-[11px] font-semibold text-indigo-300">
                    Шаг {step.step}:
                  </div>
                  <div className="text-[11px] text-slate-300 leading-snug">
                    {step.description}
                  </div>
                  {step.location && (
                    <div className="font-mono text-[10px] text-slate-500 mt-0.5">
                      {step.location.path}:{step.location.startLine}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Advisory / CVE */}
        {finding.advisoryIds && finding.advisoryIds.length > 0 && (
          <div>
            <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">
              Идентификаторы уязвимостей (Advisories)
            </div>
            <div className="flex flex-wrap gap-1.5">
              {finding.advisoryIds.map((cve) => (
                <span
                  key={cve}
                  className="px-2 py-0.5 rounded text-[11px] font-mono bg-rose-950 text-rose-300 border border-rose-800"
                >
                  {cve}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* AI Enrichment */}
        {finding.ai_enrichment && (
          <div className="p-3 rounded-xl bg-fuchsia-950/30 border border-fuchsia-800/60 space-y-2">
            <div className="flex items-center gap-1.5 text-xs font-bold text-fuchsia-300">
              <Sparkles size={14} />
              <span>Рекомендация AI-ассистента</span>
            </div>
            <div className="text-[11px] text-slate-300 leading-relaxed">
              {finding.ai_enrichment.explanation}
            </div>
            <div className="mt-2 text-[11px] font-mono p-2 rounded bg-slate-900 border border-fuchsia-800/40 text-fuchsia-200">
              {finding.ai_enrichment.remediation}
            </div>
            {finding.ai_enrichment.suggestedFixedVersion && (
              <div className="text-[10px] text-emerald-400 font-semibold">
                Исправленная версия: {finding.ai_enrichment.suggestedFixedVersion}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
