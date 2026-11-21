import React, { useMemo, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  ClipboardCheck,
  GitBranch,
  Loader2,
  ShieldQuestion,
  ShieldX,
} from 'lucide-react';
import { API_BASE } from '../../api/client';
import type {
  PinnedReference,
  PinnedTarget,
  ReferenceComparisonReport,
  ReferenceReportRow,
  ReferenceVerdict,
} from './types';

export interface ReferenceChecksPanelProps {
  references: PinnedReference[];
  targets: PinnedTarget[];
  /** Defaults to `${API_BASE}/reference-checks/compare`. */
  compareUrl?: string;
  onResult?: (report: ReferenceComparisonReport) => void;
  className?: string;
}

const VERDICT_LABEL: Record<ReferenceVerdict, string> = {
  pass: 'Совпало с эталоном',
  fail: 'Расхождения с эталоном',
  inconclusive: 'Сравнить нельзя',
};

const VERDICT_HINT: Record<ReferenceVerdict, string> = {
  pass: 'Все ожидаемые находки на месте, безопасные контроли молчат.',
  fail: 'Есть подтверждённое расхождение: пропавшая находка, сработавший контроль или лишняя находка.',
  inconclusive: 'Несовместимая версия анализаторов или потеря покрытия. Это не pass.',
};

const VERDICT_STYLE: Record<ReferenceVerdict, string> = {
  pass: 'border-emerald-800 bg-emerald-950/60 text-emerald-300',
  fail: 'border-rose-800 bg-rose-950/60 text-rose-300',
  inconclusive: 'border-amber-800 bg-amber-950/60 text-amber-300',
};

const ROW_LABEL: Record<ReferenceReportRow['status'], string> = {
  matched: 'совпало',
  missing: 'пропало',
  inconclusive: 'не проверено',
  triggered: 'контроль сработал',
};

const ROW_STYLE: Record<ReferenceReportRow['status'], string> = {
  matched: 'text-emerald-300',
  missing: 'text-rose-300',
  inconclusive: 'text-amber-300',
  triggered: 'text-rose-300',
};

const ERROR_HINT: Record<string, string> = {
  INVALID_MANIFEST: 'Манифест эталона неполный или противоречивый.',
  UNSUPPORTED_MANIFEST: 'Версия формата эталона не поддерживается этим сравнением.',
  INVALID_BUNDLE: 'Bundle повреждён или не соответствует контракту AnalysisBundle 0.1.',
  INCOMPATIBLE_BUNDLE: 'Bundle несовместим: не хватает подписи, покрытия или идентичности.',
  INVALID_PROVENANCE: 'Не закреплён SHA эталона/цели или версия runner.',
};

const shortSha = (sha: string): string => (sha.length > 12 ? `${sha.slice(0, 12)}…` : sha);

const describeError = (error: unknown): string => {
  if (error instanceof Error) return error.message;
  return String(error);
};

async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    const detail = body?.detail;
    if (detail && typeof detail === 'object') {
      const code = typeof detail.code === 'string' ? detail.code : '';
      const hint = ERROR_HINT[code];
      return [hint, typeof detail.message === 'string' ? detail.message : ''].filter(Boolean).join(' ');
    }
    if (typeof detail === 'string') return detail;
  } catch {
    // fall through to the status line
  }
  return `Сравнение отклонено: HTTP ${response.status}`;
}

const RowTable: React.FC<{ title: string; rows: ReferenceReportRow[] }> = ({ title, rows }) => {
  if (!rows.length) return null;
  return (
    <section className="rounded-xl border border-slate-800 bg-slate-950/60 p-4">
      <h4 className="mb-3 text-sm font-semibold text-slate-200">{title}</h4>
      <div className="space-y-2">
        {rows.map((row) => (
          <div key={`${row.id}-${row.path}-${row.ruleId}`} className="rounded-lg border border-slate-800 p-3 text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`font-semibold ${ROW_STYLE[row.status]}`}>{ROW_LABEL[row.status]}</span>
              <span className="text-slate-400">{row.category}</span>
              <span className="font-mono text-slate-300">{row.ruleId}</span>
              <span className="break-all text-slate-400">{row.path}</span>
            </div>
            <p className="mt-1 text-slate-500">{row.reason}</p>
          </div>
        ))}
      </div>
    </section>
  );
};

export const ReferenceChecksPanel: React.FC<ReferenceChecksPanelProps> = ({
  references,
  targets,
  compareUrl,
  onResult,
  className,
}) => {
  const [referenceId, setReferenceId] = useState(references[0]?.id ?? '');
  const [targetId, setTargetId] = useState(targets[0]?.id ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<ReferenceComparisonReport | null>(null);

  const reference = useMemo(
    () => references.find((item) => item.id === referenceId) ?? null,
    [references, referenceId],
  );
  const target = useMemo(() => targets.find((item) => item.id === targetId) ?? null, [targets, targetId]);
  const projectMismatch = Boolean(reference && target && reference.projectId !== target.projectId);

  const compare = async () => {
    if (!reference || !target || busy) return;
    setBusy(true);
    setError(null);
    setReport(null);
    try {
      const bundle = target.bundle ?? (await target.loadBundle?.());
      if (!bundle) throw new Error('У выбранного артефакта нет опубликованного bundle');
      const endpoint = compareUrl ?? `${API_BASE}/reference-checks/compare`;
      const response = await fetch(endpoint, {
        method: 'POST',
        credentials: 'include',
        cache: 'no-store',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          manifest: reference.manifest,
          bundle,
          targetSha: target.sha,
          targetProjectId: target.projectId,
          targetBranch: target.branch ?? null,
        }),
      });
      if (!response.ok) throw new Error(await readError(response));
      const result = (await response.json()) as ReferenceComparisonReport;
      setReport(result);
      onResult?.(result);
    } catch (cause) {
      setError(describeError(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className={`space-y-4 rounded-2xl border border-slate-800 bg-slate-900/60 p-5 ${className ?? ''}`}>
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 text-base font-semibold text-slate-100">
            <ClipboardCheck className="h-4 w-4 text-sky-400" />
            Эталонная проверка
          </h3>
          <p className="mt-1 max-w-2xl text-xs text-slate-400">
            Сравниваем закреплённый эталон с опубликованным bundle другого снимка. Совпадение — только когда
            все ожидаемые находки лежат в проверенных областях, а безопасные контроли не сработали.
          </p>
        </div>
      </header>

      <div className="grid gap-3 md:grid-cols-2">
        <label className="text-xs text-slate-400">
          Эталон (ветка и коммит)
          <select
            value={referenceId}
            onChange={(event) => {
              setReferenceId(event.target.value);
              setReport(null);
            }}
            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200"
          >
            {references.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label} · {item.branch}@{shortSha(item.sha)}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-slate-400">
          Проверяемый снимок / артефакт
          <select
            value={targetId}
            onChange={(event) => {
              setTargetId(event.target.value);
              setReport(null);
            }}
            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200"
          >
            {targets.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label} · {shortSha(item.sha)}
              </option>
            ))}
          </select>
        </label>
      </div>

      {reference && target ? (
        <div className="flex flex-wrap items-center gap-3 text-xs text-slate-400">
          <span className="inline-flex items-center gap-1">
            <GitBranch className="h-3 w-3" /> эталон {shortSha(reference.sha)}
          </span>
          <span>runner {String(reference.manifest.reference.runnerVersion)}</span>
          <span>цель {shortSha(target.sha)}</span>
          {projectMismatch ? (
            <span className="inline-flex items-center gap-1 text-amber-300">
              <AlertTriangle className="h-3 w-3" /> другой проект: сравнение вернёт inconclusive
            </span>
          ) : null}
        </div>
      ) : null}

      <button
        type="button"
        onClick={() => void compare()}
        disabled={!reference || !target || busy}
        className="inline-flex items-center gap-2 rounded-lg border border-sky-700 bg-sky-950/60 px-4 py-2 text-sm font-medium text-sky-200 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <ClipboardCheck className="h-4 w-4" />}
        Сравнить с эталоном
      </button>

      {error ? (
        <p className="rounded-lg border border-rose-800 bg-rose-950/50 p-3 text-sm text-rose-200">{error}</p>
      ) : null}

      {report ? (
        <div className="space-y-4">
          <div className={`rounded-xl border p-4 ${VERDICT_STYLE[report.status]}`}>
            <div className="flex items-center gap-2 text-sm font-semibold">
              {report.status === 'pass' ? (
                <CheckCircle2 className="h-4 w-4" />
              ) : report.status === 'fail' ? (
                <ShieldX className="h-4 w-4" />
              ) : (
                <ShieldQuestion className="h-4 w-4" />
              )}
              {VERDICT_LABEL[report.status]}
            </div>
            <p className="mt-1 text-xs opacity-90">{VERDICT_HINT[report.status]}</p>
            {report.compatibility.reasons.length ? (
              <ul className="mt-2 list-disc space-y-1 pl-5 text-xs">
                {report.compatibility.reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            ) : null}
          </div>

          <dl className="grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
            <div className="rounded-lg border border-slate-800 p-3">
              <dt className="text-slate-500">Находки</dt>
              <dd className="text-slate-200">
                {report.summary.matchedFindings}/{report.summary.expectedFindings}
              </dd>
            </div>
            <div className="rounded-lg border border-slate-800 p-3">
              <dt className="text-slate-500">Пропало</dt>
              <dd className="text-rose-300">{report.summary.missingFindings}</dd>
            </div>
            <div className="rounded-lg border border-slate-800 p-3">
              <dt className="text-slate-500">Контроли</dt>
              <dd className="text-slate-200">
                {report.summary.matchedControls}/{report.summary.expectedControls}
              </dd>
            </div>
            <div className="rounded-lg border border-slate-800 p-3">
              <dt className="text-slate-500">Лишние находки</dt>
              <dd className="text-amber-300">{report.summary.unexpectedFindings}</dd>
            </div>
          </dl>

          <RowTable title="Ожидаемые находки" rows={report.findings} />
          <RowTable title="Безопасные контроли" rows={report.controls} />

          {report.unexpectedFindings.length ? (
            <section className="rounded-xl border border-amber-800 bg-amber-950/30 p-4">
              <h4 className="mb-2 text-sm font-semibold text-amber-200">Лишние находки</h4>
              <ul className="space-y-1 text-xs text-amber-100/80">
                {report.unexpectedFindings.map((item) => (
                  <li key={`${item.category}-${item.fingerprint}`}>
                    {item.category} · {item.ruleId ?? '—'} · {item.path ?? '—'}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </div>
      ) : null}
    </section>
  );
};

export default ReferenceChecksPanel;