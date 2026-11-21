import React, { useState } from 'react';
import { FileSearch, GitBranch } from 'lucide-react';
import { RepositorySource } from '../types';
import { providerLabel, repositoryShortName, shortCommit } from '../repository';
import { RepositoryFileGraphModal } from './repository-graph';

interface RepositorySourceViewProps {
  repository: RepositorySource;
  className?: string;
  showProvider?: boolean;
  /**
   * Запуск, по фактам которого открывается файловый граф.
   * Без него компонент остаётся компактной строкой источника.
   */
  runId?: string | null;
}

/**
 * Компактная строка источника: провайдер, owner/repo, ветка и короткий SHA.
 * Показывается только для артефакта выбранной проверки — не для черновика.
 * Если передан runId, рядом появляется вход в отдельное дерево файлов.
 */
export const RepositorySourceView: React.FC<RepositorySourceViewProps> = ({
  repository,
  className = '',
  showProvider = true,
  runId = null,
}) => {
  const [graphOpen, setGraphOpen] = useState(false);

  return (
    <>
      <div className={`flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-slate-400 ${className}`}>
        <GitBranch size={12} className="shrink-0 text-indigo-400" />
        <span className="min-w-0 break-all text-slate-300" title={repository.url}>
          {showProvider ? `${providerLabel(repository.provider)} · ` : ''}
          {repositoryShortName(repository.url)}
        </span>
        <span className="whitespace-nowrap">
          ветка <span className="font-mono text-slate-300">{repository.branch || '—'}</span>
        </span>
        <span className="whitespace-nowrap">
          коммит <span className="font-mono text-indigo-300" title={repository.commit_sha}>{shortCommit(repository.commit_sha)}</span>
        </span>
        {runId ? (
          <button
            type="button"
            onClick={() => setGraphOpen(true)}
            className="flex shrink-0 items-center gap-1 rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 text-[10px] text-slate-300 hover:border-indigo-800 hover:bg-indigo-950/40 hover:text-indigo-200"
            title="Открыть дерево файлов проверки"
          >
            <FileSearch size={11} />
            Файлы
          </button>
        ) : null}
      </div>
      {graphOpen && runId ? (
        <RepositoryFileGraphModal
          key={runId}
          runId={runId}
          repository={repository}
          onClose={() => setGraphOpen(false)}
        />
      ) : null}
    </>
  );
};