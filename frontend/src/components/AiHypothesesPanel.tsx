import React, { useState } from 'react';
import { AlertTriangle, ChevronDown, ChevronRight, Sparkles } from 'lucide-react';
import { AiObservation, AiScanResult, Severity } from '../types';

interface AiHypothesesPanelProps {
  aiScan: AiScanResult;
}

const SEVERITY_LABEL: Record<Severity, string> = {
  critical: 'Критично',
  high: 'Высокий',
  medium: 'Средний',
  low: 'Низкий',
  info: 'Инфо',
};

const STATUS_LABEL: Record<string, string> = {
  succeeded: 'готово',
  partial: 'частично',
  degraded: 'деградация',
  rejected: 'отклонено',
  complete: 'полное',
};

const statusClass = (status: string): string => {
  if (status === 'succeeded' || status === 'complete') {
    return 'border-emerald-800 bg-emerald-950/60 text-emerald-300';
  }
  if (status === 'partial') return 'border-amber-800 bg-amber-950/60 text-amber-300';
  return 'border-rose-800 bg-rose-950/60 text-rose-300';
};

const ObservationRow: React.FC<{ observation: AiObservation }> = ({ observation }) => {
  const severity = observation.severity;
  const severityClass =
    severity === 'critical'
      ? 'border-rose-800 bg-rose-950/60 text-rose-300'
      : severity === 'high'
        ? 'border-orange-800 bg-orange-950/60 text-orange-300'
        : severity === 'medium'
          ? 'border-yellow-800 bg-yellow-950/60 text-yellow-300'
          : severity === 'low'
            ? 'border-sky-800 bg-sky-950/60 text-sky-300'
            : 'border-slate-700 bg-slate-900 text-slate-300';

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-950/50 p-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded border px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider ${severityClass}`}>
          {SEVERITY_LABEL[severity] || severity}
        </span>
        <span className="text-xs font-semibold text-slate-100">{observation.ruleClass}</span>
        <span className="rounded border border-fuchsia-900/70 bg-fuchsia-950/40 px-1.5 py-0.5 text-[10px] text-fuchsia-300">
          гипотеза · potential
        </span>
        <span className="font-mono text-[10px] text-indigo-400">
          {observation.primaryLocation.path}:{observation.primaryLocation.startLine}
        </span>
      </div>
      <p className="mt-1 text-[11px] leading-snug text-slate-300">{observation.rationale}</p>
      <p className="mt-0.5 text-[10px] text-slate-500">{observation.confidenceBasis}</p>
      <details className="mt-1.5">
        <summary className="cursor-pointer text-[10px] text-slate-400 hover:text-slate-200">
          Фрагмент и рекомендация
        </summary>
        <pre className="mt-1.5 overflow-x-auto rounded border border-slate-800 bg-slate-900 p-2 text-[10px] leading-relaxed text-slate-300">
          {observation.redactedEvidence}
        </pre>
        <p className="mt-1.5 text-[11px] leading-snug text-slate-400">{observation.remediation}</p>
        <p className="mt-1 font-mono text-[9px] text-slate-600">
          fileRef {observation.grounding.fileRef} · quote {observation.grounding.quoteSha256.slice(0, 12)}… · bytes {observation.grounding.startByte}–{observation.grounding.endByte}
        </p>
      </details>
    </div>
  );
};

export const AiHypothesesPanel: React.FC<AiHypothesesPanelProps> = ({ aiScan }) => {
  const [expanded, setExpanded] = useState(true);
  const observations = aiScan.observations || [];
  const coverage = aiScan.coverage;
  const provenance = aiScan.provenance;
  const incomplete = aiScan.status !== 'succeeded' || coverage.status !== 'complete';
  const limitations = [
    ...(coverage.limitsReached || []),
    coverage.truncatedFiles?.length ? `обрезано файлов: ${coverage.truncatedFiles.length}` : '',
    coverage.omittedCount ? `пропущено файлов: ${coverage.omittedCount}` : '',
    coverage.failedCount ? `ошибок файлов: ${coverage.failedCount}` : '',
    ...(coverage.providerErrors || []),
    ...(aiScan.validationErrors || []),
  ].filter(Boolean);

  return (
    <section className="border-b border-slate-800 bg-fuchsia-950/10">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="flex w-full items-start justify-between gap-3 px-3.5 py-2.5 text-left"
        aria-expanded={expanded}
      >
        <div className="flex min-w-0 items-start gap-2">
          <Sparkles size={15} className="mt-0.5 shrink-0 text-fuchsia-400" />
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-bold uppercase tracking-wider text-fuchsia-300">
                AI-гипотезы
              </span>
              <span className={`rounded border px-1.5 py-0.5 text-[10px] ${statusClass(aiScan.status)}`}>
                {STATUS_LABEL[aiScan.status] || aiScan.status}
              </span>
              <span className="rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 text-[10px] text-slate-400">
                {observations.length} гипотез
              </span>
              <span className="rounded border border-amber-900/60 bg-amber-950/30 px-1.5 py-0.5 text-[10px] text-amber-300">
                не findings · не влияет на вердикт
              </span>
            </div>
            <p className="mt-1 text-[11px] leading-snug text-slate-400">
              {observations.length
                ? 'Модель предложила дополнительные места для ручной проверки. Это не подтверждённые находки.'
                : 'Модель не добавила гипотез. Это не отменяет и не заменяет детерминированные проверки.'}
            </p>
            <p className="mt-0.5 text-[10px] text-slate-500">
              Покрытие AI: {STATUS_LABEL[coverage.status] || coverage.status} · файлов отправлено {coverage.filesSent}/{coverage.filesSelected} из {coverage.filesTotal}
              {aiScan.suppressedDuplicates > 0 ? ` · подавлено дублей: ${aiScan.suppressedDuplicates}` : ''}
              {provenance?.model ? ` · модель: ${provenance.model}` : ''}
            </p>
          </div>
        </div>
        {expanded ? (
          <ChevronDown size={16} className="mt-1 shrink-0 text-slate-500" />
        ) : (
          <ChevronRight size={16} className="mt-1 shrink-0 text-slate-500" />
        )}
      </button>

      {expanded && (
        <div className="space-y-2 px-3.5 pb-3">
          {incomplete && (
            <div className="flex items-start gap-1.5 rounded border border-amber-900/70 bg-amber-950/30 px-2 py-1.5 text-[10px] leading-snug text-amber-200">
              <AlertTriangle size={12} className="mt-0.5 shrink-0" />
              <span>
                AI-проверка неполная: это ограничение выбранного фрагмента, а не доказательство отсутствия проблем.
              </span>
            </div>
          )}

          {limitations.length > 0 && (
            <ul className="list-inside list-disc space-y-0.5 rounded border border-slate-800 bg-slate-950/40 px-2.5 py-1.5 text-[10px] text-slate-400">
              {limitations.slice(0, 6).map((item, index) => (
                <li key={`${item}-${index}`}>{item}</li>
              ))}
            </ul>
          )}

          {observations.length > 0 && (
            <div className="max-h-72 space-y-1.5 overflow-y-auto pr-0.5">
              {observations.map((observation) => (
                <ObservationRow key={observation.id} observation={observation} />
              ))}
            </div>
          )}

          {provenance && (
            <p className="font-mono text-[9px] text-slate-600">
              {provenance.provider || 'provider'} · {provenance.promptVersion || 'prompt'} · {provenance.validatorVersion || 'validator'}
              {provenance.latencyMs ? ` · ${Math.round(provenance.latencyMs)} мс` : ''}
            </p>
          )}
        </div>
      )}
    </section>
  );
};