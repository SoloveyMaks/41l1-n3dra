import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  ClipboardCheck,
  GitBranch,
  Loader2,
  Server,
  ShieldQuestion,
  ShieldX,
  Unplug,
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
  pass: 'Все ожидаемые находки на месте в проверенных областях, контроли чисты, все сканеры завершены.',
  fail: 'Есть подтверждённое расхождение: пропавшая находка в проверенной области, изменившийся fingerprint или сработавший контроль.',
  inconclusive: 'Нет полного покрытия, сканер не завершён или сервер не подтвердил происхождение. Это не pass.',
};

const VERDICT_STYLE: Record<ReferenceVerdict, string> = {
  pass: 'border-emerald-800 bg-emerald-950/60 text-emerald-300',
  fail: 'border-rose-800 bg-rose-950/60 text-rose-300',
  inconclusive: 'border-amber-800 bg-amber-950/60 text-amber-300',
};

const ROW_LABEL: Record<ReferenceReportRow['status'], string> = {
  matched: 'совпало',
  missing: 'пропало',
  mismatch: 'изменилось',
  inconclusive: 'не проверено',
  triggered: 'контроль сработал',
};

const ROW_STYLE: Record<ReferenceReportRow['status'], string> = {
  matched: 'text-emerald-300',
  missing: 'text-rose-300',
  mismatch: 'text-rose-300',
  inconclusive: 'text-amber-300',
  triggered: 'text-rose-300',
};

const ERROR_HINT: Record<string, string> = {
  REFERENCE_UNKNOWN: 'Сервер не знает такой эталонный манифест.',
  REFERENCE_UNREADABLE: 'Серверный манифест эталона недоступен или повреждён.',
  INVALID_MANIFEST: 'Манифест эталона неполный или противоречивый.',
  UNSUPPORTED_MANIFEST: 'Версия формата эталона не поддерживается этим сравнением.',
  INVALID_PROVENANCE: 'Манифест эталона не закрепил SHA/хэши или версию runner.',
  TARGET_RUN_UNKNOWN: 'Запуск не найден в вашем workspace.',
  TARGET_ARTIFACT_UNKNOWN: 'Артефакт запуска не найден в вашем workspace.',
  TARGET_RUN_ARTIFACT_MISMATCH: 'Артефакт не принадлежит выбранному запуску.',
  TARGET_SNAPSHOT_UNKNOWN: 'У запуска нет снимка для сравнения.',
  TARGET_SNAPSHOT_MISMATCH: 'Снимок запуска не совпал с закреплённым снимком.',
  TARGET_NOT_PUBLISHED: 'У запуска нет опубликованного canonical bundle.',
  TARGET_CANONICAL_INCOMPLETE: 'Опубликованный envelope неполный: нет manifest/provenance.',
  TARGET_IDENTITY_MISMATCH: 'Опубликованный bundle не совпал с серверным запуском/артефактом.',
  INVALID_BUNDLE: 'Bundle повреждён или не соответствует контракту AnalysisBundle 0.1.',
  INCOMPATIBLE_BUNDLE: 'Bundle несовместим: подпись, fingerprintVersion или вложенная идентичность.',
};

const shortSha = (sha: string | null | undefined): string =>
  !sha ? '—' : sha.length > 12 ? `${sha.slice(0, 12)}…` : sha;

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
  const requestEpoch = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  const reference = useMemo(
    () => references.find((item) => item.id === referenceId) ?? null,
    [references, referenceId],
  );
  const target = useMemo(() => targets.find((item) => item.id === targetId) ?? null, [targets, targetId]);
  const projectMismatch = Boolean(reference?.projectId && target?.projectId && reference.projectId !== target.projectId);

  const disposeResult = () => {
    requestEpoch.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    setBusy(false);
    setError(null);
    setReport(null);
  };

  useEffect(() => () => abortRef.current?.abort(), []);

  const compare = async () => {
    if (!reference || !target || busy) return;
    const epoch = requestEpoch.current + 1;
    requestEpoch.current = epoch;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setBusy(true);
    setError(null);
    setReport(null);
    try {
      const endpoint = compareUrl ?? `${API_BASE}/reference-checks/compare`;
      const response = await fetch(endpoint, {
        method: 'POST',
        credentials: 'include',
        cache: 'no-store',
        signal: controller.signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          referenceManifestId: reference.id,
          targetRunId: target.runId,
          targetArtifactId: target.artifactId ?? null,
        }),
      });
      if (!response.ok) throw new Error(await readError(response));
      const result = (await response.json()) as ReferenceComparisonReport;
      if (epoch !== requestEpoch.current) return;
      setReport(result);
      onResult?.(result);
    } catch (cause) {
      if (epoch !== requestEpoch.current) return;
      if (cause instanceof DOMException && cause.name === 'AbortError') return;
      setError(describeError(cause));
    } finally {
      if (epoch === requestEpoch.current) {
        setBusy(false);
        abortRef.current = null;
      }
    }
  };

  const attestation =
    report?.provenance.attestation === 'canonical-published' && report.provenance.verified
      ? 'проверено сервером'
      : 'без аттестации';

  return (
    <section className={`space-y-4 rounded-2xl border border-slate-800 bg-slate-900/60 p-5 ${className ?? ''}`}>
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 text-base font-semibold text-slate-100">
            <ClipboardCheck className="h-4 w-4 text-sky-400" />
            Эталонная проверка
          </h3>
          <p className="mt-1 max-w-2xl text-xs text-slate-400">
            Сервер сам загружает закреплённый эталон и опубликованный bundle выбранного запуска.
            Клиент передаёт только id, не манифест, не bundle и не commit SHA.
          </p>
        </div>
      </header>

      <div className="grid gap-3 md:grid-cols-2">
        <label className="text-xs text-slate-400">
          Эталон (серверный манифест)
          <select
            value={referenceId}
            onChange={(event) => {
              setReferenceId(event.target.value);
              disposeResult();
            }}
            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200"
          >
            {references.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
                {item.branch ? ` · ${item.branch}` : ''}
                {item.sha ? `@${shortSha(item.sha)}` : ''}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-slate-400">
          Проверяемый запуск
          <select
            value={targetId}
            onChange={(event) => {
              setTargetId(event.target.value);
              disposeResult();
            }}
            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200"
          >
            {targets.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
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
          <span className="inline-flex items-center gap-1">
            <Server className="h-3 w-3" /> запуск {target.runId}
          </span>
          {target.artifactId ? <span>артефакт {target.artifactId}</span> : null}
          <span className="text-slate-500">SHA цели определит сервер</span>
          {projectMismatch ? (
            <span className="inline-flex items-center gap-1 text-amber-300">
              <AlertTriangle className="h-3 w-3" /> подписи проектов в UI различаются; сервер решит по provenance
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
            <div className="flex flex-wrap items-center gap-2 text-sm font-semibold">
              {report.status === 'pass' ? (
                <CheckCircle2 className="h-4 w-4" />
              ) : report.status === 'fail' ? (
                <ShieldX className="h-4 w-4" />
              ) : (
                <ShieldQuestion className="h-4 w-4" />
              )}
              {VERDICT_LABEL[report.status]}
              <span
                className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium ${
                  attestation === 'проверено сервером'
                    ? 'border-emerald-700 text-emerald-200'
                    : 'border-amber-700 text-amber-200'
                }`}
              >
                {attestation === 'проверено сервером' ? <Server className="h-3 w-3" /> : <Unplug className="h-3 w-3" />}
                {attestation}
              </span>
            </div>
            <p className="mt-1 text-xs opacity-90">{VERDICT_HINT[report.status]}</p>
            <p className="mt-2 text-[11px] opacity-80">
              эталон {shortSha(report.reference.sha)} · цель {shortSha(report.target.sha)} ·{' '}
              {report.target.provenance}
            </p>
            {report.compatibility.reasons.length ? (
              <ul className="mt-2 list-disc space-y-1 pl-5 text-xs">
                {report.compatibility.reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            ) : null}
            {report.provenance.reasons.length ? (
              <ul className="mt-2 list-disc space-y-1 pl-5 text-xs">
                {report.provenance.reasons.map((reason) => (
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

          {report.compatibility.coverageReasons.length ? (
            <section className="rounded-xl border border-amber-800 bg-amber-950/30 p-4">
              <h4 className="mb-2 text-sm font-semibold text-amber-200">Неполное покрытие</h4>
              <ul className="space-y-1 text-xs text-amber-100/80">
                {report.compatibility.coverageReasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            </section>
          ) : null}

          <RowTable title="Ожидаемые находки" rows={report.findings} />
          <RowTable title="Безопасные контроли" rows={report.controls} />

          {report.unexpectedFindings.length ? (
            <section className="rounded-xl border border-amber-800 bg-amber-950/30 p-4">
              <h4 className="mb-2 text-sm font-semibold text-amber-200">Лишние находки</h4>
              <ul className="space-y-1 text-xs text-amber-100/80">
                {report.unexpectedFindings.map((item) => (
                  <li key={`${item.category}-${item.fingerprint}`}>
                    {item.category} · {item.ruleId ?? '—'} · {item.path ?? '—'} · {item.status ?? '—'}
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
