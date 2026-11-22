import React, { useEffect, useRef, useState } from 'react';
import {
  Upload, X, FileArchive, AlertCircle, Loader2, Sparkles, GitBranch, Lock, FolderArchive,
} from 'lucide-react';
import { ArtifactManifest } from '../types';
import { apiClient, ApiError } from '../api/client';
import { describeRepositoryError, parseRepositoryUrl, validateBranch } from '../repository';

interface UploadModalProps {
  isOpen: boolean;
  onClose: () => void;
  onArtifactLoaded: (manifest: ArtifactManifest) => void;
  showTraining?: boolean;
}

type UploadTab = 'zip' | 'repository';

const zipError = (error: unknown): string => {
  if (error instanceof ApiError) {
    if (error.status === 413) return 'Архив больше допустимого размера (50 МБ).';
    if (error.status === 400) return 'Файл не похож на корректный ZIP-архив.';
    if (error.status === 429) return 'Слишком много загрузок подряд. Подождите минуту.';
  }
  const message = error instanceof Error ? error.message : '';
  if (message && !/^HTTP \d+$/.test(message)) return message;
  return 'Не удалось загрузить ZIP. Проверьте файл и повторите.';
};

export const UploadModal: React.FC<UploadModalProps> = ({ isOpen, onClose, onArtifactLoaded, showTraining = true }) => {
  const [tab, setTab] = useState<UploadTab>('zip');
  const [isUploading, setIsUploading] = useState(false);
  const [zipProgress, setZipProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [repoUrl, setRepoUrl] = useState('');
  const [repoBranch, setRepoBranch] = useState('');

  const requestSeq = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  // Закрытие или повторный вход отменяют незавершённый запрос: ответ не должен
  // выбрать артефакт в уже закрытой форме или после смены вкладки.
  useEffect(() => {
    if (!isOpen) {
      requestSeq.current += 1;
      abortRef.current?.abort();
      abortRef.current = null;
      setIsUploading(false);
      setZipProgress(null);
      return;
    }
    setError(null);
  }, [isOpen]);

  useEffect(() => () => {
    requestSeq.current += 1;
    abortRef.current?.abort();
  }, []);

  if (!isOpen) return null;

  const beginRequest = (): { seq: number; signal: AbortSignal } => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const seq = ++requestSeq.current;
    setIsUploading(true);
    setError(null);
    return { seq, signal: controller.signal };
  };

  const finish = (seq: number, manifest: ArtifactManifest) => {
    if (seq !== requestSeq.current) return;
    abortRef.current = null;
    setIsUploading(false);
    setZipProgress(null);
    onArtifactLoaded(manifest);
    onClose();
  };

  const fail = (seq: number, message: string) => {
    if (seq !== requestSeq.current) return;
    abortRef.current = null;
    setIsUploading(false);
    setZipProgress(null);
    setError(message);
  };

  const handleFileUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const input = event.currentTarget;
    const file = input.files?.[0];
    if (!file) return;
    input.value = '';
    if (!file.name.toLowerCase().endsWith('.zip')) {
      setError('Выберите ZIP-архив проекта.');
      return;
    }
    if (file.size > 50 * 1024 * 1024) {
      setError('Архив больше 50 МБ — сервер его не примет.');
      return;
    }
    const { seq, signal } = beginRequest();
    setZipProgress(0);
    try {
      const manifest = await apiClient.uploadArtifactWithProgress(
        file,
        (percent) => {
          if (seq === requestSeq.current) setZipProgress(percent);
        },
        signal,
      );
      finish(seq, manifest);
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      fail(seq, zipError(err));
    }
  };

  const handleRepositoryImport = async (event: React.SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault();
    let repositoryUrl = '';
    let branch = '';
    try {
      repositoryUrl = parseRepositoryUrl(repoUrl).url;
      branch = validateBranch(repoBranch);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Проверьте ссылку на репозиторий');
      return;
    }
    const { seq, signal } = beginRequest();
    try {
      const manifest = await apiClient.importRepository(repositoryUrl, branch, signal);
      finish(seq, manifest);
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      fail(seq, describeRepositoryError(err));
    }
  };

  const handleLoadSample = async () => {
    const { seq } = beginRequest();
    try {
      finish(seq, await apiClient.loadSampleArtifact());
    } catch (err: unknown) {
      fail(seq, zipError(err));
    }
  };

  const handleLoadTraining = async () => {
    const { seq } = beginRequest();
    try {
      finish(seq, await apiClient.loadTrainingArtifact());
    } catch (err: unknown) {
      fail(seq, err instanceof Error ? err.message : 'Training-project не настроен на сервере');
    }
  };

  const close = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    requestSeq.current += 1;
    setIsUploading(false);
    setZipProgress(null);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-3 backdrop-blur-sm sm:p-4">
      <div className="max-h-[calc(100vh-1.5rem)] w-full max-w-lg overflow-y-auto rounded-2xl border border-slate-800 bg-slate-900 shadow-2xl">
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-slate-800 bg-slate-900 p-4">
          <div className="flex items-center gap-2">
            <Upload size={18} className="text-indigo-400" />
            <h3 className="text-sm font-bold text-slate-100">Добавить проект</h3>
          </div>
          <button onClick={close} className="text-slate-400 hover:text-slate-200" aria-label="Закрыть">
            <X size={16} />
          </button>
        </div>

        <div className="space-y-4 p-4 sm:p-6">
          <div className="grid grid-cols-2 gap-1 rounded-xl border border-slate-800 bg-slate-950/70 p-1" role="tablist" aria-label="Способ добавления проекта">
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'zip'}
              onClick={() => { setTab('zip'); setError(null); }}
              disabled={isUploading}
              className={`flex items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-xs font-semibold transition disabled:opacity-50 ${
                tab === 'zip' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
              }`}
            >
              <FolderArchive size={14} /> ZIP-архив
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'repository'}
              onClick={() => { setTab('repository'); setError(null); }}
              disabled={isUploading}
              className={`flex items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-xs font-semibold transition disabled:opacity-50 ${
                tab === 'repository' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
              }`}
            >
              <GitBranch size={14} /> Репозиторий
            </button>
          </div>

          {error && (
            <div role="alert" className="flex items-start gap-2 rounded-xl border border-rose-800 bg-rose-950/80 p-3 text-xs text-rose-200">
              <AlertCircle size={16} className="mt-0.5 shrink-0 text-rose-400" />
              <span className="break-words">{error}</span>
            </div>
          )}

          {tab === 'zip' && (
            <>
              <label className={`flex flex-col items-center justify-center rounded-xl border-2 border-dashed p-6 text-center transition sm:p-8 ${
                isUploading ? 'border-slate-800 bg-slate-950/40' : 'cursor-pointer border-slate-700 bg-slate-950/40 hover:border-indigo-500 hover:bg-slate-950/60'
              }`}>
                <FileArchive size={36} className="mb-2 text-slate-500" />
                <span className="text-xs font-semibold text-slate-200">
                  {isUploading ? 'Загружаем архив…' : 'Выберите ZIP-архив проекта'}
                </span>
                <span className="mt-1 text-[10px] text-slate-400">
                  До 50 МБ. Загрузка начнётся сразу после выбора файла.
                </span>
                <input
                  type="file"
                  accept=".zip,application/zip"
                  onChange={handleFileUpload}
                  disabled={isUploading}
                  className="hidden"
                />
              </label>

              {isUploading && (
                <div className="space-y-1">
                  <div className="flex justify-between text-[11px] text-slate-400">
                    <span>Загрузка архива</span>
                    <span>{zipProgress === null ? '—' : `${zipProgress}%`}</span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-slate-800">
                    <div
                      className="h-full rounded-full bg-indigo-500 transition-all"
                      style={{ width: `${zipProgress ?? 8}%` }}
                    />
                  </div>
                </div>
              )}

              <div className="relative flex items-center py-1">
                <div className="flex-grow border-t border-slate-800" />
                <span className="mx-3 flex-shrink text-[10px] uppercase tracking-wider text-slate-500">
                  или готовый эталон
                </span>
                <div className="flex-grow border-t border-slate-800" />
              </div>

              <button
                onClick={() => void handleLoadSample()}
                disabled={isUploading}
                className="flex w-full items-center justify-center gap-2 rounded-xl border border-indigo-700 bg-indigo-950/60 px-4 py-2.5 text-xs font-semibold text-indigo-300 shadow-md transition hover:bg-indigo-900/80 disabled:opacity-50"
              >
                {isUploading ? <Loader2 size={16} className="animate-spin text-indigo-400" /> : <Sparkles size={16} className="text-indigo-400" />}
                <span>Загрузить sample (Kotlin/Spring Boot)</span>
              </button>

              {showTraining && (
                <button
                  onClick={() => void handleLoadTraining()}
                  disabled={isUploading}
                  className="flex w-full items-center justify-center gap-2 rounded-xl border border-slate-700 bg-slate-900 px-4 py-2.5 text-xs font-semibold text-slate-300 transition hover:bg-slate-800 disabled:opacity-50"
                >
                  <Sparkles size={16} className="text-slate-400" />
                  <span>Загрузить organizer training-project (если настроен)</span>
                </button>
              )}
            </>
          )}

          {tab === 'repository' && (
            <form onSubmit={handleRepositoryImport} className="space-y-4">
              <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-3 text-[11px] leading-relaxed text-slate-400">
                Публичный репозиторий <span className="text-slate-200">GitHub</span> или{' '}
                <span className="text-slate-200">GitLab</span> по HTTPS. Скачаем архив и привяжем проверку
                к конкретному коммиту.
              </div>

              <label className="block">
                <span className="mb-1 block text-[11px] text-slate-400">Ссылка на репозиторий</span>
                <input
                  type="url"
                  inputMode="url"
                  required
                  value={repoUrl}
                  onChange={(event) => setRepoUrl(event.target.value)}
                  disabled={isUploading}
                  placeholder="https://github.com/owner/repo"
                  aria-label="Ссылка на репозиторий"
                  className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200 placeholder:text-slate-600 focus:border-indigo-500 focus:outline-none disabled:opacity-60"
                />
              </label>

              <label className="block">
                <span className="mb-1 block text-[11px] text-slate-400">
                  Ветка <span className="text-slate-500">(необязательно)</span>
                </span>
                <input
                  type="text"
                  value={repoBranch}
                  onChange={(event) => setRepoBranch(event.target.value)}
                  disabled={isUploading}
                  placeholder="пусто — ветка по умолчанию"
                  aria-label="Ветка репозитория"
                  className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-xs text-slate-200 placeholder:font-sans placeholder:text-slate-600 focus:border-indigo-500 focus:outline-none disabled:opacity-60"
                />
              </label>

              {isUploading && (
                <div className="flex items-center gap-2 rounded-xl border border-indigo-900 bg-indigo-950/40 p-3 text-[11px] text-indigo-200">
                  <Loader2 size={15} className="animate-spin" />
                  <span>Скачиваем архив репозитория и фиксируем коммит. Обычно это занимает до минуты.</span>
                </div>
              )}

              <button
                type="submit"
                disabled={isUploading || !repoUrl.trim()}
                className="flex w-full items-center justify-center gap-2 rounded-xl bg-indigo-600 px-4 py-2.5 text-xs font-semibold text-white transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
              >
                {isUploading ? <Loader2 size={16} className="animate-spin" /> : <GitBranch size={16} />}
                {isUploading ? 'Импортируем…' : 'Импортировать репозиторий'}
              </button>

              <p className="flex items-start gap-1.5 text-[10px] leading-relaxed text-slate-500">
                <Lock size={11} className="mt-0.5 shrink-0" />
                Приватные репозитории и токены пока не поддерживаются. Ссылки с логином или паролем отклоняются.
              </p>
            </form>
          )}
        </div>
      </div>
    </div>
  );
};
