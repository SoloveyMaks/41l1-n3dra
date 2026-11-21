import React from 'react';
import { GitBranch } from 'lucide-react';
import { RepositorySource } from '../types';
import { providerLabel, repositoryShortName, shortCommit } from '../repository';

interface RepositorySourceViewProps {
  repository: RepositorySource;
  className?: string;
  showProvider?: boolean;
}

/**
 * Компактная строка источника: провайдер, owner/repo, ветка и короткий SHA.
 * Показывается только для артефакта выбранной проверки — не для черновика.
 */
export const RepositorySourceView: React.FC<RepositorySourceViewProps> = ({
  repository,
  className = '',
  showProvider = true,
}) => (
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
  </div>
);