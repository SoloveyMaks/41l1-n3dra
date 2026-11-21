import React, { useState, useEffect } from 'react';
import { Clock, X, Plus, CheckCircle2, Play, AlertCircle } from 'lucide-react';
import { apiClient } from '../api/client';

interface ScheduleModalProps {
  workflowId: string;
  artifactId?: string;
  isOpen: boolean;
  onClose: () => void;
}

export const ScheduleModal: React.FC<ScheduleModalProps> = ({
  workflowId,
  artifactId,
  isOpen,
  onClose,
}) => {
  const [schedules, setSchedules] = useState<any[]>([]);
  const [cronOrInterval, setCronOrInterval] = useState('interval:60');
  const [isSaving, setIsSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [messageTone, setMessageTone] = useState<'ok' | 'error'>('ok');

  const loadSchedules = async () => {
    try {
      const list = await apiClient.listSchedules();
      setSchedules(list);
    } catch (e) {
      console.error(e);
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
    // Current scheduler implements only interval:N and */N * * * * (minutes).
    const everyMinutes = spec.match(/^\*\/(\d+) \* \* \* \*$/);
    return Boolean(everyMinutes && Number(everyMinutes[1]) > 0);
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
      setMessage('Поддерживаются interval:60 и */15 * * * *. Произвольный cron сервер сейчас не соблюдает.');
      return;
    }
    setIsSaving(true);
    setMessage(null);
    try {
      await apiClient.createSchedule(workflowId, artifactId, cronOrInterval.trim());
      setMessageTone('ok');
      setMessage('Расписание создано. Это только запуск workflow; внешние уведомления не отправляются.');
      loadSchedules();
    } catch (e: any) {
      setMessageTone('error');
      setMessage(`Ошибка расписания: ${e.message}`);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
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
              Поддерживаются <code className="text-purple-300">interval:30</code> (каждые 30 сек) и <code className="text-purple-300">*/15 * * * *</code> (каждые 15 минут). Произвольный 5-field cron сервер сейчас исполняет как минутный — UI его не отправляет.
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
                schedules.map((s) => (
                  <div key={s.id} className="p-2.5 rounded-lg bg-slate-800/80 border border-slate-700 flex items-center justify-between font-mono text-[11px]">
                    <div>
                      <span className="text-purple-400 font-bold">{s.cron_or_interval}</span>
                      <div className="text-[10px] text-slate-400 font-sans">
                        Сценарий: {s.workflow_id} • Артефакт: {s.artifact_id}
                      </div>
                    </div>
                    <div className="text-right text-[10px] text-slate-400">
                      <div>Следующий запуск:</div>
                      <div className="text-emerald-400">
                        {s.next_fire_at ? new Date(s.next_fire_at).toLocaleTimeString() : 'Скоро'}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
