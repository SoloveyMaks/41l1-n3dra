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

  const handleCreateSchedule = async () => {
    if (!artifactId) {
      setMessage('Сначала выберите или загрузите учебный проект (артефакт)');
      return;
    }
    setIsSaving(true);
    setMessage(null);
    try {
      await apiClient.createSchedule(workflowId, artifactId, cronOrInterval);
      setMessage('Расписание успешно создано и активировано!');
      loadSchedules();
    } catch (e: any) {
      setMessage(`Ошибка: ${e.message}`);
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
          {message && (
            <div className="p-3 rounded-xl bg-indigo-950/80 border border-indigo-800 text-indigo-200">
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
              Примеры: <code className="text-purple-300">interval:30</code> (каждые 30 сек), <code className="text-purple-300">*/15 * * * *</code> (каждые 15 минут)
            </p>
          </div>

          <div>
            <h4 className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">
              Активные расписания в системе ({schedules.length})
            </h4>
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
