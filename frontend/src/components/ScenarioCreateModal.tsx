import React from 'react';
import { GitFork, X } from 'lucide-react';

interface ScenarioCreateModalProps {
  isOpen: boolean;
  name: string;
  busy: boolean;
  error: string | null;
  onName: (value: string) => void;
  onClose: () => void;
  onCreate: () => void;
}

export const ScenarioCreateModal: React.FC<ScenarioCreateModalProps> = ({
  isOpen,
  name,
  busy,
  error,
  onName,
  onClose,
  onCreate,
}) => {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
      <form
        onSubmit={(event) => { event.preventDefault(); if (!busy && name.trim()) onCreate(); }}
        className="w-full max-w-md overflow-hidden rounded-2xl border border-slate-800 bg-slate-900 shadow-2xl"
      >
        <div className="flex items-center justify-between border-b border-slate-800 p-4">
          <div className="flex items-center gap-2">
            <GitFork size={17} className="text-indigo-400" />
            <h3 className="text-sm font-bold text-slate-100">Новый сценарий проверки</h3>
          </div>
          <button type="button" onClick={onClose} disabled={busy} className="text-slate-400 hover:text-slate-200 disabled:opacity-50" aria-label="Закрыть">
            <X size={16} />
          </button>
        </div>

        <div className="space-y-4 p-4 sm:p-5">
          <p className="rounded-xl border border-slate-800 bg-slate-950/70 p-3 text-[11px] leading-relaxed text-slate-400">
            Новый сценарий получит текущие блоки и связи. Дальше его можно менять независимо от исходного.
          </p>
          {error && (
            <div role="alert" className="rounded-xl border border-rose-800 bg-rose-950/70 p-3 text-xs text-rose-200">
              {error}
            </div>
          )}
          <label className="block">
            <span className="mb-1 block text-[11px] text-slate-400">Название сценария</span>
            <input
              type="text"
              value={name}
              onChange={(event) => onName(event.target.value)}
              maxLength={120}
              disabled={busy}
              autoFocus
              placeholder="Например: Проверка моего сервиса"
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-indigo-500 focus:outline-none disabled:opacity-60"
            />
          </label>
          <button
            type="submit"
            disabled={busy || !name.trim()}
            className="w-full rounded-lg bg-indigo-600 px-4 py-2.5 text-xs font-semibold text-white hover:bg-indigo-500 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
          >
            {busy ? 'Создаём…' : 'Создать сценарий'}
          </button>
        </div>
      </form>
    </div>
  );
};