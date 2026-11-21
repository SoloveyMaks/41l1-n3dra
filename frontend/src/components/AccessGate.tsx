import React, { useState } from 'react';
import { ArrowLeft, KeyRound, ShieldCheck } from 'lucide-react';
import { SessionRole } from '../types';

interface AccessGateProps {
  busy: boolean;
  error?: string | null;
  adminAvailable?: boolean;
  onSubmit: (accessCode: string, role: SessionRole) => void;
}

export const AccessGate: React.FC<AccessGateProps> = ({
  busy,
  error,
  adminAvailable = true,
  onSubmit,
}) => {
  const [code, setCode] = useState('');
  const [adminMode, setAdminMode] = useState(false);

  const switchMode = (next: boolean) => {
    setAdminMode(next);
    setCode('');
  };

  return (
    <div className="min-h-screen w-screen bg-slate-950 text-slate-100 flex items-center justify-center p-6">
      <form
        className="w-full max-w-sm rounded-2xl border border-slate-800 bg-slate-900/90 p-6 shadow-2xl shadow-indigo-950/30"
        onSubmit={(event) => {
          event.preventDefault();
          if (!busy && code.trim()) onSubmit(code.trim(), adminMode ? 'admin' : 'user');
        }}
      >
        <div className="flex items-center gap-3 mb-5">
          <div className="p-2.5 rounded-xl bg-gradient-to-tr from-indigo-600 to-violet-500 text-white">
            <ShieldCheck size={22} />
          </div>
          <div>
            <h1 className="font-bold text-base text-white">Проверка безопасности кода</h1>
            <p className="text-xs text-slate-400">
              {adminMode ? 'Вход для администратора' : 'Демонстрационный стенд'}
            </p>
          </div>
        </div>

        <label className="block text-xs text-slate-300 mb-1.5" htmlFor="access-code">
          {adminMode ? 'Код администратора' : 'Код доступа'}
        </label>
        <div className="relative mb-3">
          <KeyRound className="absolute left-3 top-2.5 text-slate-500" size={16} />
          <input
            id="access-code"
            type="password"
            value={code}
            onChange={(event) => setCode(event.target.value)}
            disabled={busy}
            autoFocus
            autoComplete="off"
            spellCheck={false}
            placeholder={adminMode ? 'Код администратора' : 'Код, выданный организатором'}
            className="w-full rounded-lg bg-slate-950 border border-slate-700 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 pl-9 pr-3 py-2 text-sm outline-none disabled:opacity-60"
          />
        </div>

        {error && (
          <p role="alert" className="mb-3 text-xs text-rose-300 bg-rose-950/50 border border-rose-900 rounded-lg px-3 py-2">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={busy || !code.trim()}
          className="w-full rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:bg-slate-700 disabled:text-slate-400 px-3 py-2 text-sm font-semibold transition"
        >
          {busy ? 'Проверяем…' : adminMode ? 'Войти в админку' : 'Начать проверку'}
        </button>

        {adminMode ? (
          <button
            type="button"
            onClick={() => switchMode(false)}
            className="mt-3 flex w-full items-center justify-center gap-1.5 text-[11px] text-slate-400 hover:text-slate-200"
          >
            <ArrowLeft size={12} /> Вернуться к проверке проекта
          </button>
        ) : (
          <>
            <p className="mt-3 text-[11px] leading-relaxed text-slate-500">
              После входа вы увидите только проверку проекта и её результат. Доступ к
              настройкам сценария есть у администратора.
            </p>
            {adminAvailable !== false && (
              <button
                type="button"
                onClick={() => switchMode(true)}
                className="mt-3 text-[11px] text-slate-500 underline decoration-slate-700 underline-offset-2 hover:text-slate-300"
              >
                Вход для администратора
              </button>
            )}
          </>
        )}
      </form>
    </div>
  );
};