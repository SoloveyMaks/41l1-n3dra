import React, { useState } from 'react';
import { KeyRound, ShieldCheck } from 'lucide-react';

interface AccessGateProps {
  busy: boolean;
  error?: string | null;
  onSubmit: (accessCode: string) => void;
}

export const AccessGate: React.FC<AccessGateProps> = ({ busy, error, onSubmit }) => {
  const [code, setCode] = useState('');

  return (
    <div className="min-h-screen w-screen bg-slate-950 text-slate-100 flex items-center justify-center p-6">
      <form
        className="w-full max-w-sm rounded-2xl border border-slate-800 bg-slate-900/90 p-6 shadow-2xl shadow-indigo-950/30"
        onSubmit={(event) => {
          event.preventDefault();
          if (!busy && code.trim()) onSubmit(code.trim());
        }}
      >
        <div className="flex items-center gap-3 mb-5">
          <div className="p-2.5 rounded-xl bg-gradient-to-tr from-indigo-600 to-violet-500 text-white">
            <ShieldCheck size={22} />
          </div>
          <div>
            <h1 className="font-bold text-base text-white">AI Security Workflow</h1>
            <p className="text-xs text-slate-400">Доступ для жюри</p>
          </div>
        </div>

        <label className="block text-xs text-slate-300 mb-1.5" htmlFor="access-code">
          Код доступа
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
            placeholder="Введите код, выданный организатором"
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
          {busy ? 'Проверяем…' : 'Войти'}
        </button>
        <p className="mt-3 text-[11px] leading-relaxed text-slate-500">
          Это общий демонстрационный workspace: после входа данные стенда видны всем,
          у кого есть код доступа.
        </p>
      </form>
    </div>
  );
};
