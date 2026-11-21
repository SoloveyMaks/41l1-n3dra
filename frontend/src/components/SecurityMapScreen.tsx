import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Network, RefreshCw, AlertCircle } from 'lucide-react';
import { SecurityMap } from '../../../ui/security-map/src/index.ts';
import { sameScope } from '../../../ui/security-map/src/model.ts';
import type {
  EvidenceRequest,
  LoadState,
  SourceState,
} from '../../../ui/security-map/src/types.ts';
import type {
  Finding,
  RunScope,
  SecurityMapDto,
} from '../../../docs/data-contracts/contracts.ts';
import {
  SecurityMapApiError,
  getRunEvidence,
  getRunSecurityMap,
} from '../api/securityMap';

interface SecurityMapScreenProps {
  runId: string | null;
}

export const SecurityMapScreen: React.FC<SecurityMapScreenProps> = ({ runId }) => {
  const [identity, setIdentity] = useState<RunScope | null>(null);
  const [data, setData] = useState<SecurityMapDto | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [loadState, setLoadState] = useState<LoadState>('loading');
  const [error, setError] = useState<{ code: string } | undefined>(undefined);
  const [sourceState, setSourceState] = useState<SourceState>({ status: 'idle' });
  const [reloadKey, setReloadKey] = useState(0);

  const loadSeq = useRef(0);
  const sourceSeq = useRef(0);
  const mapAbort = useRef<AbortController | null>(null);
  const sourceAbort = useRef<AbortController | null>(null);

  useEffect(() => {
    mapAbort.current?.abort();
    sourceAbort.current?.abort();
    sourceSeq.current += 1;
    setSourceState({ status: 'idle' });
    setIdentity(null);
    setData(null);
    setFindings([]);
    setError(undefined);
    if (!runId) return;

    const controller = new AbortController();
    mapAbort.current = controller;
    const seq = ++loadSeq.current;
    setLoadState('loading');

    getRunSecurityMap(runId, controller.signal)
      .then((payload) => {
        if (seq !== loadSeq.current || controller.signal.aborted) return;
        const empty =
          !payload.map.nodes.length &&
          !payload.map.edges.length &&
          !payload.map.findingRefs.length;
        setIdentity(payload.identity);
        setData(payload.map);
        setFindings(payload.findings);
        setError(undefined);
        setLoadState(
          empty
            ? 'empty'
            : payload.map.coverage.status === 'complete'
              ? 'ready'
              : 'partial',
        );
      })
      .catch((err: unknown) => {
        if (seq !== loadSeq.current || controller.signal.aborted) return;
        setIdentity(null);
        setData(null);
        setFindings([]);
        setError({
          code:
            err instanceof SecurityMapApiError && err.code === 'source_unavailable'
              ? 'source_unavailable'
              : 'map_load_failed',
        });
        setLoadState('error');
      });

    return () => controller.abort();
  }, [runId, reloadKey]);

  useEffect(
    () => () => {
      mapAbort.current?.abort();
      sourceAbort.current?.abort();
    },
    [],
  );

  const openEvidence = useCallback(
    (request: EvidenceRequest) => {
      if (!identity || !data || !sameScope(identity, request)) return;
      if (!data.evidence.some((evidence) => evidence.id === request.evidenceId))
        return;

      sourceAbort.current?.abort();
      const controller = new AbortController();
      sourceAbort.current = controller;
      const seq = ++sourceSeq.current;
      setSourceState({ status: 'loading', request });

      getRunEvidence(request, controller.signal)
        .then((state) => {
          if (seq !== sourceSeq.current || controller.signal.aborted) return;
          setSourceState(state);
        })
        .catch((err: unknown) => {
          if (seq !== sourceSeq.current || controller.signal.aborted) return;
          setSourceState({
            status: 'unavailable',
            request,
            code:
              err instanceof SecurityMapApiError
                ? err.code
                : 'evidence_not_available',
          });
        });
    },
    [identity, data],
  );

  if (!runId) {
    return (
      <div className="flex-1 h-full flex flex-col items-center justify-center text-slate-500 gap-2 bg-slate-950 px-4 text-center">
        <Network size={36} className="text-slate-600" />
        <p className="text-sm">Запуск не выбран</p>
        <p className="text-xs text-slate-600 max-w-md">
          Карта строится по снимку выбранного запуска. Откройте «Ход проверки» и выберите запуск в истории.
        </p>
      </div>
    );
  }

  if (loadState === 'loading') {
    return (
      <div className="flex-1 h-full flex flex-col overflow-hidden bg-slate-950" aria-busy="true">
        <div className="border-b border-slate-800 bg-slate-900/70 p-3.5 sm:p-4">
          <div className="flex items-center gap-2">
            <Network size={16} className="text-indigo-400" />
            <h2 className="text-xs font-bold uppercase tracking-wider text-slate-300">Карта доступа</h2>
          </div>
          <p className="mt-1 text-xs text-slate-400">
            Строим граф по снимку выбранного запуска: акторы, маршруты, проверки и данные.
          </p>
          <p className="mt-1 font-mono text-[10px] text-slate-600">Запуск: {runId.slice(0, 8)}</p>
        </div>
        <div className="flex-1 p-4">
          <div className="mx-auto max-w-3xl animate-pulse space-y-3">
            <div className="h-32 rounded-xl border border-slate-800 bg-slate-900/70" />
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <div className="h-20 rounded-xl border border-slate-800 bg-slate-900/70" />
              <div className="h-20 rounded-xl border border-slate-800 bg-slate-900/70" />
              <div className="h-20 rounded-xl border border-slate-800 bg-slate-900/70" />
            </div>
            <p className="text-center text-[11px] text-slate-500">
              Карта не изобретается на клиенте: ждём факты кода и доказательства от сервера.
            </p>
          </div>
        </div>
      </div>
    );
  }

  if (loadState === 'error') {
    const reason = error?.code === 'source_unavailable'
      ? 'Снимок проекта для этого запуска недоступен. Карта не может быть построена без исходников.'
      : 'Сервер не отдал карту доступа для этого запуска.';
    return (
      <div className="flex-1 h-full flex flex-col items-center justify-center gap-3 bg-slate-950 px-4 text-center">
        <div className="rounded-xl border border-rose-900 bg-rose-950/40 p-3 text-rose-300">
          <AlertCircle size={24} />
        </div>
        <div>
          <p className="text-sm font-semibold text-slate-200">Карта доступа недоступна</p>
          <p className="mt-1 max-w-md text-xs text-slate-500">{reason}</p>
        </div>
        <p className="font-mono text-[10px] text-slate-600">
          Запуск: {runId.slice(0, 8)} · код: {error?.code || 'map_load_failed'}
        </p>
        <button
          type="button"
          onClick={() => setReloadKey((key) => key + 1)}
          className="flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-700"
        >
          <RefreshCw size={13} />
          Повторить загрузку
        </button>
      </div>
    );
  }

  return (
    <div className="flex-1 h-full min-h-0 overflow-hidden bg-slate-950">
      <SecurityMap
        identity={identity ?? { workspaceId: '', runId, snapshotId: '' }}
        data={data}
        findings={findings}
        loadState={loadState}
        error={error}
        sourceState={sourceState}
        onOpenEvidence={openEvidence}
        onRetrySource={openEvidence}
        onRetry={() => setReloadKey((key) => key + 1)}
      />
    </div>
  );
};
