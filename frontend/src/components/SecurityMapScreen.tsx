import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Network } from 'lucide-react';
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
      <div className="flex-1 h-full flex flex-col items-center justify-center text-slate-500 gap-2 bg-slate-950">
        <Network size={36} className="text-slate-600" />
        <p className="text-sm">Запуск не выбран</p>
        <p className="text-xs text-slate-600">
          Запустите workflow или выберите существующий запуск — карта строится по его snapshot.
        </p>
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
