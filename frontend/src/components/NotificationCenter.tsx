import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertTriangle, Bell, BellRing, CheckCheck, Clock, Loader2, RefreshCw,
  Save, Settings, ShieldAlert, ShieldCheck, ShieldQuestion, X,
} from 'lucide-react';
import {
  NotificationApiError,
  NotificationInbox,
  NotificationItem,
  NotificationSettings,
  getNotificationSettings,
  getNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  saveNotificationSettings,
} from '../api/notifications';

const POLL_MS = 10_000;

const RUN_LABEL: Record<NotificationItem['execution_status'], string> = {
  succeeded: 'Завершена',
  failed: 'Ошибка',
  cancelled: 'Отменена',
  interrupted: 'Прервана',
};

const VERDICT_LABEL: Record<NotificationItem['security_verdict'], string> = {
  pass: 'Нет блокирующих',
  fail: 'Есть проблемы',
  inconclusive: 'Неполная',
  pending: 'Ожидает',
};

const DELIVERY_LABEL: Record<NotificationItem['delivery_status'], string> = {
  pending: 'Ожидает отправки',
  sending: 'Отправляется',
  delivered: 'Доставлено',
  failed: 'Не доставлено',
  skipped: 'Пропущено',
  waiting: 'Ожидает завершения',
};

const DELIVERY_CLASS: Record<NotificationItem['delivery_status'], string> = {
  pending: 'border-amber-800 bg-amber-950/50 text-amber-300',
  sending: 'border-blue-800 bg-blue-950/50 text-blue-300',
  delivered: 'border-emerald-800 bg-emerald-950/50 text-emerald-300',
  failed: 'border-rose-800 bg-rose-950/50 text-rose-300',
  skipped: 'border-slate-700 bg-slate-900 text-slate-400',
  waiting: 'border-slate-700 bg-slate-900 text-slate-400',
};

const runStatus = (item: NotificationItem) =>
  item.execution_status === 'succeeded'
    ? 'text-emerald-300'
    : item.execution_status === 'failed'
      ? 'text-rose-300'
      : 'text-amber-300';

const formatWhen = (value: string): string => {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '—';
  return parsed.toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  });
};

const shortRun = (runId: string) => (runId.length > 22 ? `${runId.slice(0, 22)}…` : runId);

const errorText = (error: unknown): string => {
  if (error instanceof NotificationApiError) {
    if (error.code === 'AUTH_REQUIRED') return 'Нужен вход в аккаунт.';
    if (error.status === 503 || error.status >= 500) {
      return `Сервис уведомлений временно недоступен (HTTP ${error.status}).`;
    }
    return error.message;
  }
  return error instanceof Error ? error.message : 'Не удалось загрузить уведомления';
};

interface NotificationCenterProps {
  onOpenRun?: (runId: string) => void;
}

export const NotificationCenter: React.FC<NotificationCenterProps> = ({ onOpenRun }) => {
  const [open, setOpen] = useState(false);
  const [inbox, setInbox] = useState<NotificationInbox>({ items: [], unread: 0 });
  const [loading, setLoading] = useState(true);
  const [feedError, setFeedError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyRun, setBusyRun] = useState<string | null>(null);

  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settings, setSettings] = useState<NotificationSettings>({
    enabled: false, webhook_url: '', webhook_configured: false,
    webhook_masked: '', webhook_ref: '', allow_local: false, allow_external: false,
  });
  const [settingsLoading, setSettingsLoading] = useState(false);
  const [settingsBusy, setSettingsBusy] = useState(false);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [settingsNotice, setSettingsNotice] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const inFlightRef = useRef(false);
  // Monotonic epoch of this mounted center context. Every async action captures
  // it and discards its result after unmount or a StrictMode effect replay, so
  // a late response cannot mutate state, refresh, or call the parent for a
  // previous session. A plain `mounted` ref would stay false after replay.
  const epochRef = useRef(0);
  const onOpenRunRef = useRef(onOpenRun);

  useEffect(() => {
    onOpenRunRef.current = onOpenRun;
  }, [onOpenRun]);

  const isCurrent = useCallback((epoch: number) => epochRef.current === epoch, []);

  const refresh = useCallback(async (surfaceError: boolean) => {
    const epoch = epochRef.current;
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    const controller = new AbortController();
    abortRef.current = controller;
    if (surfaceError) {
      setLoading(true);
      setFeedError(null);
    }
    try {
      const next = await getNotifications(controller.signal);
      if (!isCurrent(epoch)) return;
      setInbox(next);
      setFeedError(null);
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === 'AbortError') return;
      if (!isCurrent(epoch)) return;
      // Polling failures are visible too: never present an empty journal as
      // "nothing happened" while the server is unavailable.
      setFeedError(errorText(cause));
    } finally {
      if (isCurrent(epoch)) {
        inFlightRef.current = false;
        if (abortRef.current === controller) abortRef.current = null;
        setLoading(false);
      }
    }
  }, [isCurrent]);

  useEffect(() => {
    void refresh(false);
    const tick = () => {
      if (document.visibilityState === 'visible') void refresh(false);
    };
    const timer = window.setInterval(tick, POLL_MS);
    window.addEventListener('focus', tick);
    return () => {
      // Invalidate every pending callback before closing its request. Resetting
      // inFlight here also lets a StrictMode replay start a fresh load.
      epochRef.current += 1;
      inFlightRef.current = false;
      window.clearInterval(timer);
      window.removeEventListener('focus', tick);
      abortRef.current?.abort();
      abortRef.current = null;
    };
  }, [refresh]);

  useEffect(() => {
    if (!open || !settingsOpen) return;
    const epoch = epochRef.current;
    let cancelled = false;
    setSettingsLoading(true);
    setSettingsError(null);
    setSettingsNotice(null);
    getNotificationSettings()
      .then((value) => {
        if (!cancelled && isCurrent(epoch)) setSettings(value);
      })
      .catch((cause) => {
        if (!cancelled && isCurrent(epoch)) setSettingsError(errorText(cause));
      })
      .finally(() => {
        if (!cancelled && isCurrent(epoch)) setSettingsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, settingsOpen, isCurrent]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  const openItem = async (item: NotificationItem) => {
    const epoch = epochRef.current;
    const runId = item.run_id;
    if (!item.read_at) {
      setInbox((prev) => ({
        unread: Math.max(0, prev.unread - 1),
        items: prev.items.map((entry) =>
          entry.run_id === runId
            ? { ...entry, read_at: new Date().toISOString() }
            : entry,
        ),
      }));
      setBusyRun(runId);
      try {
        await markNotificationRead(runId);
      } catch {
        if (isCurrent(epoch)) void refresh(false);
      } finally {
        if (isCurrent(epoch)) setBusyRun(null);
      }
    }
    // Logout/unmount/new session bumps the epoch: a delayed mark-read must not
    // navigate the parent into the previous session's run.
    if (!isCurrent(epoch)) return;
    const openRun = onOpenRunRef.current;
    if (openRun) {
      openRun(runId);
      setOpen(false);
    }
  };

  const readAll = async () => {
    const epoch = epochRef.current;
    setBusyRun('*');
    setActionError(null);
    try {
      await markAllNotificationsRead();
      if (!isCurrent(epoch)) return;
      const now = new Date().toISOString();
      setInbox((prev) => ({
        unread: 0,
        items: prev.items.map((item) => ({ ...item, read_at: item.read_at ?? now })),
      }));
    } catch (cause) {
      if (!isCurrent(epoch)) return;
      setActionError(errorText(cause));
    } finally {
      if (isCurrent(epoch)) setBusyRun(null);
    }
  };

  const saveSettings = async () => {
    const epoch = epochRef.current;
    setSettingsBusy(true);
    setSettingsError(null);
    setSettingsNotice(null);
    const submittedUrl = settings.webhook_url.trim();
    const keepsStoredDestination = !submittedUrl && settings.webhook_configured;
    try {
      const saved = await saveNotificationSettings({
        enabled: settings.enabled,
        webhook_url: submittedUrl,
        allow_local: settings.allow_local,
        allow_external: settings.allow_external,
      });
      if (!isCurrent(epoch)) return;
      setSettings(saved);
      if (!saved.enabled) {
        setSettingsNotice('Внешняя доставка выключена. Уведомления остаются в журнале со статусом «Пропущено».');
      } else if (keepsStoredDestination) {
        setSettingsNotice('Настройки сохранены. Сохранённый адрес приёмника не изменён и скрыт.');
      } else {
        setSettingsNotice('Настройки сохранены. Новые завершённые запуски будут отправляться на приёмник.');
      }
      await refresh(true);
    } catch (cause) {
      if (!isCurrent(epoch)) return;
      setSettingsError(errorText(cause));
    } finally {
      if (isCurrent(epoch)) setSettingsBusy(false);
    }
  };

  const canSave = !settingsBusy && (
    !settings.enabled
    || settings.webhook_url.trim().length > 0
    || settings.webhook_configured
  );

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-label={inbox.unread > 0 ? `Уведомления: ${inbox.unread} новых` : 'Уведомления'}
        title="Уведомления о завершённых запусках"
        className="fixed bottom-4 right-3 sm:right-4 z-40 flex h-11 w-11 items-center justify-center rounded-full border border-slate-700 bg-slate-900/95 text-slate-200 shadow-xl shadow-slate-950/60 hover:bg-slate-800 hover:text-white"
      >
        {inbox.unread > 0 ? <BellRing size={19} /> : <Bell size={19} />}
        {inbox.unread > 0 && (
          <span className="absolute -right-1 -top-1 min-w-5 rounded-full border border-slate-950 bg-rose-600 px-1 text-[10px] font-bold leading-5 text-white">
            {inbox.unread > 99 ? '99+' : inbox.unread}
          </span>
        )}
      </button>

      {open && (
        <section
          role="dialog"
          aria-label="Центр уведомлений"
          className="fixed bottom-20 right-3 sm:right-4 z-40 flex max-h-[min(72vh,560px)] w-[calc(100vw-1.5rem)] max-w-[420px] flex-col overflow-hidden rounded-2xl border border-slate-700 bg-slate-950 shadow-2xl shadow-slate-950/80"
        >
          <header className="flex items-center gap-2 border-b border-slate-800 px-3 py-2.5">
            <div className="min-w-0">
              <div className="text-sm font-semibold text-slate-100">Уведомления</div>
              <div className="text-[10px] text-slate-500">
                {feedError
                  ? 'Данные могут быть устаревшими: сервис не ответил'
                  : 'Сохраняются на сервере и видны после перезагрузки страницы'}
              </div>
            </div>
            <div className="ml-auto flex items-center gap-1">
              <button
                type="button"
                onClick={() => void refresh(true)}
                disabled={loading}
                title="Обновить"
                className="rounded-md p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-100 disabled:opacity-50"
              >
                <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
              </button>
              <button
                type="button"
                onClick={() => setSettingsOpen((value) => !value)}
                title="Настройки доставки"
                className={`rounded-md p-1.5 hover:bg-slate-800 ${
                  settingsOpen ? 'bg-slate-800 text-indigo-300' : 'text-slate-400 hover:text-slate-100'
                }`}
              >
                <Settings size={14} />
              </button>
              <button
                type="button"
                onClick={() => setOpen(false)}
                title="Закрыть"
                className="rounded-md p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-100"
              >
                <X size={14} />
              </button>
            </div>
          </header>

          {settingsOpen ? (
            <div className="min-h-0 flex-1 overflow-y-auto p-3">
              <p className="mb-3 text-[11px] leading-relaxed text-slate-400">
                По умолчанию внешняя доставка выключена: наружу ничего не уходит, а завершённый
                запуск остаётся в журнале со статусом «Пропущено».
              </p>
              {settingsLoading ? (
                <div className="flex items-center gap-2 py-6 text-xs text-slate-400">
                  <Loader2 size={14} className="animate-spin" /> Загружаем настройки…
                </div>
              ) : (
                <div className="space-y-3">
                  <label className="flex items-start gap-2 rounded-xl border border-slate-800 bg-slate-900/60 p-2.5">
                    <input
                      type="checkbox"
                      checked={settings.enabled}
                      onChange={(event) => setSettings((prev) => ({ ...prev, enabled: event.target.checked }))}
                      className="mt-0.5 h-4 w-4 accent-emerald-500"
                    />
                    <span>
                      <span className="block text-xs font-medium text-slate-200">Отправлять уведомление о завершении</span>
                      <span className="block text-[10px] text-slate-500">
                        Это осознанный opt-in. Выключено — ни одного внешнего запроса.
                      </span>
                    </span>
                  </label>

                  <label className="block">
                    <span className="mb-1 block text-[11px] font-medium text-slate-300">URL приёмника</span>
                    <input
                      type="url"
                      value={settings.webhook_url}
                      onChange={(event) => setSettings((prev) => ({ ...prev, webhook_url: event.target.value }))}
                      placeholder={settings.webhook_masked || 'http://127.0.0.1:9099/notify или https://example.com/hook'}
                      autoComplete="off"
                      spellCheck={false}
                      className="w-full rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-xs text-slate-100 outline-none focus:border-indigo-600"
                    />
                    <span className="mt-1 block break-words text-[10px] text-slate-500">
                      {settings.webhook_configured ? (
                        <>
                          Сохранённый адрес скрыт: <span className="text-slate-400">{settings.webhook_masked}</span>.
                          Оставьте поле пустым, чтобы не менять его. Полный URL сервер не возвращает.
                        </>
                      ) : (
                        <>Query и fragment запрещены: токены в ссылке не поддерживаются.</>
                      )}
                    </span>
                  </label>

                  <label className="flex items-start gap-2 rounded-xl border border-slate-800 bg-slate-900/60 p-2.5">
                    <input
                      type="checkbox"
                      checked={settings.allow_local}
                      onChange={(event) => setSettings((prev) => ({ ...prev, allow_local: event.target.checked }))}
                      className="mt-0.5 h-4 w-4 accent-indigo-500"
                    />
                    <span>
                      <span className="block text-xs font-medium text-slate-200">Разрешить локальный приёмник</span>
                      <span className="block text-[10px] text-slate-500">Только loopback: 127.0.0.1 или ::1. Удобно для живой проверки.</span>
                    </span>
                  </label>

                  <label className="flex items-start gap-2 rounded-xl border border-slate-800 bg-slate-900/60 p-2.5">
                    <input
                      type="checkbox"
                      checked={settings.allow_external}
                      onChange={(event) => setSettings((prev) => ({ ...prev, allow_external: event.target.checked }))}
                      className="mt-0.5 h-4 w-4 accent-indigo-500"
                    />
                    <span>
                      <span className="block text-xs font-medium text-slate-200">Разрешить внешний приёмник</span>
                      <span className="block text-[10px] text-slate-500">Только https и порт 443; private/metadata адреса отклоняются.</span>
                    </span>
                  </label>

                  {settingsError && (
                    <div className="rounded-lg border border-rose-800 bg-rose-950/40 px-2.5 py-2 text-[11px] text-rose-300">
                      {settingsError}
                    </div>
                  )}
                  {settingsNotice && (
                    <div className="rounded-lg border border-emerald-800 bg-emerald-950/40 px-2.5 py-2 text-[11px] text-emerald-300">
                      {settingsNotice}
                    </div>
                  )}

                  <button
                    type="button"
                    onClick={() => void saveSettings()}
                    disabled={!canSave}
                    className="flex w-full items-center justify-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-500 disabled:bg-slate-800 disabled:text-slate-500"
                  >
                    {settingsBusy ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
                    {settingsBusy ? 'Сохраняем…' : 'Сохранить настройки'}
                  </button>
                  <p className="text-[10px] leading-relaxed text-slate-500">
                    Повтор при сетевой ошибке или HTTP 429/5xx — до трёх попыток. После этого
                    статус остаётся «Не доставлено», а не превращается в «Доставлено».
                  </p>
                </div>
              )}
            </div>
          ) : (
            <>
              <div className="flex items-center justify-between border-b border-slate-800 px-3 py-1.5">
                <span className="text-[10px] text-slate-500">
                  {feedError && inbox.items.length === 0
                    ? 'Журнал недоступен'
                    : inbox.items.length === 0
                      ? 'Журнал пуст'
                      : `Последние ${inbox.items.length}`}
                </span>
                <button
                  type="button"
                  onClick={() => void readAll()}
                  disabled={inbox.unread === 0 || busyRun !== null}
                  className="flex items-center gap-1 rounded px-1.5 py-1 text-[10px] text-slate-400 hover:bg-slate-800 hover:text-slate-100 disabled:opacity-40"
                >
                  <CheckCheck size={12} /> Прочитать все
                </button>
              </div>

              {feedError && (
                <div
                  role="status"
                  className="mx-3 mt-2 flex items-start gap-2 rounded-lg border border-amber-800 bg-amber-950/40 px-2.5 py-2 text-[11px] text-amber-200"
                >
                  <AlertTriangle size={13} className="mt-0.5 shrink-0" />
                  <div className="min-w-0 flex-1 break-words">
                    <div>{feedError}</div>
                    <div className="mt-0.5 text-[10px] text-amber-300/80">
                      {inbox.items.length > 0
                        ? 'Показаны последние успешно загруженные данные.'
                        : 'Журнал сейчас недоступен: пустой список не означает, что запусков не было.'}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => void refresh(true)}
                    disabled={loading}
                    className="shrink-0 rounded border border-amber-700 px-1.5 py-0.5 text-[10px] text-amber-100 hover:bg-amber-900/60 disabled:opacity-50"
                  >
                    Повторить
                  </button>
                </div>
              )}

              {actionError && (
                <div className="mx-3 mt-2 rounded-lg border border-rose-800 bg-rose-950/40 px-2.5 py-2 text-[11px] text-rose-300">
                  {actionError}
                </div>
              )}

              <div className="min-h-0 flex-1 overflow-y-auto p-2">
                {loading && inbox.items.length === 0 && (
                  <div className="flex items-center gap-2 px-2 py-6 text-xs text-slate-400">
                    <Loader2 size={14} className="animate-spin" /> Загружаем журнал…
                  </div>
                )}
                {!loading && inbox.items.length === 0 && feedError && (
                  <div className="px-3 py-8 text-center">
                    <AlertTriangle size={22} className="mx-auto mb-2 text-amber-500" />
                    <div className="text-xs text-amber-200">Журнал недоступен.</div>
                    <div className="mt-1 text-[10px] text-slate-500">
                      Сервер не вернул данные; показать пустой журнал как «запусков нет» нельзя.
                    </div>
                  </div>
                )}
                {!loading && inbox.items.length === 0 && !feedError && (
                  <div className="px-3 py-8 text-center">
                    <Clock size={22} className="mx-auto mb-2 text-slate-600" />
                    <div className="text-xs text-slate-400">Завершённых запусков пока нет.</div>
                    <div className="mt-1 text-[10px] text-slate-600">
                      Запустите проверку и закройте вкладку — уведомление появится здесь после
                      завершения.
                    </div>
                  </div>
                )}
                {inbox.items.map((item) => (
                  <button
                    key={item.run_id}
                    type="button"
                    onClick={() => void openItem(item)}
                    className={`mb-1.5 w-full rounded-xl border p-2.5 text-left transition ${
                      item.read_at
                        ? 'border-slate-800 bg-slate-900/40 hover:bg-slate-900'
                        : 'border-indigo-900/70 bg-indigo-950/20 hover:bg-indigo-950/35'
                    }`}
                  >
                    <div className="flex items-start gap-2">
                      <div className="mt-0.5 shrink-0">
                        {item.security_verdict === 'fail'
                          ? <ShieldAlert size={15} className="text-rose-400" />
                          : item.security_verdict === 'pass'
                            ? <ShieldCheck size={15} className="text-emerald-400" />
                            : <ShieldQuestion size={15} className="text-amber-400" />}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5">
                          <span className="truncate text-[11px] font-medium text-slate-200">
                            {item.workflow_name || item.workflow_id}
                          </span>
                          {!item.read_at && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-indigo-400" />}
                          {busyRun === item.run_id && <Loader2 size={11} className="animate-spin text-slate-500" />}
                        </div>
                        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px]">
                          <span className={runStatus(item)}>{RUN_LABEL[item.execution_status]}</span>
                          <span className="text-slate-600">·</span>
                          <span className="text-slate-400">{VERDICT_LABEL[item.security_verdict]}</span>
                          <span className="text-slate-600">·</span>
                          <span className="text-slate-400">{formatWhen(item.completed_at)}</span>
                        </div>
                        <div className="mt-1 flex flex-wrap items-center gap-1.5">
                          <span className={`rounded border px-1.5 py-0.5 text-[9px] font-medium ${DELIVERY_CLASS[item.delivery_status]}`}>
                            {DELIVERY_LABEL[item.delivery_status]}
                          </span>
                          {item.attempts > 0 && (
                            <span className="text-[9px] text-slate-500">
                              попыток: {item.attempts}
                              {item.status_code ? ` · HTTP ${item.status_code}` : ''}
                            </span>
                          )}
                          {item.total_findings > 0 && (
                            <span className="text-[9px] text-slate-500">
                              находок: {item.total_findings}
                              {item.critical_count > 0 ? ` · critical: ${item.critical_count}` : ''}
                            </span>
                          )}
                        </div>
                        {item.detail && (
                          <div className="mt-0.5 truncate text-[9px] text-slate-500" title={item.detail}>
                            {item.detail}
                          </div>
                        )}
                        <div className="mt-0.5 text-[9px] text-slate-600">запуск {shortRun(item.run_id)}</div>
                      </div>
                    </div>
                  </button>
                ))}
              </div>

              <footer className="flex items-center gap-1 border-t border-slate-800 px-3 py-1.5 text-[9px] text-slate-600">
                <AlertTriangle size={10} />
                Уведомление хранится на сервере, а не в памяти вкладки.
              </footer>
            </>
          )}
        </section>
      )}
    </>
  );
};