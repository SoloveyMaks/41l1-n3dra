import React, { useEffect, useState } from 'react';
import { listCustomFindings, type CustomCheckRunSummary } from '../api/customChecks';

export const CustomFindingsPanel: React.FC<{ runId: string | null; runStatus?: string }> = ({ runId, runStatus }) => {
  const scope = `${runId || ''}:${runStatus || ''}`;
  const [loaded, setLoaded] = useState<{ scope: string; results: CustomCheckRunSummary[]; error: string } | null>(null);
  useEffect(() => {
    if (!runId) return;
    const controller = new AbortController();
    listCustomFindings(runId, controller.signal)
      .then((items) => {
        if (!controller.signal.aborted) setLoaded({ scope, results: items, error: '' });
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setLoaded({ scope, results: [], error: reason instanceof Error ? reason.message : 'Не удалось загрузить свои проверки' });
      });
    return () => controller.abort();
  }, [runId, scope]);
  const results = loaded?.scope === scope ? loaded.results : [];
  const error = loaded?.scope === scope ? loaded.error : '';
  if (!results.length && !error) return null;
  return (
    <section className="border-b border-violet-900/60 bg-violet-950/20 p-3 text-xs text-slate-300">
      <h3 className="font-semibold text-violet-200">Результаты своих проверок</h3>
      <p className="mt-1 text-[10px] text-slate-400">Показаны отдельно от штатных находок и не меняют вердикт.</p>
      {error && <p role="alert" className="mt-2 text-amber-300">{error}</p>}
      {results.map((result) => (
        <details key={result.id} className="mt-2 rounded border border-slate-800 p-2">
          <summary className="cursor-pointer break-words">
            {result.definitionName} · v{result.definitionVersion} · {result.status === 'succeeded' ? 'завершена' : result.status} · результатов: {result.findings.length}
          </summary>
          {result.findings.map((finding) => (
            <div key={finding.id} className="mt-2 break-words [overflow-wrap:anywhere]">
              <span className="text-violet-200">{finding.ruleId}</span> · {finding.severity} · {finding.path}:{finding.startLine}
              <p className="mt-0.5">{finding.message}</p>
            </div>
          ))}
        </details>
      ))}
    </section>
  );
};
