import React, { useState } from 'react';
import { ArrowLeft, KeyRound, ShieldCheck } from 'lucide-react';
import { AccountCredentials, SessionRole } from '../types';

type GateMode = 'login' | 'register' | 'demo' | 'admin';

interface AccessGateProps {
  busy: boolean;
  error?: string | null;
  adminAvailable?: boolean;
  registrationInviteRequired?: boolean;
  onAccountSubmit: (mode: 'login' | 'register', input: AccountCredentials) => void;
  onCodeSubmit: (accessCode: string, role: SessionRole) => void;
}

export const AccessGate: React.FC<AccessGateProps> = ({
  busy,
  error,
  adminAvailable = true,
  registrationInviteRequired = false,
  onAccountSubmit,
  onCodeSubmit,
}) => {
  const [mode, setMode] = useState<GateMode>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [registrationCode, setRegistrationCode] = useState('');
  const [code, setCode] = useState('');

  const switchMode = (next: GateMode) => {
    setMode(next);
    setCode('');
    if (next !== 'login') setPassword('');
    if (next !== 'register') {
      setDisplayName('');
      setRegistrationCode('');
    }
  };

  const isAccountMode = mode === 'login' || mode === 'register';
  const isRegister = mode === 'register';
  const canSubmit = isAccountMode
    ? email.trim().length > 0 && password.length >= (isRegister ? 10 : 1)
    : code.trim().length > 0;

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (busy || !canSubmit) return;
    if (mode === 'login') {
      onAccountSubmit('login', { email: email.trim(), password });
      return;
    }
    if (mode === 'register') {
      onAccountSubmit('register', {
        email: email.trim(),
        password,
        displayName: displayName.trim() || undefined,
        registrationCode: registrationInviteRequired ? registrationCode.trim() : undefined,
      });
      return;
    }
    onCodeSubmit(code.trim(), mode === 'admin' ? 'admin' : 'user');
  };

  const title = mode === 'register'
    ? 'Создать аккаунт'
    : mode === 'demo'
      ? 'Demo-вход по коду'
      : mode === 'admin'
        ? 'Вход для администратора'
        : 'Вход в аккаунт';

  const subtitle = mode === 'register'
    ? 'Личный workspace и своя история проверок'
    : mode === 'demo'
      ? 'Общий демонстрационный workspace'
      : mode === 'admin'
        ? 'Отдельный код организатора'
        : 'Профиль, история и только свои проверки';

  return (
    <div className="min-h-screen w-screen bg-slate-950 text-slate-100 flex items-center justify-center p-6">
      <form
        className="w-full max-w-sm rounded-2xl border border-slate-800 bg-slate-900/90 p-6 shadow-2xl shadow-indigo-950/30"
        onSubmit={submit}
      >
        <div className="flex items-center gap-3 mb-5">
          <div className="p-2.5 rounded-xl bg-gradient-to-tr from-indigo-600 to-violet-500 text-white">
            <ShieldCheck size={22} />
          </div>
          <div>
            <h1 className="font-bold text-base text-white">Проверка безопасности кода</h1>
            <p className="text-xs text-slate-400">{subtitle}</p>
          </div>
        </div>

        {isAccountMode && (
          <div className="mb-4 grid grid-cols-2 gap-1 rounded-lg border border-slate-800 bg-slate-950/60 p-0.5">
            <button
              type="button"
              onClick={() => switchMode('login')}
              aria-pressed={mode === 'login'}
              className={`rounded-md px-2 py-1.5 text-xs font-medium ${
                mode === 'login' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              Вход
            </button>
            <button
              type="button"
              onClick={() => switchMode('register')}
              aria-pressed={mode === 'register'}
              className={`rounded-md px-2 py-1.5 text-xs font-medium ${
                mode === 'register' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              Регистрация
            </button>
          </div>
        )}

        <h2 className="mb-3 text-sm font-semibold text-slate-200">{title}</h2>

        {isAccountMode ? (
          <div className="space-y-3">
            {isRegister && (
              <label className="block">
                <span className="mb-1 block text-xs text-slate-300">Имя</span>
                <input
                  type="text"
                  value={displayName}
                  onChange={(event) => setDisplayName(event.target.value)}
                  disabled={busy}
                  autoComplete="name"
                  placeholder="Как к вам обращаться"
                  className="w-full rounded-lg bg-slate-950 border border-slate-700 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 px-3 py-2 text-sm outline-none disabled:opacity-60"
                />
              </label>
            )}
            <label className="block">
              <span className="mb-1 block text-xs text-slate-300">Email</span>
              <input
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                disabled={busy}
                autoComplete="email"
                placeholder="you@example.com"
                className="w-full rounded-lg bg-slate-950 border border-slate-700 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 px-3 py-2 text-sm outline-none disabled:opacity-60"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs text-slate-300">Пароль</span>
              <input
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                disabled={busy}
                autoComplete={isRegister ? 'new-password' : 'current-password'}
                placeholder={isRegister ? 'Минимум 10 символов' : 'Пароль'}
                className="w-full rounded-lg bg-slate-950 border border-slate-700 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 px-3 py-2 text-sm outline-none disabled:opacity-60"
              />
            </label>
            {isRegister && registrationInviteRequired && (
              <label className="block">
                <span className="mb-1 block text-xs text-slate-300">Код приглашения</span>
                <input
                  type="password"
                  value={registrationCode}
                  onChange={(event) => setRegistrationCode(event.target.value)}
                  disabled={busy}
                  autoComplete="off"
                  placeholder="Код от организатора"
                  className="w-full rounded-lg bg-slate-950 border border-slate-700 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 px-3 py-2 text-sm outline-none disabled:opacity-60"
                />
              </label>
            )}
          </div>
        ) : (
          <div className="space-y-3">
            <label className="block">
              <span className="mb-1 block text-xs text-slate-300">
                {mode === 'admin' ? 'Код администратора' : 'Код доступа'}
              </span>
              <div className="relative">
                <KeyRound className="absolute left-3 top-2.5 text-slate-500" size={16} />
                <input
                  type="password"
                  value={code}
                  onChange={(event) => setCode(event.target.value)}
                  disabled={busy}
                  autoFocus
                  autoComplete="off"
                  spellCheck={false}
                  placeholder={mode === 'admin' ? 'Код администратора' : 'Код, выданный организатором'}
                  className="w-full rounded-lg bg-slate-950 border border-slate-700 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 pl-9 pr-3 py-2 text-sm outline-none disabled:opacity-60"
                />
              </div>
            </label>
          </div>
        )}

        {error && (
          <p role="alert" className="mt-3 text-xs text-rose-300 bg-rose-950/50 border border-rose-900 rounded-lg px-3 py-2">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={busy || !canSubmit}
          className="mt-4 w-full rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:bg-slate-700 disabled:text-slate-400 px-3 py-2 text-sm font-semibold transition"
        >
          {busy
            ? 'Проверяем…'
            : mode === 'register'
              ? 'Создать аккаунт'
              : mode === 'demo'
                ? 'Войти в demo'
                : mode === 'admin'
                  ? 'Войти в админку'
                  : 'Войти'}
        </button>

        <div className="mt-3 space-y-2 text-[11px] text-slate-500">
          {isAccountMode ? (
            <button
              type="button"
              onClick={() => switchMode('demo')}
              className="block w-full text-left underline decoration-slate-700 underline-offset-2 hover:text-slate-300"
            >
              Demo-вход по коду организатора
            </button>
          ) : (
            <button
              type="button"
              onClick={() => switchMode('login')}
              className="flex items-center gap-1.5 underline decoration-slate-700 underline-offset-2 hover:text-slate-300"
            >
              <ArrowLeft size={12} /> Вернуться к аккаунту
            </button>
          )}
          {mode !== 'admin' && adminAvailable !== false && (
            <button
              type="button"
              onClick={() => switchMode('admin')}
              className="block text-left underline decoration-slate-700 underline-offset-2 hover:text-slate-300"
            >
              Вход для администратора
            </button>
          )}
        </div>

        <p className="mt-4 text-[11px] leading-relaxed text-slate-500">
          {isAccountMode
            ? 'Аккаунт получает личный workspace: проверки и история не смешиваются с другими пользователями.'
            : 'Demo-режим использует общий workspace и не выдаёт себя за личный профиль.'}
        </p>
      </form>
    </div>
  );
};