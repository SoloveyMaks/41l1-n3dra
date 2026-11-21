import React, { useEffect, useRef, useState } from 'react';
import {
  Clock, X, Play, Pause, AlertCircle, GitBranch, RefreshCw, FolderArchive, CheckCircle2, ExternalLink,
  Webhook, Copy, KeyRound, ShieldAlert,
} from 'lucide-react';
import { apiClient, ApiError } from '../api/client';
import { RepositoryCheckResult, RepositoryTriggerMode, RepositoryWatch, RepositoryWebhookSetup } from '../types';
import {
  describeRepositoryError, isValidCronOrInterval, parseRepositoryUrl, providerLabel,
  repositoryShortName, shortCommit, validateBranch,
} from '../repository';

interface ScheduleModalProps {
  workflowId: string;
  artifactId?: string;
  isOpen: boolean;
  onClose: () => void;
  embedded?: boolean;
  adminAllowed?: boolean;
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

type Mode = 'archive' | 'poll' | 'webhook';

const isEnabled = (row: { enabled?: boolean | number | string }): boolean =>
  row.enabled === true || row.enabled === 1 || row.enabled === '1' || row.enabled === 'true';

const triggerMode = (watch: RepositoryWatch): RepositoryTriggerMode =>
  watch.trigger_mode === 'webhook' ? 'webhook' : 'poll';

const maskSecret = (secret: string): string => {
  if (!secret) return '••••••••';
  return secret.length <= 10 ? '••••••••' : `${secret.slice(0, 4)}••••••${secret.slice(-4)}`;
};

const deliveryStatusLabel = (status?: string | null): string => {
  switch ((status || '').toLowerCase()) {
    case 'accepted': return 'принят';
    case 'duplicate': return 'повтор — запуск не создан';
    case 'ignored': return 'пропущен';
    case 'error':
    case 'failed': return 'ошибка';
    case 'queued': return 'в очереди';
    default: return status || '—';
  }
};

const setupErrorText = (error: unknown): string => {
  if (error instanceof ApiError && error.status === 503) {
    const detail = error.message && !/^HTTP \d+$/.test(error.message) ? error.message : '';
    return detail
      ? `Настройка доставки недоступна: ${detail}`
      : 'Настройка доставки недоступна: на сервере нет стабильного ключа для выпуска секрета. Это ограничение сервера, а не ошибка ссылки.';
  }
  return describeRepositoryError(error);
};

/** Копирование без хранения секрета: только буфер обмена текущего действия. */
const copyToClipboard = async (text: string): Promise<boolean> => {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const area = document.createElement('textarea');
      area.value = text;
      area.style.position = 'fixed';
      area.style.opacity = '0';
      document.body.appendChild(area);
      area.select();
      const copied = document.execCommand('copy');
      document.body.removeChild(area);
      return copied;
    } catch {
      return false;
    }
  }
};

const formatTime = (value?: string | null): string => {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? '—' : parsed.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
};

const cadenceLabel = (value: string): string => {
  const match = /^interval:(\d+)$/.exec(value.trim());
  if (!match) return `cron ${value.trim()}`;
  const seconds = Number(match[1]);
  if (seconds % 60 === 0) return `каждые ${seconds / 60} мин`;
  return `каждые ${seconds} сек`;
};

const checkResultText = (result: RepositoryCheckResult): string => {
  if (result.status === 'unchanged') return 'Изменений нет: ветка на том же коммите.';
  if (result.status === 'started') return `Новый коммит ${shortCommit(result.commitSha)}: проверка запущена.`;
  if (result.status === 'busy') return result.detail || 'Проверка этого репозитория уже идёт.';
  return result.detail || 'Не удалось проверить изменения.';
};

const watchListError = (error: unknown): string =>
  error instanceof ApiError && error.status === 404
    ? 'Наблюдения за репозиториями пока недоступны: серверная часть ещё не развёрнута.'
    : describeRepositoryError(error);

const loadScheduleData = async (): Promise<{
  schedules?: ScheduleRow[];
  watches?: RepositoryWatch[];
  scheduleError?: string;
  watchError?: string;
}> => {
  const [scheduleResult, watchResult] = await Promise.allSettled([
    apiClient.listSchedules(),
    apiClient.listRepositoryWatches(),
  ]);
  return {
    schedules: scheduleResult.status === 'fulfilled' ? scheduleResult.value : undefined,
    watches: watchResult.status === 'fulfilled' ? watchResult.value : undefined,
    scheduleError: scheduleResult.status === 'rejected' ? describeRepositoryError(scheduleResult.reason) : undefined,
    watchError: watchResult.status === 'rejected' ? watchListError(watchResult.reason) : undefined,
  };
};

export const ScheduleModal: React.FC<ScheduleModalProps> = ({
  workflowId,
  artifactId,
  isOpen,
  onClose,
  embedded = false,
  adminAllowed = true,
  onOpenRun,
}) => {
  const [mode, setMode] = useState<Mode>('archive');
  const [schedules, setSchedules] = useState<ScheduleRow[]>([]);
  const [watches, setWatches] = useState<RepositoryWatch[]>([]);
  const [loading, setLoading] = useState(false);
  const [cronOrInterval, setCronOrInterval] = useState('interval:60');
  const [watchUrl, setWatchUrl] = useState('');
  const [watchBranch, setWatchBranch] = useState('');
  const [watchCadence, setWatchCadence] = useState('interval:300');
  const [isSaving, setIsSaving] = useState(false);
  const [togglingId, setTogglingId] = useState<string | null>(null);
  const [checkingId, setCheckingId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [messageTone, setMessageTone] = useState<'ok' | 'error'>('ok');
  const [messageRunId, setMessageRunId] = useState<string | null>(null);
  const [watchError, setWatchError] = useState<string | null>(null);
  // Секрет живёт только в памяти вкладки: ни localStorage, ни логов.
  const [setup, setSetup] = useState<(RepositoryWebhookSetup & { watchId: string }) | null>(null);
  const [setupBusyId, setSetupBusyId] = useState<string | null>(null);
  const [rotateConfirmId, setRotateConfirmId] = useState<string | null>(null);

  const loadSeq = useRef(0);
  const workflowRef = useRef(workflowId);

  useEffect(() => {
    workflowRef.current = workflowId;
    setIsSaving(false);
    setSetup(null);
    setRotateConfirmId(null);
  }, [workflowId]);

  useEffect(() => {
    if (!isOpen) {
      loadSeq.current += 1;
      setLoading(false);
      setTogglingId(null);
      setCheckingId(null);
      setSetup(null);
      setSetupBusyId(null);
      setRotateConfirmId(null);
      return;
    }
    if (!adminAllowed) return;
    const seq = ++loadSeq.current;
    setLoading(true);
    setMessage(null);
    setMessageRunId(null);
    setWatchError(null);
    loadScheduleData()
      .then((data) => {
        if (seq !== loadSeq.current) return;
        if (data.schedules) setSchedules(data.schedules);
        if (data.watches) setWatches(data.watches);
        if (data.watchError) setWatchError(data.watchError);
        if (data.scheduleError) {
          setMessageTone('error');
          setMessage(data.scheduleError);
        }
      })
      .finally(() => {
        if (seq === loadSeq.current) setLoading(false);
      });
  }, [isOpen, adminAllowed, workflowId]);

  if (!isOpen) return null;

  const showError = (text: string) => {
    setMessageTone('error');
    setMessageRunId(null);
    setMessage(text);
  };

  const showOk = (text: string, runId?: string) => {
    setMessageTone('ok');
    setMessageRunId(runId || null);
    setMessage(text);
  };

  const refreshLists = async () => {
    const seq = ++loadSeq.current;
    const data = await loadScheduleData();
    if (seq !== loadSeq.current) return;
    if (data.schedules) setSchedules(data.schedules);
    if (data.watches) setWatches(data.watches);
    setWatchError(data.watchError || null);
  };

  const handleCreateSchedule = async () => {
    if (!artifactId) {
      showError('Сначала выберите или загрузите ZIP: расписанию нужен артефакт.');
      return;
    }
    const spec = cronOrInterval.trim();
    if (!spec) {
      showError('Укажите cron-выражение или interval:секунды.');
      return;
    }
    const parts = spec.split(/\s+/);
    const valid = /^interval:\d+$/.test(spec) ? Number(spec.split(':')[1]) > 0 : parts.length === 5 && parts.every((part) => /^[0-9A-Za-z*/,?\-/]+$/.test(part));
    if (!valid) {
      showError('Некорректное расписание. Примеры: interval:60 или 0 0 1 1 *.');
      return;
    }
    const projectAtRequest = workflowId;
    setIsSaving(true);
    setMessage(null);
    setMessageRunId(null);
    try {
      await apiClient.createSchedule(projectAtRequest, artifactId, spec);
      if (workflowRef.current !== projectAtRequest) return;
      await refreshLists();
      showOk('Расписание создано. Оно запускает сохранённый сценарий с выбранным архивом.');
    } catch (error: unknown) {
      if (workflowRef.current !== projectAtRequest) return;
      showError(`Ошибка расписания: ${describeRepositoryError(error)}`);
    } finally {
      if (workflowRef.current === projectAtRequest) setIsSaving(false);
    }
  };

  const handleCreateWatch = async (event: React.SyntheticEvent<HTMLFormElement>, kind: RepositoryTriggerMode) => {
    event.preventDefault();
    const isWebhook = kind === 'webhook';
    let url = '';
    let branch = '';
    try {
      url = parseRepositoryUrl(watchUrl).url;
      branch = validateBranch(watchBranch);
    } catch (error: unknown) {
      showError(error instanceof Error ? error.message : 'Проверьте ссылку на репозиторий');
      return;
    }
    const cadence = watchCadence.trim();
    if (!isWebhook && !isValidCronOrInterval(cadence)) {
      showError('Проверка изменений: минимальный интервал 60 секунд. Пример: interval:300.');
      return;
    }
    if (!workflowRef.current.trim()) {
      showError('Не выбран сценарий проверки.');
      return;
    }
    const projectAtRequest = workflowRef.current;
    setIsSaving(true);
    setMessage(null);
    setMessageRunId(null);
    setSetup(null);
    setRotateConfirmId(null);
    try {
      const watch = await apiClient.createRepositoryWatch({
        workflowId: projectAtRequest,
        url,
        branch: branch || undefined,
        ...(isWebhook ? { triggerMode: 'webhook' as const } : { cronOrInterval: cadence }),
      });
      if (workflowRef.current !== projectAtRequest) return;
      setWatchUrl('');
      setWatchBranch('');
      await refreshLists();
      if (!isWebhook) {
        showOk(`Наблюдение создано: ветка ${watch.branch}, проверка ${cadenceLabel(watch.cron_or_interval)}.`);
        return;
      }
      // Хук в репозитории ставит человек: выпускаем адрес и секрет сразу
      // после явного создания доставки, но ничего не сохраняем в браузере.
      try {
        const issued = await apiClient.setupRepositoryWebhook(watch.id, false);
        if (workflowRef.current !== projectAtRequest) return;
        setSetup({ ...issued, watchId: watch.id });
        await refreshLists();
        showOk(`Доставка создана для ветки ${watch.branch}. Скопируйте адрес и секрет и включите хук в ${providerLabel(watch.provider)}.`);
      } catch (error: unknown) {
        if (workflowRef.current !== projectAtRequest) return;
        showError(`Доставка создана, но секрет не выпущен. ${setupErrorText(error)}`);
      }
    } catch (error: unknown) {
      if (workflowRef.current !== projectAtRequest) return;
      showError(describeRepositoryError(error));
    } finally {
      if (workflowRef.current === projectAtRequest) setIsSaving(false);
    }
  };

  const setupWebhook = async (watch: RepositoryWatch, rotate: boolean) => {
    setSetupBusyId(watch.id);
    setMessage(null);
    setMessageRunId(null);
    try {
      const issued = await apiClient.setupRepositoryWebhook(watch.id, rotate);
      setSetup({ ...issued, watchId: watch.id });
      setRotateConfirmId(null);
      await refreshLists();
      showOk(rotate
        ? 'Секрет перевыпущен: старый больше не принимается. Обновите его в настройках хука.'
        : 'Адрес и секрет готовы: скопируйте их в настройки хука репозитория.');
    } catch (error: unknown) {
      showError(setupErrorText(error));
    } finally {
      setSetupBusyId(null);
    }
  };

  const copyValue = async (value: string, successText: string) => {
    const copied = await copyToClipboard(value);
    if (copied) {
      showOk(successText);
    } else {
      showError('Не удалось скопировать автоматически: выделите значение и скопируйте вручную.');
    }
  };

  const toggleSchedule = async (row: ScheduleRow) => {
    const nextEnabled = !isEnabled(row);
    setTogglingId(row.id);
    setMessage(null);
    setMessageRunId(null);
    try {
      await apiClient.setScheduleEnabled(row.id, nextEnabled);
      await refreshLists();
      showOk(nextEnabled ? 'Расписание включено; следующий запуск пересчитан.' : 'Расписание поставлено на паузу.');
    } catch (error: unknown) {
      showError(describeRepositoryError(error));
    } finally {
      setTogglingId(null);
    }
  };

  const toggleWatch = async (watch: RepositoryWatch) => {
    const nextEnabled = !isEnabled(watch);
    setTogglingId(watch.id);
    setMessage(null);
    setMessageRunId(null);
    try {
      await apiClient.setRepositoryWatchEnabled(watch.id, nextEnabled);
      await refreshLists();
      if (triggerMode(watch) === 'webhook') {
        showOk(nextEnabled
          ? 'Доставка включена: подписанные события пуша будут приниматься.'
          : 'Доставка на паузе: события пуша игнорируются, новые проверки не запускаются.');
      } else {
        showOk(nextEnabled
          ? 'Наблюдение включено: следующая проверка запланирована.'
          : 'Наблюдение на паузе: новые коммиты не проверяются.');
      }
    } catch (error: unknown) {
      showError(describeRepositoryError(error));
    } finally {
      setTogglingId(null);
    }
  };

  const checkNow = async (watch: RepositoryWatch) => {
    setCheckingId(watch.id);
    setMessage(null);
    setMessageRunId(null);
    try {
      const result = await apiClient.checkRepositoryWatchNow(watch.id);
      if (result.status === 'error') {
        showError(checkResultText(result));
      } else {
        await refreshLists();
        showOk(checkResultText(result), result.runId);
      }
    } catch (error: unknown) {
      showError(describeRepositoryError(error));
    } finally {
      setCheckingId(null);
    }
  };

  const content = (
    <div className={embedded ? 'w-full' : 'fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-3 backdrop-blur-sm sm:p-4'}>
      <div className={`max-h-[calc(100vh-1.5rem)] w-full overflow-y-auto rounded-2xl border border-slate-800 bg-slate-900 shadow-2xl ${mode === 'archive' ? 'max-w-lg' : 'max-w-2xl'}`}>
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-slate-800 bg-slate-900 p-4">
          <div className="flex items-center gap-2">
            <Clock size={18} className="text-purple-400" />
            <h3 className="text-sm font-bold text-slate-100">Автозапуск проверок</h3>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-200" aria-label="Закрыть">
            <X size={16} />
          </button>
        </div>

        <div className="space-y-4 p-4 text-xs text-slate-300 sm:p-6">
          {!adminAllowed ? (
            <div className="rounded-xl border border-amber-800 bg-amber-950/50 p-3 text-amber-200">
              Управление расписаниями и наблюдением за репозиториями доступно администратору.
            </div>
          ) : (
            <>
              <div className="grid grid-cols-1 gap-1 rounded-xl border border-slate-800 bg-slate-950/70 p-1 sm:grid-cols-3" role="tablist" aria-label="Тип автозапуска">
                <button
                  type="button"
                  role="tab"
                  aria-selected={mode === 'archive'}
                  onClick={() => { setMode('archive'); setMessage(null); setMessageRunId(null); setSetup(null); setRotateConfirmId(null); }}
                  className={`flex items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-xs font-semibold transition ${mode === 'archive' ? 'bg-purple-600 text-white' : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'}`}
                >
                  <FolderArchive size={14} /> Архив по расписанию
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={mode === 'poll'}
                  onClick={() => { setMode('poll'); setMessage(null); setMessageRunId(null); setSetup(null); setRotateConfirmId(null); }}
                  className={`flex items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-xs font-semibold transition ${mode === 'poll' ? 'bg-purple-600 text-white' : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'}`}
                >
                  <RefreshCw size={14} /> Проверка по таймеру
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={mode === 'webhook'}
                  onClick={() => { setMode('webhook'); setMessage(null); setMessageRunId(null); setSetup(null); setRotateConfirmId(null); }}
                  className={`flex items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-xs font-semibold transition ${mode === 'webhook' ? 'bg-purple-600 text-white' : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'}`}
                >
                  <Webhook size={14} /> Пуш в репозиторий
                </button>
              </div>

              {message && (
                <div
                  role={messageTone === 'error' ? 'alert' : 'status'}
                  className={`rounded-xl border p-3 ${messageTone === 'error' ? 'border-rose-800 bg-rose-950/80 text-rose-200' : 'border-emerald-800 bg-emerald-950/60 text-emerald-200'}`}
                >
                  <div className="break-words">{message}</div>
                  {messageRunId && onOpenRun && (
                    <button
                      onClick={() => onOpenRun(messageRunId)}
                      className="mt-2 inline-flex items-center gap-1 rounded border border-emerald-700 px-2 py-1 text-[11px] hover:bg-emerald-900"
                    >
                      <ExternalLink size={11} /> Открыть проверку
                    </button>
                  )}
                </div>
              )}
              {mode !== 'archive' && watchError && (
                <div role="alert" className="rounded-xl border border-amber-800 bg-amber-950/50 p-3 text-amber-200">
                  {watchError}
                </div>
              )}

              {mode === 'archive' ? (
                <>
                  <div className="rounded-xl border border-slate-800 bg-slate-950/80 p-3 leading-relaxed text-slate-400">
                    Фиксированный архив: сценарий запускается по времени с выбранным артефактом. Уведомления по умолчанию выключены.
                  </div>

                  <div className="space-y-3 rounded-xl border border-slate-800 bg-slate-950 p-4">
                    <div className="font-semibold text-slate-200">Новое расписание</div>
                    <label className="block">
                      <span className="mb-1 block text-[11px] text-slate-400">Интервал (сек) или cron-выражение</span>
                      <div className="flex flex-col gap-2 sm:flex-row">
                        <input
                          type="text"
                          value={cronOrInterval}
                          onChange={(event) => setCronOrInterval(event.target.value)}
                          placeholder="interval:60 или */10 * * * *"
                          aria-label="Интервал или cron"
                          className="w-full min-w-0 rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 font-mono text-purple-300 focus:border-purple-500 focus:outline-none sm:flex-1"
                        />
                        <button
                          onClick={() => void handleCreateSchedule()}
                          disabled={isSaving || !artifactId}
                          className="w-full rounded-lg bg-purple-600 px-3.5 py-1.5 font-medium text-white transition hover:bg-purple-500 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400 sm:w-auto"
                        >
                          {isSaving ? 'Создаём…' : 'Активировать'}
                        </button>
                      </div>
                    </label>
                    {!artifactId && <p className="text-[10px] text-amber-300">Сначала выберите проект: расписанию нужен архив.</p>}
                    <p className="text-[10px] text-slate-500">
                      Поддерживаются <code className="text-purple-300">interval:30</code> и обычный 5-field cron, например <code className="text-purple-300">0 0 1 1 *</code>.
                    </p>
                  </div>

                  <ScheduleList
                    schedules={schedules}
                    loading={loading}
                    togglingId={togglingId}
                    onToggle={(row) => void toggleSchedule(row)}
                    onRefresh={() => void refreshLists()}
                  />
                </>
              ) : mode === 'poll' ? (
                <>
                  <div className="space-y-2 rounded-xl border border-amber-900/60 bg-amber-950/30 p-3 leading-relaxed text-amber-100/90">
                    <p>
                      Проверка по таймеру: сервер сам опрашивает ветку, поэтому новая проверка запустится
                      с задержкой до одного интервала. Мгновенного уведомления от GitHub или GitLab здесь нет.
                    </p>
                    <p className="text-amber-200/80">
                      При создании запоминается текущий коммит ветки. Сразу после создания проверка не запускается:
                      сначала импортируйте проект и проверьте вручную, дальше наблюдение будет ловить новые коммиты.
                    </p>
                  </div>

                  <form onSubmit={(event) => void handleCreateWatch(event, 'poll')} className="space-y-3 rounded-xl border border-slate-800 bg-slate-950 p-4">
                    <div className="font-semibold text-slate-200">Наблюдать за новыми коммитами</div>
                    <label className="block">
                      <span className="mb-1 block text-[11px] text-slate-400">Репозиторий</span>
                      <input
                        type="url"
                        required
                        value={watchUrl}
                        onChange={(event) => setWatchUrl(event.target.value)}
                        placeholder="https://github.com/owner/repo"
                        aria-label="Репозиторий для наблюдения"
                        className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-slate-200 placeholder:text-slate-600 focus:border-purple-500 focus:outline-none"
                      />
                    </label>
                    <div className="grid gap-3 sm:grid-cols-2">
                      <label className="block">
                        <span className="mb-1 block text-[11px] text-slate-400">Ветка (необязательно)</span>
                        <input
                          type="text"
                          value={watchBranch}
                          onChange={(event) => setWatchBranch(event.target.value)}
                          placeholder="по умолчанию"
                          aria-label="Ветка наблюдения"
                          className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 font-mono text-slate-200 placeholder:font-sans placeholder:text-slate-600 focus:border-purple-500 focus:outline-none"
                        />
                      </label>
                      <label className="block">
                        <span className="mb-1 block text-[11px] text-slate-400">Как часто проверять</span>
                        <input
                          type="text"
                          required
                          value={watchCadence}
                          onChange={(event) => setWatchCadence(event.target.value)}
                          placeholder="interval:300"
                          aria-label="Частота проверки изменений"
                          className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 font-mono text-purple-300 focus:border-purple-500 focus:outline-none"
                        />
                      </label>
                    </div>
                    <p className="text-[10px] text-slate-500">
                      Минимум 60 секунд, по умолчанию 300. Можно cron, например <code className="text-purple-300">*/5 * * * *</code>.
                      Публичные GitHub и GitLab; приватные репозитории не поддерживаются.
                    </p>
                    <button
                      type="submit"
                      disabled={isSaving || !watchUrl.trim()}
                      className="flex w-full items-center justify-center gap-2 rounded-lg bg-purple-600 px-3.5 py-2 font-semibold text-white transition hover:bg-purple-500 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
                    >
                      {isSaving ? <RefreshCw size={13} className="animate-spin" /> : <GitBranch size={13} />}
                      {isSaving ? 'Создаём…' : 'Наблюдать за коммитами'}
                    </button>
                    <p className="text-[10px] text-slate-500">
                      Сценарий: <span className="font-mono">{workflowId}</span>
                    </p>
                  </form>

                  <WatchList
                    mode="poll"
                    watches={watches}
                    loading={loading}
                    togglingId={togglingId}
                    checkingId={checkingId}
                    onToggle={(watch) => void toggleWatch(watch)}
                    onCheck={(watch) => void checkNow(watch)}
                    onOpenRun={onOpenRun}
                    onRefresh={() => void refreshLists()}
                  />
                </>
              ) : (
                <>
                  <div className="space-y-2 rounded-xl border border-indigo-900/60 bg-indigo-950/30 p-3 leading-relaxed text-indigo-100/90">
                    <p>
                      Запуск по пушу: GitHub или GitLab присылает событие <span className="font-semibold">push</span> в выбранную ветку,
                      и проверка стартует сразу после доставки. Плановый опрос и cron здесь не нужны.
                    </p>
                    <p className="text-indigo-200/80">
                      Хук в репозитории ставится вручную и требует прав администратора репозитория.
                      Сам по себе этот экран ничего не подключает: после сохранения скопируйте адрес и секрет
                      в настройки вебхука провайдера.
                    </p>
                  </div>

                  <form onSubmit={(event) => void handleCreateWatch(event, 'webhook')} className="space-y-3 rounded-xl border border-slate-800 bg-slate-950 p-4">
                    <div className="font-semibold text-slate-200">Новая доставка по пушу</div>
                    <label className="block">
                      <span className="mb-1 block text-[11px] text-slate-400">Репозиторий</span>
                      <input
                        type="url"
                        required
                        value={watchUrl}
                        onChange={(event) => setWatchUrl(event.target.value)}
                        placeholder="https://github.com/owner/repo"
                        aria-label="Репозиторий для доставки по пушу"
                        className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-slate-200 placeholder:text-slate-600 focus:border-purple-500 focus:outline-none"
                      />
                    </label>
                    <label className="block">
                      <span className="mb-1 block text-[11px] text-slate-400">Ветка (необязательно)</span>
                      <input
                        type="text"
                        value={watchBranch}
                        onChange={(event) => setWatchBranch(event.target.value)}
                        placeholder="по умолчанию"
                        aria-label="Ветка доставки по пушу"
                        className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 font-mono text-slate-200 placeholder:font-sans placeholder:text-slate-600 focus:border-purple-500 focus:outline-none"
                      />
                    </label>
                    <p className="text-[10px] text-slate-500">
                      Публичные GitHub и GitLab; приватные репозитории не поддерживаются.
                      Событие принимается только для этой ветки; теги и другие ветки игнорируются.
                    </p>
                    <button
                      type="submit"
                      disabled={isSaving || !watchUrl.trim()}
                      className="flex w-full items-center justify-center gap-2 rounded-lg bg-purple-600 px-3.5 py-2 font-semibold text-white transition hover:bg-purple-500 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
                    >
                      {isSaving ? <RefreshCw size={13} className="animate-spin" /> : <Webhook size={13} />}
                      {isSaving ? 'Создаём…' : 'Подключить доставку'}
                    </button>
                    <p className="text-[10px] text-slate-500">
                      Сценарий: <span className="font-mono">{workflowId}</span>
                    </p>
                  </form>

                  {setup && (
                    <WebhookSetupPanel
                      setup={setup}
                      watch={watches.find((watch) => watch.id === setup.watchId)}
                      onCopy={copyValue}
                      onClose={() => setSetup(null)}
                    />
                  )}

                  <WatchList
                    mode="webhook"
                    watches={watches}
                    loading={loading}
                    togglingId={togglingId}
                    checkingId={checkingId}
                    onToggle={(watch) => void toggleWatch(watch)}
                    onCheck={(watch) => void checkNow(watch)}
                    onOpenRun={onOpenRun}
                    onRefresh={() => void refreshLists()}
                    onSetup={(watch, rotate) => void setupWebhook(watch, rotate)}
                    onRotateConfirm={setRotateConfirmId}
                    setupBusyId={setupBusyId}
                    rotateConfirmId={rotateConfirmId}
                    activeSetupWatchId={setup?.watchId || null}
                  />
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );

  return content;
};

const ScheduleList: React.FC<{
  schedules: ScheduleRow[];
  loading: boolean;
  togglingId: string | null;
  onToggle: (row: ScheduleRow) => void;
  onRefresh: () => void;
}> = ({ schedules, loading, togglingId, onToggle, onRefresh }) => (
  <div>
    <div className="mb-2 flex items-center justify-between">
      <h4 className="text-xs font-bold uppercase tracking-wider text-slate-400">
        Расписания архивов ({schedules.length})
      </h4>
      <button onClick={onRefresh} disabled={loading} className="text-[10px] text-slate-400 hover:text-slate-200 disabled:opacity-50">
        Обновить
      </button>
    </div>
    <div className="max-h-56 space-y-2 overflow-y-auto">
      {schedules.length === 0 ? (
        <div className="p-2 text-center text-slate-500">{loading ? 'Загружаем…' : 'Расписаний пока нет'}</div>
      ) : schedules.map((schedule) => {
        const enabled = isEnabled(schedule);
        const toggling = togglingId === schedule.id;
        return (
          <div key={schedule.id} className="flex flex-col gap-2 rounded-lg border border-slate-700 bg-slate-800/80 p-2.5 font-mono text-[11px] sm:flex-row sm:items-center">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="break-all font-bold text-purple-400">{schedule.cron_or_interval}</span>
                <span className={`rounded border px-1.5 py-0.5 font-sans text-[9px] font-semibold ${enabled ? 'border-emerald-800 bg-emerald-950 text-emerald-300' : 'border-amber-800 bg-amber-950 text-amber-300'}`}>
                  {enabled ? 'Активно' : 'На паузе'}
                </span>
              </div>
              <div className="break-all font-sans text-[10px] text-slate-400">
                Сценарий: {schedule.workflow_id} • Архив: {schedule.artifact_id}
              </div>
            </div>
            <div className="flex items-center gap-2 sm:ml-auto">
              <div className="min-w-0 text-right text-[10px] text-slate-400">
                <div>Следующий запуск:</div>
                <div className={enabled ? 'text-emerald-400' : 'text-slate-500'}>
                  {enabled ? formatTime(schedule.next_fire_at) : '—'}
                </div>
              </div>
              <button
                onClick={() => onToggle(schedule)}
                disabled={toggling}
                className="flex items-center gap-1 rounded border border-slate-600 bg-slate-900 px-2 py-1 text-[10px] text-slate-300 hover:bg-slate-700 disabled:opacity-50"
                title={enabled ? 'Поставить на паузу' : 'Включить расписание'}
              >
                {enabled ? <Pause size={11} /> : <Play size={11} />}
                {toggling ? '…' : enabled ? 'Пауза' : 'Включить'}
              </button>
            </div>
          </div>
        );
      })}
    </div>
  </div>
);

const WatchList: React.FC<{
  mode: RepositoryTriggerMode;
  watches: RepositoryWatch[];
  loading: boolean;
  togglingId: string | null;
  checkingId: string | null;
  onToggle: (watch: RepositoryWatch) => void;
  onCheck: (watch: RepositoryWatch) => void;
  onOpenRun?: (runId: string) => void;
  onRefresh: () => void;
  onSetup?: (watch: RepositoryWatch, rotate: boolean) => void;
  onRotateConfirm?: (watchId: string | null) => void;
  setupBusyId?: string | null;
  rotateConfirmId?: string | null;
  activeSetupWatchId?: string | null;
}> = ({
  mode, watches, loading, togglingId, checkingId, onToggle, onCheck, onOpenRun, onRefresh,
  onSetup, onRotateConfirm, setupBusyId, rotateConfirmId, activeSetupWatchId,
}) => (
  <div>
    <div className="mb-2 flex items-center justify-between">
      <h4 className="text-xs font-bold uppercase tracking-wider text-slate-400">
        {mode === 'webhook' ? 'Доставка по пушу' : 'Проверки по таймеру'}
        {' '}({watches.filter((watch) => triggerMode(watch) === mode).length})
      </h4>
      <button onClick={onRefresh} disabled={loading} className="text-[10px] text-slate-400 hover:text-slate-200 disabled:opacity-50">
        Обновить
      </button>
    </div>
    <div className="space-y-2">
      {watches.filter((watch) => triggerMode(watch) === mode).length === 0 ? (
        <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3 text-center text-slate-500">
          {loading ? 'Загружаем…' : mode === 'webhook' ? 'Доставок по пушу пока нет' : 'Проверок по таймеру пока нет'}
        </div>
      ) : watches.filter((watch) => triggerMode(watch) === mode).map((watch) => {
        const enabled = isEnabled(watch);
        const toggling = togglingId === watch.id;
        const checking = checkingId === watch.id;
        return (
          <div key={watch.id} className="rounded-xl border border-slate-800 bg-slate-950/70 p-3">
            <div className="flex flex-wrap items-start gap-2">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="break-all text-[11px] font-semibold text-slate-200">{repositoryShortName(watch.url)}</span>
                  <span className={`rounded border px-1.5 py-0.5 text-[9px] font-semibold ${enabled ? 'border-emerald-800 bg-emerald-950 text-emerald-300' : 'border-amber-800 bg-amber-950 text-amber-300'}`}>
                    {enabled ? 'Включено' : 'На паузе'}
                  </span>
                  {mode === 'webhook' && (
                    <span className={`rounded border px-1.5 py-0.5 text-[9px] font-semibold ${watch.webhook_ready ? 'border-indigo-800 bg-indigo-950 text-indigo-300' : 'border-slate-700 bg-slate-900 text-slate-400'}`}>
                      {watch.webhook_ready ? 'Секрет выпущен' : 'Секрет не выпущен'}
                    </span>
                  )}
                  {watch.last_error && (
                    <span className="rounded border border-rose-800 bg-rose-950 px-1.5 py-0.5 text-[9px] font-semibold text-rose-300">
                      Ошибка
                    </span>
                  )}
                </div>
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-slate-400">
                  <span>{providerLabel(watch.provider)}</span>
                  <span>ветка <span className="font-mono text-slate-300">{watch.branch}</span></span>
                  <span>{mode === 'webhook' ? 'событие push' : cadenceLabel(watch.cron_or_interval)}</span>
                </div>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {mode === 'webhook' && (
                  <>
                    <button
                      onClick={() => onSetup?.(watch, false)}
                      disabled={setupBusyId === watch.id}
                      title="Показать адрес и секрет доставки (секрет не сохраняется в браузере)"
                      className={`flex items-center gap-1 rounded border px-2 py-1 text-[10px] disabled:opacity-50 ${activeSetupWatchId === watch.id ? 'border-indigo-700 bg-indigo-950/60 text-indigo-300' : 'border-slate-600 bg-slate-900 text-slate-300 hover:bg-slate-700'}`}
                    >
                      <KeyRound size={11} />
                      {setupBusyId === watch.id ? 'Готовим…' : watch.webhook_ready ? 'Показать адрес' : 'Настроить доставку'}
                    </button>
                    {watch.webhook_ready && (
                      <button
                        onClick={() => onRotateConfirm?.(rotateConfirmId === watch.id ? null : watch.id)}
                        disabled={setupBusyId === watch.id}
                        className="flex items-center gap-1 rounded border border-amber-800 bg-amber-950/40 px-2 py-1 text-[10px] text-amber-200 hover:bg-amber-900/50 disabled:opacity-50"
                      >
                        <ShieldAlert size={11} /> Перевыпустить секрет
                      </button>
                    )}
                  </>
                )}
                <button
                  onClick={() => onCheck(watch)}
                  disabled={checking || !enabled}
                  title={enabled ? 'Разовая проверка ветки на новый коммит, не ожидание события' : 'Сначала включите наблюдение'}
                  className="flex items-center gap-1 rounded border border-slate-600 bg-slate-900 px-2 py-1 text-[10px] text-slate-300 hover:bg-slate-700 disabled:opacity-50"
                >
                  <RefreshCw size={11} className={checking ? 'animate-spin' : ''} />
                  {checking ? 'Проверяем…' : 'Проверить ветку сейчас'}
                </button>
                <button
                  onClick={() => onToggle(watch)}
                  disabled={toggling}
                  className="flex items-center gap-1 rounded border border-slate-600 bg-slate-900 px-2 py-1 text-[10px] text-slate-300 hover:bg-slate-700 disabled:opacity-50"
                >
                  {enabled ? <Pause size={11} /> : <Play size={11} />}
                  {toggling ? '…' : enabled ? 'Пауза' : 'Включить'}
                </button>
              </div>
            </div>

            <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[10px] text-slate-400 sm:grid-cols-4">
              {mode === 'webhook' ? (
                <>
                  <div>
                    <dt className="text-slate-500">Последняя доставка</dt>
                    <dd>{formatTime(watch.last_delivery_at)}</dd>
                  </div>
                  <div>
                    <dt className="text-slate-500">Статус доставки</dt>
                    <dd>{deliveryStatusLabel(watch.last_delivery_status)}</dd>
                  </div>
                </>
              ) : (
                <>
                  <div>
                    <dt className="text-slate-500">Последняя проверка</dt>
                    <dd>{formatTime(watch.last_checked_at)}</dd>
                  </div>
                  <div>
                    <dt className="text-slate-500">Следующая</dt>
                    <dd>{enabled ? formatTime(watch.next_check_at) : '—'}</dd>
                  </div>
                </>
              )}
              <div>
                <dt className="text-slate-500">Последний коммит</dt>
                <dd className="font-mono text-indigo-300" title={watch.last_seen_commit || ''}>{shortCommit(watch.last_seen_commit)}</dd>
              </div>
              <div>
                <dt className="text-slate-500">Сценарий</dt>
                <dd className="break-all font-mono">{watch.workflow_id}</dd>
              </div>
            </dl>

            {rotateConfirmId === watch.id && (
              <div className="mt-2 rounded-lg border border-amber-800 bg-amber-950/40 p-2 text-[10px] text-amber-100">
                <div className="flex items-start gap-1.5">
                  <ShieldAlert size={12} className="mt-0.5 shrink-0" />
                  <span>
                    Перевыпуск сразу отменит старый секрет: доставки с ним получат отказ.
                    Хук в {providerLabel(watch.provider)} нужно обновить тем же действием.
                  </span>
                </div>
                <div className="mt-2 flex flex-wrap gap-2">
                  <button
                    onClick={() => onSetup?.(watch, true)}
                    disabled={setupBusyId === watch.id}
                    className="rounded border border-amber-600 bg-amber-900/60 px-2 py-1 font-semibold text-amber-100 hover:bg-amber-800 disabled:opacity-50"
                  >
                    {setupBusyId === watch.id ? 'Перевыпускаем…' : 'Да, перевыпустить'}
                  </button>
                  <button
                    onClick={() => onRotateConfirm?.(null)}
                    className="rounded border border-slate-600 px-2 py-1 text-slate-300 hover:bg-slate-800"
                  >
                    Отмена
                  </button>
                </div>
              </div>
            )}

            {watch.last_error && (
              <div className="mt-2 flex items-start gap-1.5 rounded-lg border border-rose-900 bg-rose-950/50 p-2 text-[10px] text-rose-200">
                <AlertCircle size={12} className="mt-0.5 shrink-0" />
                <span className="break-words">{watch.last_error}</span>
              </div>
            )}
            {mode === 'webhook' && watch.last_delivery_error && (
              <div className="mt-2 flex items-start gap-1.5 rounded-lg border border-amber-900 bg-amber-950/40 p-2 text-[10px] text-amber-200">
                <AlertCircle size={12} className="mt-0.5 shrink-0" />
                <span className="break-words">Последняя доставка: {watch.last_delivery_error}</span>
              </div>
            )}

            <div className="mt-2 flex flex-wrap items-center gap-2 text-[10px] text-slate-400">
              <span>Последний запуск:</span>
              {watch.last_run_id ? (
                onOpenRun ? (
                  <button
                    onClick={() => onOpenRun(watch.last_run_id || '')}
                    className="inline-flex items-center gap-1 rounded border border-indigo-800 bg-indigo-950/50 px-2 py-1 text-indigo-300 hover:bg-indigo-900"
                  >
                    <ExternalLink size={10} /> открыть запуск
                  </button>
                ) : (
                  <span className="font-mono text-indigo-300">{watch.last_run_id}</span>
                )
              ) : (
                <span className="inline-flex items-center gap-1 text-slate-500">
                  <CheckCircle2 size={10} /> ещё не запускалась
                </span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  </div>
);

const WebhookSetupPanel: React.FC<{
  setup: RepositoryWebhookSetup & { watchId: string };
  watch?: RepositoryWatch;
  onCopy: (value: string, successText: string) => void;
  onClose: () => void;
}> = ({ setup, watch, onCopy, onClose }) => {
  const callbackUrl = `${window.location.origin}${setup.endpointPath.startsWith('/') ? '' : '/'}${setup.endpointPath}`;
  const repo = watch ? repositoryShortName(watch.url) : 'репозиторий';
  const provider = providerLabel(setup.provider);
  return (
    <div className="space-y-3 rounded-xl border border-emerald-800 bg-emerald-950/20 p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 font-semibold text-emerald-200">
          <KeyRound size={14} /> Адрес и секрет доставки
        </div>
        <button onClick={onClose} className="text-emerald-300/70 hover:text-emerald-100" aria-label="Скрыть адрес и секрет">
          <X size={14} />
        </button>
      </div>

      <p className="text-[10px] leading-relaxed text-emerald-100/80">
        Секрет не сохраняется в браузере и не пишется в журналы: он исчезнет при закрытии окна
        или смене сценария. Скопируйте его сейчас. При перевыпуске старый секрет сразу перестанет работать.
      </p>

      <label className="block">
        <span className="mb-1 block text-[11px] text-emerald-200/90">Payload URL / URL</span>
        <div className="flex items-center gap-1.5">
          <input
            readOnly
            value={callbackUrl}
            aria-label="Адрес доставки вебхука"
            onFocus={(event) => event.currentTarget.select()}
            className="w-full min-w-0 rounded-lg border border-emerald-900 bg-slate-950 px-2 py-1.5 font-mono text-[10px] text-emerald-100 focus:border-emerald-600 focus:outline-none"
          />
          <button
            onClick={() => onCopy(callbackUrl, 'Адрес доставки скопирован в буфер обмена.')}
            className="flex shrink-0 items-center gap-1 rounded border border-emerald-700 bg-emerald-900/40 px-2 py-1.5 text-[10px] text-emerald-100 hover:bg-emerald-800"
            aria-label="Скопировать адрес доставки"
          >
            <Copy size={11} /> Копировать
          </button>
        </div>
      </label>

      <label className="block">
        <span className="mb-1 block text-[11px] text-emerald-200/90">Secret / Secret token</span>
        <div className="flex items-center gap-1.5">
          <code className="w-full min-w-0 truncate rounded-lg border border-emerald-900 bg-slate-950 px-2 py-1.5 text-[10px] text-emerald-100">
            {maskSecret(setup.secret)}
          </code>
          <button
            onClick={() => onCopy(setup.secret, 'Секрет скопирован в буфер обмена.')}
            className="flex shrink-0 items-center gap-1 rounded border border-emerald-700 bg-emerald-900/40 px-2 py-1.5 text-[10px] text-emerald-100 hover:bg-emerald-800"
            aria-label="Скопировать секрет целиком"
            title="Скопирует секрет целиком, хотя на экране он в маске"
          >
            <Copy size={11} /> Копировать
          </button>
        </div>
      </label>

      <div className="rounded-lg border border-slate-700 bg-slate-950/70 p-2.5 text-[10px] leading-relaxed text-slate-300">
        <div className="mb-1 font-semibold text-slate-200">
          Как включить хук в {provider}: {repo}
        </div>
        {setup.provider === 'gitlab' ? (
          <ol className="list-decimal space-y-0.5 pl-4">
            <li>Проект → Settings → Webhooks → Add new webhook.</li>
            <li>URL — адрес доставки выше.</li>
            <li>Secret token — скопированный секрет.</li>
            <li>Trigger: только Push events; остальные события выключены.</li>
            <li>Сохраните хук. После первого пуша обновите этот список и проверьте статус доставки.</li>
          </ol>
        ) : setup.provider === 'github' ? (
          <ol className="list-decimal space-y-0.5 pl-4">
            <li>Репозиторий → Settings → Webhooks → Add webhook.</li>
            <li>Payload URL — адрес доставки выше; Content type — application/json.</li>
            <li>Secret — скопированный секрет.</li>
            <li>Which events? — Only the push event.</li>
            <li>Active включён, SSL verification включён → Add webhook.</li>
          </ol>
        ) : (
          <p>Настройте вебхук в интерфейсе провайдера: URL и секрет выше, событие — push.</p>
        )}
      </div>

      <div className="flex items-start gap-1.5 rounded-lg border border-amber-900/70 bg-amber-950/30 p-2 text-[10px] text-amber-200/90">
        <ShieldAlert size={12} className="mt-0.5 shrink-0" />
        <span>
          Хук ставится вручную и требует прав администратора репозитория в {provider}.
          Этот экран не подключает его автоматически и не подтверждает установку:
          «секрет выпущен» означает только готовность сервера.
        </span>
      </div>
    </div>
  );
};