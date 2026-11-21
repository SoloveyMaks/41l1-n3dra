import React, { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { AlertCircle, Loader2, RefreshCw, X } from 'lucide-react';
import type { RepositorySource } from '../../types';
import type { RunScope } from '../../../../docs/data-contracts/contracts.ts';
import { getRunEvidence, getRunSecurityMap, SecurityMapApiError } from '../../api/securityMap';
import { providerLabel, repositoryShortName } from '../../repository';
import { deriveFileGraph } from './derive';
import type { MaskedReadRequest, MaskedReadResult, RepositoryFileGraphData } from './types';
import { RepositoryFileGraphPanel } from './RepositoryFileGraphPanel';

interface RepositoryFileGraphModalProps {
  runId: string;
  repository?: RepositorySource | null;
  onClose: () => void;
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: RepositoryFileGraphData };

const humanEvidenceReason = (code: string): string => {
  if (code === 'source_unavailable') return 'Снимок проекта недоступен.';
  if (code === 'source_scope_mismatch') return 'Фрагмент относится к другому запуску.';
  if (code === 'source_redaction_incomplete') return 'Маскирование фрагмента не завершено.';
  if (code === 'evidence_not_available') return 'Сохранённый фрагмент не найден.';
  return 'Сервер не отдал фрагмент.';
};

const humanMapError = (error: unknown): string => {
  if (error instanceof SecurityMapApiError) {
    if (error.status === 409) {
      return 'Карта ещё не построена: запуск не завершил статический анализ или снимок недоступен.';
    }
    if (error.status === 503) return 'Снимок проекта для этого запуска недоступен.';
    if (error.code === 'map_contract_mismatch') {
      return 'Ответ карты не соответствует контракту. Дерево не достраивается по догадкам.';
    }
    return error.message;
  }
  return 'Не удалось загрузить факты карты для файлового графа.';
};

export const RepositoryFileGraphModal: React.FC<RepositoryFileGraphModalProps> = ({
  runId,
  repository = null,
  onClose,
}) => {
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  const [identity, setIdentity] = useState<RunScope | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    getRunSecurityMap(runId, controller.signal)
      .then((payload) => {
        if (controller.signal.aborted) return;
        setIdentity(payload.identity);
        setState({ status: 'ready', data: deriveFileGraph(payload.map, payload.findings) });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({ status: 'error', message: humanMapError(error) });
      });
    return () => controller.abort();
  }, [runId, reloadKey]);

  const retry = useCallback(() => {
    setIdentity(null);
    setState({ status: 'loading' });
    setReloadKey((value) => value + 1);
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKeyDown);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [onClose]);

  const readMasked = useCallback(
    async (request: MaskedReadRequest): Promise<MaskedReadResult> => {
      if (!identity) return { status: 'unavailable', reason: 'Снимок ещё не загружен.' };
      try {
        const source = await getRunEvidence({
          workspaceId: identity.workspaceId,
          runId: identity.runId,
          snapshotId: identity.snapshotId,
          evidenceId: request.evidenceId,
        });
        if (source.status === 'ready') {
          if (source.location.path !== request.path) {
            return { status: 'unavailable', reason: 'Фрагмент относится к другому файлу.' };
          }
          return {
            status: 'ready',
            text: source.maskedFragment,
            redactionVersion: source.redactionVersion,
          };
        }
        if (source.status === 'error' || source.status === 'unavailable') {
          return { status: 'unavailable', reason: humanEvidenceReason(source.code) };
        }
        return { status: 'unavailable', reason: 'Фрагмент ещё не готов.' };
      } catch (error: unknown) {
        return {
          status: 'unavailable',
          reason:
            error instanceof SecurityMapApiError
              ? humanEvidenceReason(error.code)
              : 'Не удалось прочитать masked-фрагмент.',
        };
      }
    },
    [identity],
  );

  const subtitle = repository
    ? `${providerLabel(repository.provider)} · ${repositoryShortName(repository.url)}`
    : 'Файлы проверки';

  const content =
    state.status === 'loading' ? (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
        <Loader2 size={26} className="animate-spin text-indigo-400" />
        <p className="text-sm text-slate-300">Собираем файловый граф из фактов запуска…</p>
        <p className="max-w-md text-[11px] text-slate-500">
          Дерево строится только по evidence, находкам и покрытию. Папки не изображают вызовы.
        </p>
      </div>
    ) : state.status === 'error' ? (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
        <div className="rounded-xl border border-rose-900 bg-rose-950/40 p-3 text-rose-300">
          <AlertCircle size={24} />
        </div>
        <p className="max-w-lg text-sm text-slate-300">{state.message}</p>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={retry}
            className="flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-700"
          >
            <RefreshCw size={13} /> Повторить
          </button>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-slate-800 px-3 py-1.5 text-xs text-slate-400 hover:bg-slate-800 hover:text-slate-200"
          >
            Закрыть
          </button>
        </div>
      </div>
    ) : (
      <RepositoryFileGraphPanel
        key={runId}
        data={state.data}
        repository={repository}
        readMasked={readMasked}
        onClose={onClose}
        className="flex-1"
      />
    );

  return createPortal(
    <div
      className="fixed inset-0 z-[90] flex items-center justify-center bg-slate-950/80 p-2 backdrop-blur-sm sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Файловый граф проверки"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="flex h-[min(92vh,920px)] w-full max-w-[1400px] flex-col overflow-hidden rounded-2xl border border-slate-700 bg-slate-950 shadow-2xl shadow-black/50">
        {state.status !== 'ready' ? (
          <div className="flex shrink-0 items-center gap-3 border-b border-slate-800 bg-slate-900 px-3 py-2 sm:px-4">
            <div className="min-w-0 flex-1">
              <div className="truncate text-xs font-semibold text-slate-200">{subtitle}</div>
              <div className="truncate font-mono text-[10px] text-slate-500">запуск {runId.slice(0, 8)}</div>
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label="Закрыть файловый граф"
              className="shrink-0 rounded-lg border border-slate-700 bg-slate-900 p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-100"
            >
              <X size={15} />
            </button>
          </div>
        ) : null}
        {content}
      </div>
    </div>,
    document.body,
  );
};