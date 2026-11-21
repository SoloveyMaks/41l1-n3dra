import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, Loader2, RefreshCw } from 'lucide-react';
import { API_BASE } from '../api/client';
import { ReferenceChecksPanel } from './reference-checks/ReferenceChecksPanel';
import type { PinnedReference, PinnedTarget } from './reference-checks/types';
import type { RunSummary } from '../types';

type RegistryState =
  | { status: 'loading' }
  | { status: 'ready'; references: PinnedReference[] }
  | { status: 'error'; message: string };

const RUN_STATUS_LABEL: Record<RunSummary['execution_status'], string> = {
  queued: 'в очереди',
  running: 'выполняется',
  succeeded: 'завершён',
  failed: 'ошибка',
  interrupted: 'прерван',
  cancelled: 'отменён',
};

const optionalString = (value: unknown): string | undefined =>
  typeof value === 'string' && value.trim() ? value.trim() : undefined;

/**
 * Server descriptors only: the UI never invents an id, SHA or verdict. An
 * entry without a usable id is dropped rather than silently renamed, and an
 * empty registry stays empty.
 */
const readReferences = (payload: unknown): PinnedReference[] => {
  if (!Array.isArray(payload)) throw new Error('Не удалось загрузить список эталонов. Повторите позже.');
  const seen = new Set<string>();
  const result: PinnedReference[] = [];
  for (const raw of payload) {
    if (!raw || typeof raw !== 'object') continue;
    const entry = raw as Record<string, unknown>;
    const id = optionalString(entry.id);
    if (!id || seen.has(id)) continue;
    seen.add(id);
    result.push({
      id,
      label: optionalString(entry.label) || id,
      projectId: optionalString(entry.projectId),
      branch: optionalString(entry.branch),
      sha: optionalString(entry.sha),
    });
  }
  return result;
};

const describeLoadError = (error: unknown): string => {
  if (error instanceof TypeError) return 'Не удалось загрузить список эталонов. Повторите позже.';
  if (error instanceof Error) return error.message;
  return 'Не удалось загрузить список эталонов. Повторите позже.';
};

const describeStatusError = (status: number): string => {
  if (status === 401 || status === 403) return 'Нужен вход в аккаунт с доступом к workspace.';
  return 'Не удалось загрузить список эталонов. Повторите позже.';
};

interface ReferenceChecksSectionProps {
  runs: RunSummary[];
  /** Scope key: changing it discards descriptors and any old report. */
  workspaceId?: string;
  role?: string;
}

/**
 * Root integration for pinned reference checks: loads the server-owned
 * registry (`GET /api/reference-checks/references`), maps existing runs to
 * target descriptors and hands both to the standalone panel. Nothing here
 * fabricates a manifest, SHA, artifact or pass verdict.
 */
export const ReferenceChecksSection: React.FC<ReferenceChecksSectionProps> = ({
  runs,
  workspaceId = '',
  role = '',
}) => {
  const [state, setState] = useState<RegistryState>({ status: 'loading' });
  const requestEpoch = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    const epoch = requestEpoch.current + 1;
    requestEpoch.current = epoch;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setState({ status: 'loading' });
    try {
      const response = await fetch(`${API_BASE}/reference-checks/references`, {
        credentials: 'include',
        cache: 'no-store',
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(describeStatusError(response.status));
      const payload: unknown = await response.json();
      if (epoch !== requestEpoch.current) return;
      setState({ status: 'ready', references: readReferences(payload) });
    } catch (error) {
      if (epoch !== requestEpoch.current) return;
      if (error instanceof DOMException && error.name === 'AbortError') return;
      setState({ status: 'error', message: describeLoadError(error) });
    } finally {
      if (epoch === requestEpoch.current) abortRef.current = null;
    }
  }, []);

  useEffect(() => {
    void load();
    return () => {
      requestEpoch.current += 1;
      abortRef.current?.abort();
      abortRef.current = null;
    };
  }, [load, workspaceId, role]);

  const targets = useMemo<PinnedTarget[]>(
    () => runs.map((run) => ({
      id: run.id,
      label: `${run.id} · ${RUN_STATUS_LABEL[run.execution_status] ?? run.execution_status}`,
      runId: run.id,
      artifactId: run.artifact_id || undefined,
    })),
    [runs],
  );

  // Remount the panel when the available descriptors or target runs change:
  // a selection from a previous context must never survive into a new one.
  const panelKey = useMemo(() => {
    const referenceIds = state.status === 'ready' ? state.references.map((item) => item.id).join(',') : '';
    const targetIds = targets.map((item) => `${item.runId}:${item.artifactId ?? ''}`).join(',');
    return `${workspaceId}|${role}|${referenceIds}|${targetIds}`;
  }, [state, targets, workspaceId, role]);

  return (
    <section className="space-y-3" aria-label="Эталонные проверки">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-slate-100">Эталонные проверки</h2>
          <p className="text-[11px] text-slate-500">
            Сравните проверяемый запуск с закреплённым эталоном.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={state.status === 'loading'}
          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-[11px] text-slate-300 hover:bg-slate-800 disabled:opacity-50"
        >
          <RefreshCw size={12} className={state.status === 'loading' ? 'animate-spin' : ''} />
          Обновить список
        </button>
      </div>

      {state.status === 'loading' && (
        <div className="flex items-center gap-2 rounded-xl border border-slate-800 bg-slate-900/60 px-4 py-6 text-xs text-slate-400">
          <Loader2 size={14} className="animate-spin" /> Загружаем список эталонов…
        </div>
      )}

      {state.status === 'error' && (
        <div
          role="alert"
          className="flex flex-wrap items-start gap-2 rounded-xl border border-amber-800 bg-amber-950/40 px-3 py-2.5 text-xs text-amber-200"
        >
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <div className="min-w-0 flex-1">
            <div className="font-medium">Не удалось загрузить список эталонов.</div>
            <div className="mt-0.5 break-words text-amber-300/90">{state.message}</div>
            <div className="mt-1 text-[10px] text-amber-300/70">
              Сравнение станет доступно после загрузки списка эталонов.
            </div>
          </div>
          <button
            type="button"
            onClick={() => void load()}
            className="rounded border border-amber-700 px-2 py-1 text-[10px] text-amber-100 hover:bg-amber-900/60"
          >
            Повторить
          </button>
        </div>
      )}

      {state.status === 'ready' && state.references.length === 0 && (
        <div className="rounded-xl border border-dashed border-slate-700 bg-slate-900/40 px-4 py-5 text-center text-xs text-slate-500">
          Закреплённых эталонов пока нет. Сравнение станет доступно, когда эталон закрепят.
        </div>
      )}

      {state.status === 'ready' && state.references.length > 0 && targets.length === 0 && (
        <div className="rounded-xl border border-dashed border-slate-700 bg-slate-900/40 px-4 py-5 text-center text-xs text-slate-500">
          Нет запусков для сравнения. Сначала выполните проверку проекта.
        </div>
      )}

      {state.status === 'ready' && state.references.length > 0 && targets.length > 0 && (
        <ReferenceChecksPanel key={panelKey} references={state.references} targets={targets} />
      )}
    </section>
  );
};
