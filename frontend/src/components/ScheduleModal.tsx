import React, { useState, useEffect } from 'react';
import { Clock, X, Plus, CheckCircle2, Play, Pause, AlertCircle } from 'lucide-react';
import { apiClient } from '../api/client';

interface ScheduleModalProps {
  workflowId: string;
  artifactId?: string;
  isOpen: boolean;
  onClose: () => void;
  embedded?: boolean;
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

const isEnabled = (row: ScheduleRow): boolean =>
  row.enabled === true || row.enabled === 1 || row.enabled === '1' || row.enabled === 'true';

const formatTime = (value?: string | null): string => {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? '—' : parsed.toLocaleString('ru-RU');
};

export const ScheduleModal: React.FC<ScheduleModalProps> = ({
  workflowId,
  artifactId,
  isOpen,
  onClose,
  embedded = false,
}) => {
  const [schedules, setSchedules] = useState<ScheduleRow[]>([]);
  const [cronOrInterval, setCronOrInterval] = useState('interval:60');
  const [isSaving, setIsSaving] = useState(false);
  const [togglingId, setTogglingId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [messageTone, setMessageTone] = useState<'ok' | 'error'>('ok');

  const loadSchedules = async () => {
    try {
      const list = await apiClient.listSchedules();
      setSchedules(list);
    } catch (error: unknown) {
      setMessageTone('error');
      setMessage(error instanceof Error ? error.message : 'Не удалось загрузить расписания');
    }
  };

  const toggleSchedule = async (row: ScheduleRow) => {
    const nextEnabled = !isEnabled(row);
    setTogglingId(row.id);
    setMessage(null);
    try {
      await apiClient.setScheduleEnabled(row.id, nextEnabled);
      setMessageTone('ok');
      setMessage(nextEnabled ? 'Расписание включено; следующий запуск пересчитан.' : 'Расписание поставлено на паузу.');
      await loadSchedules();
    } catch (error: unknown) {
      setMessageTone('error');
      setMessage(error instanceof Error ? error.message : 'Не удалось изменить расписание');
    } finally {
      setTogglingId(null);
    }
  };

  useEffect(() => {
    if (isOpen) {
      loadSchedules();
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const isValidScheduleSpec = (value: string): boolean => {
    const spec = value.trim();
    if (/^interval:\d+$/.test(spec)) return Number(spec.split(':')[1]) > 0;
    const parts = spec.split(/\s+/);
    return parts.length === 5 && parts.every((part) => /^[0-9A-Za-z*/,?\-\/]+$/.test(part));
  };

  const handleCreateSchedule = async () => {
    if (!artifactId) {
      setMessageTone('error');
      setMessage('Сначала выберите или загрузите ZIP: расписанию нужен артефакт');
      return;
    }
    if (!cronOrInterval.trim()) {
      setMessageTone('error');
      setMessage('Укажите cron-выражение или interval:секунды');
      return;
    }
    if (!isValidScheduleSpec(cronOrInterval)) {
      setMessageTone('error');
      setMessage('Некорректное расписание. Примеры: interval:60 или 0 0 1 1 *');
      return;
    }
    setIsSaving(true);
    setMessage(null);
    try {
      await apiClient.createSchedule(workflowId, artifactId, cronOrInterval.trim());
      setMessageTone('ok');
      setMessage('Расписание создано. Это только запуск workflow; внешние уведомления не отправляются.');
      loadSchedules();
    } catch (error: unknown) {
      setMessageTone('error');
      setMessage(`Ошибка расписания: ${error instanceof Error ? error.message : 'неизвестная ошибка'}`);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className={embedded ? 'w-full' : 'fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4'}>
      <div className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-lg overflow-hidden shadow-2xl">
        <div className="p-4 border-b border-slate-800 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Clock size={18} className="text-purple-400" />
            <h3 className="text-sm font-bold text-slate-100">Настройка запуска по расписанию</h3>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-200">
            <X size={16} />
          </button>
        </div>

        <div className="p-6 space-y-5 text-xs text-slate-300">
          <div className="p-3 rounded-xl bg-slate-950/80 border border-slate-800 text-slate-400 leading-relaxed">
            Расписание только запускает сохранённый workflow с выбранным артефактом. Отправка уведомлений по умолчанию выключена; внешние адреса не используются.
          </div>

          {message && (
            <div className={`p-3 rounded-xl border ${messageTone === 'error' ? 'bg-rose-950/80 border-rose-800 text-rose-200' : 'bg-indigo-950/80 border-indigo-800 text-indigo-200'}`}>
              {message}
            </div>
          )}

          <div className="space-y-3 p-4 rounded-xl bg-slate-950 border border-slate-800">
            <div className="font-semibold text-slate-200">Новое расписание для сценария:</div>
            <div>
              <label className="block text-[11px] text-slate-400 mb-1">
                Интервал (сек) или Cron-выражение:
              </label>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={cronOrInterval}
                  onChange={(e) => setCronOrInterval(e.target.value)}
                  placeholder="interval:60 или */10 * * * *"
                  className="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-3 py-1.5 font-mono text-purple-300 focus:outline-none focus:border-purple-500"
                />
                <button
                  onClick={handleCreateSchedule}
                  disabled={isSaving}
                  className="px-3.5 py-1.5 rounded-lg bg-purple-600 hover:bg-purple-500 text-white font-medium transition cursor-pointer"
                >
                  Активировать
                </button>
              </div>
            </div>
            <p className="text-[10px] text-slate-500">
              Поддерживаются <code className="text-purple-300">interval:30</code> (каждые 30 сек) и обычный 5-field cron, например <code className="text-purple-300">0 0 1 1 *</code>. Сервер сам отклоняет невозможные даты и невалидный cron.
            </p>
          </div>

          <div>
            <div className="flex items-center justify-between mb-2">
              <h4 className="text-xs font-bold text-slate-400 uppercase tracking-wider">
                Активные расписания в системе ({schedules.length})
              </h4>
              <button onClick={loadSchedules} className="text-[10px] text-slate-400 hover:text-slate-200">Обновить</button>
            </div>
            <div className="space-y-2 max-h-48 overflow-y-auto">
              {schedules.length === 0 ? (
                <div className="text-slate-500 p-2 text-center">Расписания пока не созданы</div>
              ) : (
                schedules.map((s) => {
                  const enabled = isEnabled(s);
                  const toggling = togglingId === s.id;
                  return (
                    <div key={s.id} className="p-2.5 rounded-lg bg-slate-800/80 border border-slate-700 flex flex-col gap-2 sm:flex-row sm:items-center font-mono text-[11px]">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-purple-400 font-bold break-all">{s.cron_or_interval}</span>
                          <span className={`rounded border px-1.5 py-0.5 text-[9px] font-sans font-semibold ${enabled ? 'border-emerald-800 bg-emerald-950 text-emerald-300' : 'border-amber-800 bg-amber-950 text-amber-300'}`}>
                            {enabled ? 'Активно' : 'На паузе'}
                          </span>
                        </div>
                        <div className="text-[10px] text-slate-400 font-sans break-all">
                          Сценарий: {s.workflow_id} • Артефакт: {s.artifact_id}
                        </div>
                      </div>
                      <div className="flex items-center gap-2 sm:ml-auto">
                        <div className="text-right text-[10px] text-slate-400 min-w-0">
                          <div>Следующий запуск:</div>
                          <div className={enabled ? 'text-emerald-400' : 'text-slate-500'}>
                            {enabled ? formatTime(s.next_fire_at) : '—'}
                          </div>
                        </div>
                        <button
                          onClick={() => void toggleSchedule(s)}
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
                })
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
