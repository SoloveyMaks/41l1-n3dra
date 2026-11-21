import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ChevronDown,
  ChevronRight,
  FileSearch,
  Files,
  ListTree,
  Network,
  Search,
  X,
} from 'lucide-react';
import type { RepositorySource } from '../../types';
import { providerLabel, repositoryShortName, shortCommit } from '../../repository';
import type {
  MaskedFragmentEntry,
  MaskedReadRequest,
  MaskedReadResult,
  RepositoryFileGraphData,
} from './types';
import { EVIDENCE_PREVIEW_LIMIT } from './types';
import { buildFileTree, collectFolderIds, countFiles, filterFileTree, folderIdsForPath } from './tree';
import { FileTreeView } from './FileTreeView';
import { FileGraphView } from './FileGraphView';
import { FileDetailPanel } from './FileDetailPanel';

type ViewMode = 'tree' | 'graph';

const SCOPE_LABEL: Record<string, string> = {
  'runtime-configuration': 'конфигурация запуска',
  'runtime-guards': 'проверки доступа во время работы',
  'binaries-and-archives': 'бинарные файлы и архивы',
};

export interface RepositoryFileGraphPanelProps {
  data: RepositoryFileGraphData;
  repository?: RepositorySource | null;
  readMasked?: (request: MaskedReadRequest) => Promise<MaskedReadResult>;
  onClose?: () => void;
  className?: string;
}

/**
 * Отдельный файловый граф: дерево путей и компактный граф файлов.
 * Папки — навигация, не доказательство вызовов. Межфайловые рёбра приходят
 * только из доказанных фактов; masked-код читается по evidence, а не из пути.
 */
export const RepositoryFileGraphPanel: React.FC<RepositoryFileGraphPanelProps> = ({
  data,
  repository = null,
  readMasked,
  onClose,
  className = '',
}) => {
  const tree = useMemo(() => buildFileTree(data.files), [data.files]);
  const [view, setView] = useState<ViewMode>('tree');
  const [query, setQuery] = useState('');
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set(folderIdsForPath(data.files[0]?.path ?? '')),
  );
  const [selectedPath, setSelectedPath] = useState<string | null>(data.files[0]?.path ?? null);
  const [masked, setMasked] = useState<Record<string, MaskedFragmentEntry>>({});
  const requested = useRef<Set<string>>(new Set());

  const filteredTree = useMemo(() => filterFileTree(tree, query), [tree, query]);
  const searching = Boolean(query.trim());
  const effectiveExpanded = useMemo(
    () => (searching ? new Set(collectFolderIds(filteredTree)) : expanded),
    [searching, filteredTree, expanded],
  );
  const selectedFile = useMemo(
    () => data.files.find((file) => file.path === selectedPath) ?? data.files[0] ?? null,
    [data.files, selectedPath],
  );

  const totalFindings = useMemo(
    () => data.files.reduce((sum, file) => sum + file.findings.length, 0),
    [data.files],
  );
  const maskedMap = useMemo(() => new Map(Object.entries(masked)), [masked]);

  const selectPath = useCallback((path: string) => {
    setSelectedPath(path);
    setExpanded((prev) => {
      const next = new Set(prev);
      for (const id of folderIdsForPath(path)) next.add(id);
      return next;
    });
  }, []);

  const requestOne = useCallback(
    (request: MaskedReadRequest) => {
      if (!readMasked) {
        setMasked((prev) => ({
          ...prev,
          [request.evidenceId]: {
            loading: false,
            result: {
              status: 'unavailable',
              reason: 'Чтение masked-кода не подключено к этому экрану.',
            },
          },
        }));
        return;
      }
      setMasked((prev) => ({
        ...prev,
        [request.evidenceId]: { loading: true, result: prev[request.evidenceId]?.result ?? null },
      }));
      readMasked(request)
        .then((result) => {
          setMasked((prev) => ({
            ...prev,
            [request.evidenceId]: { loading: false, result },
          }));
        })
        .catch(() => {
          setMasked((prev) => ({
            ...prev,
            [request.evidenceId]: {
              loading: false,
              result: {
                status: 'unavailable',
                reason: 'Сервер не отдал сохранённый masked-фрагмент.',
              },
            },
          }));
        });
    },
    [readMasked],
  );

  useEffect(() => {
    if (!selectedFile) return;
    for (const evidence of selectedFile.evidence.slice(0, EVIDENCE_PREVIEW_LIMIT)) {
      if (requested.current.has(evidence.id)) continue;
      requested.current.add(evidence.id);
      requestOne({
        evidenceId: evidence.id,
        path: selectedFile.path,
        line: evidence.line,
        endLine: evidence.endLine,
      });
    }
  }, [requestOne, selectedFile]);

  const toggleFolder = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const expandAll = () => setExpanded(new Set(collectFolderIds(tree)));
  const collapseAll = () => setExpanded(new Set());

  const visibleFiles = countFiles(filteredTree);
  const coverageWarning =
    data.coverage.status !== 'complete' ||
    (data.coverage.filesTotal > 0 && data.files.length < data.coverage.filesTotal);
  const coverageText =
    data.coverage.filesTotal > 0
      ? `${data.files.length} из ${data.coverage.filesTotal} файлов снимка`
      : `${data.files.length} файлов с фактами`;

  const repoLabel = repository
    ? `${providerLabel(repository.provider)} · ${repositoryShortName(repository.url)}`
    : 'Файлы проверки';

  return (
    <section
      className={`flex h-full min-h-0 w-full flex-col overflow-hidden bg-slate-950 text-slate-100 ${className}`}
      aria-label="Файловый граф проверки"
    >
      <header className="shrink-0 border-b border-slate-800 bg-slate-900/60">
        <div className="flex flex-wrap items-start gap-2 px-3 py-2.5 sm:px-4">
          <div className="flex min-w-0 flex-1 items-start gap-2.5">
            <div className="mt-0.5 rounded-lg border border-indigo-900/70 bg-indigo-950/50 p-1.5 text-indigo-300">
              <Files size={16} />
            </div>
            <div className="min-w-0">
              <h2 className="truncate text-sm font-semibold text-slate-100">Файловый граф</h2>
              <p className="truncate text-[11px] text-slate-400" title={repository?.url}>
                {repoLabel}
                {repository ? (
                  <>
                    {' · '}ветка{' '}
                    <span className="font-mono text-slate-300">{repository.branch || '—'}</span>
                    {' · '}коммит{' '}
                    <span
                      className="font-mono text-indigo-300"
                      title={repository.commit_sha}
                    >
                      {shortCommit(repository.commit_sha)}
                    </span>
                  </>
                ) : null}
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
            <span className="rounded border border-slate-800 bg-slate-950 px-1.5 py-0.5 text-slate-400">
              файлов: {data.files.length}
            </span>
            <span className="rounded border border-slate-800 bg-slate-950 px-1.5 py-0.5 text-slate-400">
              находок: {totalFindings}
            </span>
            <span
              className={`rounded border px-1.5 py-0.5 ${
                data.links.length
                  ? 'border-indigo-900/70 bg-indigo-950/40 text-indigo-200'
                  : 'border-slate-800 bg-slate-950 text-slate-400'
              }`}
              title="Только связи с доказательствами"
            >
              связей: {data.links.length}
            </span>
            <span className="rounded border border-slate-800 bg-slate-950 px-1.5 py-0.5 text-slate-400">
              {coverageText}
            </span>
            {onClose ? (
              <button
                type="button"
                onClick={onClose}
                aria-label="Закрыть файловый граф"
                className="ml-1 rounded-lg border border-slate-700 bg-slate-900 p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-100"
              >
                <X size={14} />
              </button>
            ) : null}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 px-3 pb-2.5 sm:px-4">
          <div
            className="flex shrink-0 items-center rounded-lg border border-slate-800 bg-slate-950 p-0.5"
            role="tablist"
            aria-label="Режим файлового графа"
          >
            <button
              type="button"
              role="tab"
              aria-selected={view === 'tree'}
              onClick={() => setView('tree')}
              className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[11px] font-medium ${
                view === 'tree'
                  ? 'bg-indigo-600 text-white'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <ListTree size={13} /> Дерево
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={view === 'graph'}
              onClick={() => setView('graph')}
              className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[11px] font-medium ${
                view === 'graph'
                  ? 'bg-indigo-600 text-white'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <Network size={13} /> Граф
            </button>
          </div>

          <label className="relative min-w-[160px] flex-1 sm:max-w-[360px]">
            <Search size={13} className="pointer-events-none absolute left-2.5 top-2 text-slate-500" />
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Escape') setQuery('');
              }}
              placeholder="Поиск по пути или правилу…"
              aria-label="Поиск файла по пути или правилу"
              className="w-full rounded-lg border border-slate-700 bg-slate-950 py-1.5 pl-8 pr-8 text-[11px] text-slate-200 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none"
            />
            {query ? (
              <button
                type="button"
                onClick={() => setQuery('')}
                aria-label="Очистить поиск"
                className="absolute right-1.5 top-1.5 rounded p-0.5 text-slate-500 hover:bg-slate-800 hover:text-slate-200"
              >
                <X size={12} />
              </button>
            ) : null}
          </label>

          {view === 'tree' ? (
            <div className="flex shrink-0 items-center gap-1">
              <button
                type="button"
                onClick={expandAll}
                className="flex items-center gap-1 rounded border border-slate-800 bg-slate-950 px-1.5 py-1 text-[10px] text-slate-400 hover:bg-slate-800 hover:text-slate-200"
              >
                <ChevronDown size={11} /> Развернуть
              </button>
              <button
                type="button"
                onClick={collapseAll}
                className="flex items-center gap-1 rounded border border-slate-800 bg-slate-950 px-1.5 py-1 text-[10px] text-slate-400 hover:bg-slate-800 hover:text-slate-200"
              >
                <ChevronRight size={11} /> Свернуть
              </button>
            </div>
          ) : null}

          {searching ? (
            <span className="shrink-0 text-[10px] text-cyan-300">
              найдено: {visibleFiles}
            </span>
          ) : null}
        </div>

        {coverageWarning ? (
          <div className="border-t border-amber-900/50 bg-amber-950/20 px-3 py-1.5 text-[10px] leading-4 text-amber-200/90 sm:px-4">
            Показаны файлы, о которых есть факты в ответе снимка.
            {data.coverage.filesTotal > data.files.length
              ? ` Остальные ${data.coverage.filesTotal - data.files.length} файл(ов) снимка здесь не раскрыты.`
              : ''}
            {data.coverage.skippedScopes.length
              ? ` Не проверялись: ${data.coverage.skippedScopes.map((scope) => SCOPE_LABEL[scope] ?? scope).join(', ')}.`
              : ''}
          </div>
        ) : null}
      </header>

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden lg:flex-row">
        {!data.files.length ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 px-4 text-center text-slate-500">
            <FileSearch size={32} className="text-slate-600" />
            <p className="text-sm text-slate-400">В фактах нет файлов</p>
            <p className="max-w-md text-[11px] text-slate-600">
              Карта этого запуска не содержит evidence, находок или покрытия по файлам.
              Дерево не достраивается по догадкам.
            </p>
          </div>
        ) : view === 'tree' ? (
          <>
            <div className="flex h-[44%] min-h-[180px] shrink-0 min-w-0 flex-col overflow-hidden border-b border-slate-800 lg:h-full lg:min-h-0 lg:w-[360px] lg:border-b-0 lg:border-r">
              <div className="flex shrink-0 items-center justify-between px-3 py-2 text-[10px] text-slate-500">
                <span>Путь и иерархия</span>
                <span>{searching ? `${visibleFiles} совпадений` : `${data.files.length} файлов`}</span>
              </div>
              <div className="rg-scroll min-h-0 flex-1 overflow-y-auto px-2 pb-3">
                {filteredTree.length ? (
                  <FileTreeView
                    nodes={filteredTree}
                    expanded={effectiveExpanded}
                    selectedPath={selectedFile?.path ?? null}
                    onToggleFolder={toggleFolder}
                    onSelectFile={selectPath}
                  />
                ) : (
                  <div className="mx-1 mt-2 rounded-lg border border-dashed border-slate-800 p-3 text-[11px] text-slate-500">
                    Ничего не нашлось. Поиск идёт по полному пути и правилу находки.
                  </div>
                )}
              </div>
            </div>
            <div className="min-h-0 min-w-0 flex-1 overflow-hidden">
              {selectedFile ? (
                <FileDetailPanel
                  key={selectedFile.path}
                  file={selectedFile}
                  links={data.links}
                  masked={maskedMap}
                  onRequestMasked={requestOne}
                  onSelectFile={selectPath}
                />
              ) : null}
            </div>
          </>
        ) : (
          <>
            <div className="h-[55%] shrink-0 min-w-0 p-2.5 lg:h-full lg:min-h-0 lg:flex-1">
              <FileGraphView
                data={data}
                selectedPath={selectedFile?.path ?? null}
                query={query}
                onSelectFile={selectPath}
              />
            </div>
            <div className="min-h-0 min-w-0 flex-1 overflow-hidden border-t border-slate-800 lg:h-full lg:w-[390px] lg:flex-none lg:border-l lg:border-t-0">
              {selectedFile ? (
                <FileDetailPanel
                  key={selectedFile.path}
                  file={selectedFile}
                  links={data.links}
                  masked={maskedMap}
                  onRequestMasked={requestOne}
                  onSelectFile={selectPath}
                />
              ) : null}
            </div>
          </>
        )}
      </div>
    </section>
  );
};