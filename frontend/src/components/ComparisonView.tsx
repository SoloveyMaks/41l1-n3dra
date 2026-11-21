import React, { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, GitCompare, ShieldAlert } from 'lucide-react';
import { ComparisonDto, ComparisonStatus, RunSummary } from '../types';
import { apiClient } from '../api/client';

const STATUS_LABEL: Record<ComparisonStatus, string> = {
  persistent: 'Остаётся',
  introduced: 'Появилась',
  changed: 'Изменилась',
  resolved: 'Исправлена',
  inconclusive: 'Не подтверждено',
  file_deleted: 'Файл удалён',
};

const STATUS_CLASS: Record<ComparisonStatus, string> = {
  persistent: 'bg-amber-950 text-amber-300 border-amber-800',
  introduced: 'bg-rose-950 text-rose-300 border-rose-800',
  changed: 'bg-sky-950 text-sky-300 border-sky-800',
  resolved: 'bg-emerald-950 text-emerald-300 border-emerald-800',
  inconclusive: 'bg-slate-800 text-slate-300 border-slate-700',
  file_deleted: 'bg-fuchsia-950 text-fuchsia-300 border-fuchsia-800',
};

interface ComparisonViewProps {
  runs: RunSummary[];
}

export const ComparisonView: React.FC<ComparisonViewProps> = ({ runs }) => {
  const finished = useMemo(
    () => runs.filter((run) => run.execution_status === 'succeeded' || run.execution_status === 'failed'),
    [runs],
  );
  const [baseline, setBaseline] = useState('');
  const [current, setCurrent] = useState('');
  const [result, setResult] = useState<ComparisonDto | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (finished.length >= 2 && (!baseline || !current)) {
      setCurrent(finished[0]?.id || '');
      setBaseline(finished[1]?.id || '');
    }
  }, [finished, baseline, current]);

  const compare = async () => {
    if (!baseline || !current || baseline === current) {
      setError('Выберите два разных завершённых запуска');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setResult(await apiClient.compareRuns(baseline, current));
    } catch (e: any) {
      setResult(null);
      setError(e?.message || 'Не удалось сравнить запуски');
    } finally {
      setBusy(false);
    }
  };

  const select = (value: string, onChange: (value: string) => void, label: string) => (
    <label className="block text-xs text-slate-400">
      <span className="mb-1 block">{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-lg bg-slate-950 border border-slate-700 px-3 py-2 text-xs text-slate-200"
      >
        <option value="">Выберите запуск</option>
        {finished.map((run) => (
          <option key={run.id} value={run.id}>
            {run.id} · {run.security_verdict} · {new Date(run.created_at).toLocaleString('ru-RU')}
          </option>
        ))}
      </select>
    </label>
  );

  return (
    <section className="flex-1 overflow-auto p-6 bg-slate-950">
      <div className="mx-auto max-w-5xl space-y-4">
        <div className="flex items-center gap-2">
          <GitCompare size={18} className="text-indigo-400" />
          <h2 className="text-sm font-semibold text-white">Сравнение published runs</h2>
        </div>
        <p className="text-xs text-slate-400">
          Сравнение не запускает сканеры и не считает partial coverage доказанным исправлением.
          Неизвестные связи и потеря покрытия остаются видимыми.
        </p>

        <div className="grid gap-3 rounded-xl border border-slate-800 bg-slate-900/70 p-4 md:grid-cols-[1fr_1fr_auto] md:items-end">
          {select(baseline, setBaseline, 'Baseline (до)')}
          {select(current, setCurrent, 'Current (после)')}
          <button
            onClick={compare}
            disabled={busy}
            className="rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:bg-slate-700 px-4 py-2 text-xs font-semibold text-white"
          >
            {busy ? 'Сравниваем…' : 'Сравнить'}
          </button>
        </div>

        {error && (
          <div role="alert" className="flex items-start gap-2 rounded-xl border border-rose-900 bg-rose-950/50 p-3 text-xs text-rose-200">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {result && (
          <div className="space-y-4">
            <div className={`rounded-xl border p-4 text-xs ${result.comparable ? 'border-emerald-900 bg-emerald-950/40 text-emerald-200' : 'border-amber-900 bg-amber-950/40 text-amber-200'}`}>
              <div className="font-semibold">
                {result.comparable ? 'Сравнение сопоставимо' : 'Сравнение не сопоставимо — только inconclusive'}
              </div>
              {result.reasons.length > 0 && (
                <ul className="mt-2 list-disc space-y-1 pl-5">
                  {result.reasons.map((reason) => <li key={reason}>{reason}</li>)}
                </ul>
              )}
            </div>

            <div className="overflow-hidden rounded-xl border border-slate-800">
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-900 text-slate-400">
                  <tr>
                    <th className="px-3 py-2">Статус</th>
                    <th className="px-3 py-2">Основание</th>
                    <th className="px-3 py-2">Причина</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800">
                  {result.items.map((item) => (
                    <tr key={item.id} className="align-top">
                      <td className="px-3 py-2">
                        <span className={`inline-block rounded border px-1.5 py-0.5 text-[10px] font-semibold ${STATUS_CLASS[item.status]}`}>
                          {STATUS_LABEL[item.status]}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-slate-400">{item.matchingBasis}</td>
                      <td className="px-3 py-2 text-slate-300">{item.reason}</td>
                    </tr>
                  ))}
                  {result.items.length === 0 && (
                    <tr><td colSpan={3} className="px-3 py-4 text-center text-slate-500">Пунктов сравнения нет</td></tr>
                  )}
                </tbody>
              </table>
            </div>

            {result.deletedFiles.length > 0 && (
              <div className="rounded-xl border border-fuchsia-900 bg-fuchsia-950/20 p-3">
                <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-fuchsia-200">
                  <ShieldAlert size={15} /> Удалённые файлы — это не доказательство исправления
                </div>
                <ul className="space-y-1 text-xs text-fuchsia-100/80">
                  {result.deletedFiles.map((file) => (
                    <li key={file.path}><code>{file.path}</code> — {file.reason}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
};
