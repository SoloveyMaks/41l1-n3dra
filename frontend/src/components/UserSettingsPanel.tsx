import React, { useEffect, useMemo, useState } from 'react';
import {
  AlertCircle, CheckCircle2, Clock, ExternalLink, GitBranch, Loader2,
  RefreshCw, Send, Settings,
} from 'lucide-react';
import { apiClient, ApiError } from '../api/client';
import {
  RepositoryWatch, SessionState, TelegramBinding, TelegramDeliveryMode,
  TelegramMe, TelegramScenario, TelegramSettings,
} from '../types';
import { ScheduleModal } from './ScheduleModal';
import { providerLabel, repositoryShortName } from '../repository';

interface UserSettingsPanelProps {
  session: SessionState | null;
  workflowId: string;
  artifactId: string;
  onOpenRun?: (runId: string) => void;
}

interface ScheduleRow {
  id: string;
  workflow_id: string;
  artifact_id: string;
  cron_or_interval: string;
  enabled?: boolean | number | string;
  next_fire_at?: string | null;
  last_run_at?: string | null;
}

type Notice = { kind: 'ok' | 'error'; text: string };

const DELIVERY_LABEL: Record<TelegramDeliveryMode, string> = {
  alerts: 'Только алерты о проверках',
  jsonc: 'Только JSONC-отчёт',
  both: 'Алерты и JSONC-отчёт',
};

const isEnabled = (row: { enabled?: boolean | number | string }): boolean =>
  row.enabled === true || row.enabled === 1 || row.enabled === '1' || row.enabled === 'true';

const cadenceLabel = (value: string): string => {
  const match = /^interval:(\d+)$/.exec(value.trim());
  if (!match) return `cron ${value.trim()}`;
  const seconds = Number(match[1]);
  if (seconds % 60 === 0) return `каждые ${seconds / 60} мин`;
  return `каждые ${seconds} сек`;
};

const formatTime = (value?: string | null): string => {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? '—'
    : parsed.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
};

const telegramErrorText = (error: unknown): string => {
  if (error instanceof ApiError) {
    if (error.code === 'AUTH_REQUIRED') return 'Telegram доступен только после входа в аккаунт.';
    if (error.code === 'TELEGRAM_NOT_BOUND') return 'Сначала привяжите Telegram, потом сохраняйте настройки.';
    if (error.code === 'SCENARIO_NOT_FOUND') return 'Этот сценарий не найден в вашем workspace.';
    if (error.code === 'INVALID_TELEGRAM_SETTINGS') return error.message;
    return error.message;
  }
  return error instanceof Error ? error.message : 'Не удалось выполнить действие Telegram';
};

const CopyButton: React.FC<{ value: string; onCopied: (ok: boolean) => void }> = ({ value, onCopied }) => (
  <button
    type="button"
    onClick={async () => {
      try {
        await navigator.clipboard.writeText(value);
        onCopied(true);
      } catch {
        onCopied(false);
      }
    }}
    className="rounded border border-slate-700 bg-slate-800 px-2 py-1 text-[10px] text-slate-300 hover:bg-slate-700"
  >
    Копировать
  </button>
);

export const UserSettingsPanel: React.FC<UserSettingsPanelProps> = ({
  session,
  workflowId,
  artifactId,
  onOpenRun,
}) => {
  const accountMode = session?.mode === 'account';

  const [telegramMe, setTelegramMe] = useState<TelegramMe | null>(null);
  const [telegramLoading, setTelegramLoading] = useState(true);
  const [telegramError, setTelegramError] = useState<string | null>(null);
  const [binding, setBinding] = useState<TelegramBinding | null>(null);
  const [bindingBusy, setBindingBusy] = useState(false);
  const [unbinding, setUnbinding] = useState(false);
  const [confirmUnbind, setConfirmUnbind] = useState(false);
  const [settings, setSettings] = useState<TelegramSettings>({ deliveryMode: 'alerts', scenario: null });
  const [settingsBusy, setSettingsBusy] = useState(false);
  const [scenarios, setScenarios] = useState<TelegramScenario[]>([]);
  const [scenariosError, setScenariosError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);

  const [schedules, setSchedules] = useState<ScheduleRow[]>([]);
  const [watches, setWatches] = useState<RepositoryWatch[]>([]);
  const [automationLoading, setAutomationLoading] = useState(true);
  const [scheduleError, setScheduleError] = useState<string | null>(null);
  const [watchError, setWatchError] = useState<string | null>(null);
  const [automationOpen, setAutomationOpen] = useState(false);

  const refreshTelegram = async () => {
    if (!accountMode) {
      setTelegramLoading(false);
      setTelegramError('Telegram-привязка доступна только личному аккаунту. Demo-сессия не является профилем.');
      return;
    }
    setTelegramLoading(true);
    setTelegramError(null);
    setScenariosError(null);
    const [meResult, scenarioResult] = await Promise.allSettled([
      apiClient.getTelegramMe(),
      apiClient.listTelegramScenarios(),
    ]);
    if (meResult.status === 'fulfilled') {
      setTelegramMe(meResult.value);
      if (meResult.value.settings) setSettings(meResult.value.settings);
    } else {
      setTelegramMe(null);
      setTelegramError(telegramErrorText(meResult.reason));
    }
    if (scenarioResult.status === 'fulfilled') {
      setScenarios(scenarioResult.value.items);
    } else {
      setScenarios([]);
      setScenariosError(telegramErrorText(scenarioResult.reason));
    }
    setTelegramLoading(false);
  };

  const refreshAutomation = async () => {
    if (!accountMode) {
      setAutomationLoading(false);
      return;
    }
    setAutomationLoading(true);
    setScheduleError(null);
    setWatchError(null);
    const [scheduleResult, watchResult] = await Promise.allSettled([
      apiClient.listSchedules(),
      apiClient.listRepositoryWatches(),
    ]);
    if (scheduleResult.status === 'fulfilled') setSchedules(scheduleResult.value as ScheduleRow[]);
    else setScheduleError(telegramErrorText(scheduleResult.reason));
    if (watchResult.status === 'fulfilled') setWatches(watchResult.value);
    else setWatchError(telegramErrorText(watchResult.reason));
    setAutomationLoading(false);
  };

  useEffect(() => {
    void refreshTelegram();
    void refreshAutomation();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accountMode]);

  const showNotice = (kind: Notice['kind'], text: string) => {
    setNotice({ kind, text });
    window.setTimeout(() => setNotice(null), 6000);
  };

  const createBinding = async () => {
    setBindingBusy(true);
    setTelegramError(null);
    setConfirmUnbind(false);
    try {
      const issued = await apiClient.createTelegramBinding();
      setBinding(issued);
      showNotice('ok', 'Код привязки создан. Он одноразовый и действует 10 минут.');
    } catch (error: unknown) {
      setTelegramError(telegramErrorText(error));
    } finally {
      setBindingBusy(false);
    }
  };

  const saveSettings = async () => {
    setSettingsBusy(true);
    setTelegramError(null);
    try {
      const saved = await apiClient.updateTelegramSettings(settings);
      setSettings(saved);
      setTelegramMe((previous) => previous ? { ...previous, settings: saved } : previous);
      showNotice('ok', 'Настройки Telegram сохранены для вашего аккаунта.');
    } catch (error: unknown) {
      setTelegramError(telegramErrorText(error));
    } finally {
      setSettingsBusy(false);
    }
  };

  const unbind = async () => {
    if (!confirmUnbind) {
      setConfirmUnbind(true);
      return;
    }
    setUnbinding(true);
    setTelegramError(null);
    try {
      await apiClient.unbindTelegram();
      setTelegramMe({ bound: false, settings: null, botUsername: telegramMe?.botUsername, externalStatus: telegramMe?.externalStatus });
      setBinding(null);
      setConfirmUnbind(false);
      showNotice('ok', 'Telegram отвязан от этого аккаунта.');
    } catch (error: unknown) {
      setTelegramError(telegramErrorText(error));
    } finally {
      setUnbinding(false);
    }
  };

  const botUsername = telegramMe?.botUsername || 'chilli_nedra_bot';
  const botStatus = telegramMe?.externalStatus;
  const bound = Boolean(telegramMe?.bound);
  const automationTotal = schedules.length + watches.length;

  const botStatusText = useMemo(() => {
    if (botStatus === 'ready') return 'Bot API готов';
    if (botStatus === 'blocked') return 'Bot API недоступен: привязка не подтверждена сервером';
    return 'Статус Bot API не подтверждён';
  }, [botStatus]);

  return (
    <div className="flex-1 h-full overflow-y-auto bg-slate-950 p-3 sm:p-4">
      <div className="mx-auto max-w-5xl space-y-4">
        <div>
          <h1 className="text-sm font-bold text-slate-100">Настройки проекта и проверки</h1>
          <p className="mt-0.5 text-xs text-slate-500">
            Личный Telegram и собственные автозапуски. Это не админ-панель: данные ограничены вашим workspace.
          </p>
        </div>

        {notice && (
          <div
            role={notice.kind === 'error' ? 'alert' : 'status'}
            className={`rounded-xl border px-3 py-2 text-xs ${
              notice.kind === 'error'
                ? 'border-rose-800 bg-rose-950/60 text-rose-200'
                : 'border-emerald-800 bg-emerald-950/60 text-emerald-200'
            }`}
          >
            {notice.text}
          </div>
        )}

        <section className="rounded-xl border border-slate-800 bg-slate-900/70 overflow-hidden">
          <div className="flex flex-wrap items-start justify-between gap-2 border-b border-slate-800 p-3.5">
            <div className="flex items-start gap-2">
              <div className="rounded-lg border border-sky-800 bg-sky-950/60 p-1.5 text-sky-300">
                <Send size={16} />
              </div>
              <div>
                <h2 className="text-xs font-bold uppercase tracking-wider text-slate-300">Telegram</h2>
                <p className="mt-0.5 text-[11px] text-slate-500">
                  Бот <span className="font-mono text-sky-300">@{botUsername}</span> · {botStatusText}
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => void refreshTelegram()}
              disabled={telegramLoading}
              className="flex items-center gap-1 rounded-lg border border-slate-700 bg-slate-800 px-2 py-1 text-[10px] text-slate-300 hover:bg-slate-700 disabled:opacity-50"
            >
              <RefreshCw size={11} className={telegramLoading ? 'animate-spin' : ''} /> Обновить
            </button>
          </div>

          <div className="p-3.5 space-y-3">
            {telegramLoading && (
              <div className="flex items-center gap-2 text-xs text-slate-400">
                <Loader2 size={14} className="animate-spin" /> Проверяем привязку…
              </div>
            )}

            {!telegramLoading && telegramError && (
              <div className="flex items-start gap-2 rounded-lg border border-amber-900 bg-amber-950/40 px-2.5 py-2 text-[11px] text-amber-200">
                <AlertCircle size={13} className="mt-0.5 shrink-0" /> {telegramError}
              </div>
            )}

            {!telegramLoading && !accountMode && (
              <p className="rounded-lg border border-dashed border-slate-800 px-3 py-4 text-center text-xs text-slate-500">
                Demo-сессия не является личным аккаунтом. Войдите или зарегистрируйтесь, чтобы привязать Telegram.
              </p>
            )}

            {!telegramLoading && accountMode && telegramMe && !bound && (
              <div className="space-y-3">
                <p className="text-[11px] leading-relaxed text-slate-400">
                  Привязка идёт через приватный чат с ботом. Код одноразовый, действует 10 минут; токен бота в браузер не попадает.
                </p>
                {botStatus === 'blocked' && (
                  <div className="rounded-lg border border-amber-900 bg-amber-950/40 px-2.5 py-2 text-[11px] text-amber-200">
                    Сервер сейчас не может достучаться до Bot API. Код можно выпустить, но привязка не подтвердится до восстановления Telegram-прокси.
                  </div>
                )}
                {!binding ? (
                  <button
                    type="button"
                    onClick={() => void createBinding()}
                    disabled={bindingBusy}
                    className="flex items-center gap-1.5 rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-sky-500 disabled:bg-slate-700 disabled:text-slate-400"
                  >
                    {bindingBusy ? <Loader2 size={13} className="animate-spin" /> : <Send size={13} />}
                    {bindingBusy ? 'Создаём код…' : 'Получить код привязки'}
                  </button>
                ) : (
                  <div className="rounded-xl border border-sky-900 bg-sky-950/30 p-3 space-y-2">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div>
                        <div className="text-[10px] uppercase tracking-wider text-slate-500">Одноразовый код</div>
                        <div className="font-mono text-lg font-bold tracking-wider text-sky-200">{binding.code}</div>
                      </div>
                      <CopyButton
                        value={binding.code}
                        onCopied={(ok) => showNotice(ok ? 'ok' : 'error', ok ? 'Код скопирован.' : 'Не удалось скопировать код.')}
                      />
                    </div>
                    <p className="text-[11px] text-slate-400">
                      Откройте Telegram, найдите <span className="font-mono text-sky-300">@{botUsername}</span> и отправьте{' '}
                      <span className="font-mono text-sky-300">/start {binding.code}</span>.
                      {binding.expiresAt ? ` Код действует до ${formatTime(binding.expiresAt)}.` : ' Код действует 10 минут.'}
                    </p>
                    {binding.deepLink && (
                      <a
                        href={binding.deepLink}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 text-[11px] text-sky-300 underline decoration-sky-800 underline-offset-2 hover:text-sky-200"
                      >
                        <ExternalLink size={11} /> Открыть @{botUsername} с кодом
                      </a>
                    )}
                    <button
                      type="button"
                      onClick={() => void refreshTelegram()}
                      className="text-[10px] text-slate-400 underline decoration-slate-700 underline-offset-2 hover:text-slate-200"
                    >
                      Я отправил код — проверить привязку
                    </button>
                  </div>
                )}
              </div>
            )}

            {!telegramLoading && accountMode && telegramMe && bound && (
              <div className="space-y-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="flex items-center gap-1 rounded border border-emerald-800 bg-emerald-950/60 px-2 py-0.5 text-[10px] text-emerald-300">
                    <CheckCircle2 size={11} /> Привязан
                  </span>
                  <span className="text-[11px] text-slate-400">
                    {telegramMe.telegramUsername ? `@${telegramMe.telegramUsername}` : 'приватный чат'}
                    {telegramMe.createdAt ? ` · с ${formatTime(telegramMe.createdAt)}` : ''}
                  </span>
                </div>

                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <label className="block">
                    <span className="mb-1 block text-[10px] uppercase tracking-wider text-slate-500">Что доставлять</span>
                    <select
                      value={settings.deliveryMode}
                      onChange={(event) => setSettings((prev) => ({
                        ...prev,
                        deliveryMode: event.target.value === 'jsonc' || event.target.value === 'both' ? event.target.value : 'alerts',
                      }))}
                      className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200 focus:border-sky-500 focus:outline-none"
                    >
                      {(Object.keys(DELIVERY_LABEL) as TelegramDeliveryMode[]).map((mode) => (
                        <option key={mode} value={mode}>{DELIVERY_LABEL[mode]}</option>
                      ))}
                    </select>
                  </label>
                  <label className="block">
                    <span className="mb-1 block text-[10px] uppercase tracking-wider text-slate-500">Сценарий для бота</span>
                    <select
                      value={settings.scenario || ''}
                      onChange={(event) => setSettings((prev) => ({ ...prev, scenario: event.target.value || null }))}
                      className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200 focus:border-sky-500 focus:outline-none"
                    >
                      <option value="">Не выбран</option>
                      {scenarios.map((scenario) => (
                        <option key={scenario.id} value={scenario.id}>{scenario.name || scenario.id}</option>
                      ))}
                    </select>
                  </label>
                </div>
                {scenariosError && <p className="text-[10px] text-amber-300">{scenariosError}</p>}
                {scenarios.length === 0 && !scenariosError && (
                  <p className="text-[10px] text-slate-500">В workspace пока нет сценариев для выбора.</p>
                )}

                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void saveSettings()}
                    disabled={settingsBusy}
                    className="flex items-center gap-1.5 rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-sky-500 disabled:bg-slate-700 disabled:text-slate-400"
                  >
                    {settingsBusy ? <Loader2 size={13} className="animate-spin" /> : <Settings size={13} />}
                    {settingsBusy ? 'Сохраняем…' : 'Сохранить'}
                  </button>
                  <button
                    type="button"
                    onClick={() => void unbind()}
                    disabled={unbinding}
                    className={`rounded-lg border px-3 py-1.5 text-xs ${
                      confirmUnbind
                        ? 'border-rose-800 bg-rose-950/70 text-rose-200 hover:bg-rose-900'
                        : 'border-slate-700 bg-slate-900 text-slate-400 hover:bg-slate-800'
                    } disabled:opacity-50`}
                  >
                    {unbinding ? 'Отвязываем…' : confirmUnbind ? 'Подтвердить отвязку' : 'Отвязать Telegram'}
                  </button>
                  {confirmUnbind && (
                    <button
                      type="button"
                      onClick={() => setConfirmUnbind(false)}
                      className="text-[10px] text-slate-500 underline decoration-slate-700 underline-offset-2 hover:text-slate-300"
                    >
                      Отмена
                    </button>
                  )}
                </div>
              </div>
            )}
          </div>
        </section>

        <section className="rounded-xl border border-slate-800 bg-slate-900/70 overflow-hidden">
          <div className="flex flex-wrap items-start justify-between gap-2 border-b border-slate-800 p-3.5">
            <div className="flex items-start gap-2">
              <div className="rounded-lg border border-purple-800 bg-purple-950/60 p-1.5 text-purple-300">
                <Clock size={16} />
              </div>
              <div>
                <h2 className="text-xs font-bold uppercase tracking-wider text-slate-300">Мои автозапуски</h2>
                <p className="mt-0.5 text-[11px] text-slate-500">
                  Расписания архивов и наблюдения за коммитами только этого workspace.
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => setAutomationOpen(true)}
              disabled={!accountMode}
              className="flex items-center gap-1 rounded-lg border border-purple-800 bg-purple-950/60 px-2.5 py-1.5 text-[11px] text-purple-200 hover:bg-purple-900 disabled:opacity-40"
            >
              <Settings size={12} /> Настроить
            </button>
          </div>

          <div className="p-3.5 space-y-3">
            {!accountMode && (
              <p className="rounded-lg border border-dashed border-slate-800 px-3 py-4 text-center text-xs text-slate-500">
                Собственные schedules и repository watches доступны только личному аккаунту; demo-user получает 403.
              </p>
            )}

            {accountMode && automationLoading && (
              <div className="flex items-center gap-2 text-xs text-slate-400">
                <Loader2 size={14} className="animate-spin" /> Загружаем ваши автозапуски…
              </div>
            )}

            {accountMode && !automationLoading && scheduleError && (
              <div className="rounded-lg border border-amber-900 bg-amber-950/40 px-2.5 py-2 text-[11px] text-amber-200">
                Расписания: {scheduleError}
              </div>
            )}
            {accountMode && !automationLoading && watchError && (
              <div className="rounded-lg border border-amber-900 bg-amber-950/40 px-2.5 py-2 text-[11px] text-amber-200">
                Наблюдения: {watchError}
              </div>
            )}

            {accountMode && !automationLoading && automationTotal === 0 && !scheduleError && !watchError && (
              <p className="rounded-lg border border-dashed border-slate-800 px-3 py-4 text-center text-xs text-slate-500">
                Пока нет расписаний и наблюдений. Нажмите «Настроить», чтобы создать свой триггер.
              </p>
            )}

            {accountMode && !automationLoading && automationTotal > 0 && (
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                <div className="rounded-lg border border-slate-800 bg-slate-950/50 p-2.5">
                  <div className="mb-1.5 flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-slate-500">
                    <Clock size={11} /> Расписания ({schedules.length})
                  </div>
                  {schedules.length === 0 ? (
                    <p className="text-[11px] text-slate-600">Нет</p>
                  ) : (
                    <div className="space-y-1">
                      {schedules.slice(0, 4).map((schedule) => (
                        <div key={schedule.id} className="flex items-center justify-between gap-2 text-[11px]">
                          <span className="font-mono text-purple-300">{cadenceLabel(schedule.cron_or_interval)}</span>
                          <span className={isEnabled(schedule) ? 'text-emerald-300' : 'text-slate-500'}>
                            {isEnabled(schedule) ? 'активно' : 'пауза'}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                <div className="rounded-lg border border-slate-800 bg-slate-950/50 p-2.5">
                  <div className="mb-1.5 flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-slate-500">
                    <GitBranch size={11} /> Наблюдения ({watches.length})
                  </div>
                  {watches.length === 0 ? (
                    <p className="text-[11px] text-slate-600">Нет</p>
                  ) : (
                    <div className="space-y-1">
                      {watches.slice(0, 4).map((watch) => (
                        <div key={watch.id} className="flex items-center justify-between gap-2 text-[11px]">
                          <span className="truncate text-slate-300">{repositoryShortName(watch.url)}</span>
                          <span className="shrink-0 text-slate-500">{providerLabel(watch.provider)}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}

            {accountMode && (schedules.length > 0 || watches.length > 0) && (
              <button
                type="button"
                onClick={() => setAutomationOpen(true)}
                className="text-[10px] text-purple-300 underline decoration-purple-800 underline-offset-2 hover:text-purple-200"
              >
                Управлять расписаниями и наблюдениями
              </button>
            )}
          </div>
        </section>
      </div>

      <ScheduleModal
        workflowId={workflowId}
        artifactId={artifactId}
        isOpen={automationOpen && accountMode}
        onClose={() => {
          setAutomationOpen(false);
          void refreshAutomation();
        }}
        adminAllowed={accountMode}
        onOpenRun={onOpenRun}
      />
    </div>
  );
};