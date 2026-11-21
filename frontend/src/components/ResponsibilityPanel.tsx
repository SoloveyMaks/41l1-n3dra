import React, { useEffect, useState } from 'react';
import {
  AlertTriangle, ChevronDown, ChevronRight, ExternalLink, History,
  Loader2, RefreshCw, Users,
} from 'lucide-react';
import { apiClient, ApiError, AuthRequiredError } from '../api/client';
import type { ResponsibilityData } from '../types';

interface ResponsibilityPanelProps {
  runId: string | null;
  findingId: string;
}

const ERROR_TEXT = (error: unknown): string => {
  if (error instanceof AuthRequiredError) return 'Сессия истекла. Войдите заново.';
  if (error instanceof ApiError) {
    if (error.status === 404) return 'Подсказка ответственного недоступна для этой находки или запуска.';
    if (error.status === 503) return 'Ответственный слой временно недоступен; находка и вердикт не затронуты.';
    return error.message;
  }
  return error instanceof Error ? error.message : 'Не удалось загрузить ответственную роль';
};

const HISTORY_LABEL: Record<string, string> = {
  available: 'Последний коммит файла',
  unavailable: 'История недоступна',
  rate_limited: 'Провайдер ограничил запросы',
  failed: 'Провайдер недоступен',
  unsupported: 'История не поддерживается',
};

const shortSha = (sha?: string | null): string => (sha ? sha.slice(0, 12) : '—');

const safeRepositoryUrl = (value?: string | null): string | null => {
  if (!value) return null;
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== 'https:') return null;
    if (!['github.com', 'gitlab.com'].includes(parsed.hostname.toLowerCase())) return null;
    return parsed.toString();
  } catch {
    return null;
  }
};

export const ResponsibilityPanel: React.FC<ResponsibilityPanelProps> = ({ runId, findingId }) => {
  const [data, setData] = useState<ResponsibilityData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (!runId || !findingId) {
      setData(null);
      setLoading(false);
      setError(null);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setData(null);
    apiClient.getFindingResponsibility(runId, findingId, controller.signal)
      .then((value) => {
        if (controller.signal.aborted) return;
        setData(value);
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setError(ERROR_TEXT(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [runId, findingId, reloadKey]);

  if (!runId) return null;

  return (
    <section className="border-t border-slate-800 bg-slate-900/95 px-4 py-3 text-xs text-slate-300">
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-start gap-2">
          <div className="rounded-lg border border-emerald-800/60 bg-emerald-950/40 p-1.5 text-emerald-300">
            <Users size={15} />
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[10px] font-bold uppercase tracking-wider text-slate-500">
                Ответственная роль
              </span>
              <span className="rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 text-[10px] text-slate-400">
                подсказка, не автор уязвимости
              </span>
            </div>
            {loading && (
              <p className="mt-1 flex items-center gap-1.5 text-[11px] text-slate-400">
                <Loader2 size={12} className="animate-spin" /> Подбираем роль, CODEOWNERS и историю…
              </p>
            )}
            {!loading && error && (
              <div className="mt-1 flex items-start gap-1.5 text-[11px] text-amber-200">
                <AlertTriangle size={12} className="mt-0.5 shrink-0" />
                <span>{error}</span>
              </div>
            )}
            {!loading && data && (
              <>
                <p className="mt-1 text-sm font-semibold text-slate-100">{data.suggestedRole.role}</p>
                <p className="mt-0.5 text-[10px] leading-snug text-slate-400">{data.suggestedRole.basis}</p>
              </>
            )}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {data && (
            <span className="rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 text-[9px] uppercase tracking-wider text-slate-400">
              {data.suggestedRole.confidence}
            </span>
          )}
          <button
            type="button"
            onClick={() => setReloadKey((value) => value + 1)}
            disabled={loading}
            className="rounded border border-slate-700 bg-slate-800 p-1 text-slate-400 hover:text-slate-200 disabled:opacity-40"
            title="Обновить ответственную роль"
          >
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
          </button>
          {data && (
            <button
              type="button"
              onClick={() => setExpanded((value) => !value)}
              className="rounded border border-slate-700 bg-slate-800 p-1 text-slate-400 hover:text-slate-200"
              aria-expanded={expanded}
              aria-label={expanded ? 'Свернуть детали ответственности' : 'Показать детали ответственности'}
            >
              {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            </button>
          )}
        </div>
      </div>

      {!loading && data && (
        <div className="mt-2 space-y-1.5">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded border border-slate-800 bg-slate-950/50 px-2 py-1.5">
            <span className="text-[10px] font-bold uppercase tracking-wider text-slate-500">CODEOWNERS</span>
            {data.codeowners.status === 'available' && data.codeowners.owners.length > 0 ? (
              <>
                <span className="text-[11px] text-emerald-300">{data.codeowners.owners.join(', ')}</span>
                <span className="font-mono text-[9px] text-slate-500">
                  {data.codeowners.matchedPattern}
                  {data.codeowners.line ? `:${data.codeowners.line}` : ''}
                </span>
              </>
            ) : data.codeowners.status === 'absent' ? (
              <span className="text-[10px] text-slate-500">В снимке нет CODEOWNERS</span>
            ) : (
              <span className="text-[10px] text-slate-500">{data.codeowners.basis || 'CODEOWNERS недоступен'}</span>
            )}
          </div>

          <div className="rounded border border-slate-800 bg-slate-950/50 px-2 py-1.5">
            <div className="flex flex-wrap items-center gap-2">
              <History size={12} className="text-indigo-300" />
              <span className="text-[10px] font-bold uppercase tracking-wider text-slate-500">
                {HISTORY_LABEL[data.history.status] || data.history.status}
              </span>
              {data.history.commit && (
                <span className="font-mono text-[10px] text-indigo-300">{shortSha(data.history.commit.sha)}</span>
              )}
              <span className="rounded border border-amber-900/60 bg-amber-950/30 px-1.5 py-0.5 text-[9px] text-amber-300">
                {data.history.evidenceKind === 'last_file_commit' ? 'last file commit' : data.history.evidenceKind} · не blame
              </span>
            </div>
            {data.history.commit ? (
              <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-slate-400">
                <span>Автор коммита: <strong className="text-slate-200">{data.history.commit.author || '—'}</strong></span>
                <span>{data.history.commit.date || 'дата неизвестна'}</span>
                {safeRepositoryUrl(data.history.commit.url) && (
                  <a
                    href={safeRepositoryUrl(data.history.commit.url) || '#'}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-indigo-300 hover:text-indigo-200"
                  >
                    Открыть коммит <ExternalLink size={10} />
                  </a>
                )}
              </div>
            ) : (
              <p className="mt-1 text-[10px] leading-snug text-slate-500">{data.history.reason}</p>
            )}
            <p className="mt-1 text-[9px] leading-snug text-amber-300/80">
              Последний коммит файла не является автором уязвимости. Line blame: {data.history.lineBlame.reason}
            </p>
          </div>

          {expanded && (
            <div className="space-y-1.5 rounded border border-slate-800 bg-slate-950/50 px-2 py-1.5">
              {data.codeowners.limitations.length > 0 && (
                <div>
                  <div className="text-[9px] font-bold uppercase tracking-wider text-slate-600">CODEOWNERS</div>
                  <ul className="mt-0.5 list-inside list-disc text-[9px] leading-snug text-slate-500">
                    {data.codeowners.limitations.map((item, index) => <li key={index}>{item}</li>)}
                  </ul>
                </div>
              )}
              <div>
                <div className="text-[9px] font-bold uppercase tracking-wider text-slate-600">Основания и границы</div>
                <ul className="mt-0.5 list-inside list-disc text-[9px] leading-snug text-slate-500">
                  {data.limitations.map((item, index) => <li key={index}>{item}</li>)}
                  {data.history.limitations.map((item, index) => <li key={`h-${index}`}>{item}</li>)}
                </ul>
              </div>
              {data.history.provider && (
                <p className="font-mono text-[9px] text-slate-600">
                  provider: {data.history.provider} · frozen commit: {shortSha(data.scope.commitSha)} · path: {data.scope.path}
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
};
