import React from 'react';
import { X, Trash2, Settings, ShieldAlert, GitFork, Clock, Send, Sparkles } from 'lucide-react';
import { ArtifactManifest, WorkflowNode } from '../types';

interface InspectorPanelProps {
  node: WorkflowNode | null;
  artifacts: ArtifactManifest[];
  onUpdateConfig: (nodeId: string, newConfig: Record<string, any>, newTitle?: string) => void;
  onDeleteNode: (nodeId: string) => void;
  onClose: () => void;
}

export const InspectorPanel: React.FC<InspectorPanelProps> = ({
  node,
  artifacts,
  onUpdateConfig,
  onDeleteNode,
  onClose,
}) => {
  if (!node) return null;

  const config = node.config || {};

  const handleFieldChange = (key: string, value: any) => {
    onUpdateConfig(node.id, { ...config, [key]: value });
  };

  const handleTitleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    onUpdateConfig(node.id, config, e.target.value);
  };

  return (
    <div className="w-full md:w-80 max-h-[45vh] md:max-h-none bg-slate-900/95 border-t md:border-t-0 md:border-l border-slate-800 flex flex-col md:h-full overflow-hidden shadow-2xl shrink-0">
      {/* Header */}
      <div className="p-3.5 border-b border-slate-800 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Settings size={16} className="text-indigo-400" />
          <h2 className="text-xs font-bold text-slate-200 uppercase tracking-wider">
            Настройки блока
          </h2>
        </div>
        <button
          onClick={onClose}
          className="text-slate-400 hover:text-slate-200 p-1 rounded-md hover:bg-slate-800"
        >
          <X size={16} />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4 text-xs text-slate-300">
        <div>
          <label className="block text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-1">
            Название блока
          </label>
          <input
            type="text"
            value={node.title || ''}
            onChange={handleTitleChange}
            placeholder={node.kind}
            className="w-full bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-1.5 text-slate-100 focus:outline-none focus:border-indigo-500"
          />
        </div>

        <div>
          <label className="block text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-1">
            Тип блока
          </label>
          <div className="bg-slate-800/60 border border-slate-700/60 rounded-lg px-2.5 py-1.5 font-mono text-[11px] text-indigo-300">
            {node.kind}
          </div>
        </div>

        <div className="border-t border-slate-800 pt-3">
          <label className="block text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-2">
            Параметры выполнения
          </label>

          {/* Node Specific Controls */}
          {node.kind === 'schedule_trigger' && (
            <div className="space-y-3">
              <div>
                <label className="block text-[10px] text-slate-400 mb-1">Cron или интервал:</label>
                <input
                  type="text"
                  value={config.cron || '*/30 * * * *'}
                  onChange={(e) => handleFieldChange('cron', e.target.value)}
                  placeholder="*/30 * * * * или interval:60"
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-1.5 font-mono text-xs text-purple-300 focus:outline-none focus:border-indigo-500"
                />
              </div>
            </div>
          )}

          {node.kind === 'project_input' && (
            <div className="space-y-3">
              <div>
                <label className="block text-[10px] text-slate-400 mb-1">Выбрать учебный ZIP:</label>
                <select
                  value={config.artifactId || ''}
                  onChange={(e) => handleFieldChange('artifactId', e.target.value)}
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-indigo-500"
                >
                  <option value="">Авто-выбор (последний загруженный)</option>
                  {artifacts.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.filename} ({a.kotlin_file_count} kt файлов)
                    </option>
                  ))}
                </select>
              </div>
            </div>
          )}

          {node.kind === 'policy_gate' && (
            <div className="space-y-2.5">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={config.failOnCritical !== false}
                  onChange={(e) => handleFieldChange('failOnCritical', e.target.checked)}
                  className="rounded border-slate-700 bg-slate-800 text-indigo-500 focus:ring-0"
                />
                <span>Блокировать при Critical (Fail)</span>
              </label>

              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={config.failOnHigh !== false}
                  onChange={(e) => handleFieldChange('failOnHigh', e.target.checked)}
                  className="rounded border-slate-700 bg-slate-800 text-indigo-500 focus:ring-0"
                />
                <span>Блокировать при High (Fail)</span>
              </label>

              <div>
                <label className="block text-[10px] text-slate-400 mb-1">Максимум Medium находок:</label>
                <input
                  type="number"
                  min={0}
                  max={50}
                  value={config.maxAllowedMedium ?? 2}
                  onChange={(e) => handleFieldChange('maxAllowedMedium', parseInt(e.target.value, 10))}
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-1 text-xs"
                />
              </div>

              <label className="flex items-center gap-2 cursor-pointer pt-1">
                <input
                  type="checkbox"
                  checked={config.strictCoverageRequired !== false}
                  onChange={(e) => handleFieldChange('strictCoverageRequired', e.target.checked)}
                  className="rounded border-slate-700 bg-slate-800 text-indigo-500 focus:ring-0"
                />
                <span>Inconclusive при ошибках парсера</span>
              </label>
            </div>
          )}

          {node.kind === 'condition' && (
            <div className="space-y-3">
              <div>
                <label className="block text-[10px] text-slate-400 mb-1">Поле для проверки:</label>
                <select
                  value={config.field || 'verdict'}
                  onChange={(e) => handleFieldChange('field', e.target.value)}
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-1 text-xs text-slate-200"
                >
                  <option value="verdict">Итоговый вердикт (verdict)</option>
                  <option value="critical_count">Число критических (critical_count)</option>
                  <option value="high_count">Число высокого риска (high_count)</option>
                  <option value="findings_count">Всего находок (findings_count)</option>
                </select>
              </div>

              <div>
                <label className="block text-[10px] text-slate-400 mb-1">Оператор сравнения:</label>
                <select
                  value={config.operator || 'equals'}
                  onChange={(e) => handleFieldChange('operator', e.target.value)}
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-1 text-xs text-slate-200"
                >
                  <option value="equals">Равно (equals)</option>
                  <option value="not_equals">Не равно (not equals)</option>
                  <option value="greater_than">Больше (&gt;)</option>
                  <option value="less_than">Меньше (&lt;)</option>
                  <option value="regex">Регулярное выражение (regex)</option>
                </select>
              </div>

              <div>
                <label className="block text-[10px] text-slate-400 mb-1">Ожидаемое значение:</label>
                <input
                  type="text"
                  value={config.targetValue || 'fail'}
                  onChange={(e) => handleFieldChange('targetValue', e.target.value)}
                  placeholder="fail, 0, или regex"
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-1 font-mono text-xs text-yellow-300"
                />
              </div>

              <div className="p-2.5 rounded-lg bg-yellow-950/30 border border-yellow-800/40 text-[10px] text-yellow-200 leading-relaxed">
                Если результат совпадет — активируется верхний порт (True Branch), иначе нижний порт (False Branch). Ошибка regex вызовет явный сбой ноды.
              </div>
            </div>
          )}

          {node.kind === 'ai_scan' && (
            <div className="space-y-3">
              <div className="rounded-lg border border-fuchsia-900/60 bg-fuchsia-950/30 p-2.5 text-[10px] leading-relaxed text-fuchsia-200">
                AI-сканер только предлагает гипотезы по ограниченному redacted-фрагменту.
                Это не подтверждённые findings, он не меняет вердикт, отчёт и счётчики политики.
              </div>
              <div>
                <label className="block text-[10px] text-slate-400 mb-1">Профиль бюджета:</label>
                <select
                  value={config.profile || 'balanced'}
                  onChange={(e) => handleFieldChange('profile', e.target.value)}
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-1 text-xs text-slate-200"
                >
                  <option value="fast">fast — меньше файлов</option>
                  <option value="balanced">balanced — по умолчанию</option>
                  <option value="deep">deep — больше контекста</option>
                </select>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <label className="block">
                  <span className="block text-[10px] text-slate-400 mb-1">Файлов:</span>
                  <input
                    type="number"
                    min={1}
                    max={24}
                    value={config.maxFiles ?? 6}
                    onChange={(e) => handleFieldChange('maxFiles', parseInt(e.target.value, 10) || 1)}
                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-2 py-1 text-xs"
                  />
                </label>
                <label className="block">
                  <span className="block text-[10px] text-slate-400 mb-1">Гипотез:</span>
                  <input
                    type="number"
                    min={1}
                    max={50}
                    value={config.maxObservations ?? 12}
                    onChange={(e) => handleFieldChange('maxObservations', parseInt(e.target.value, 10) || 1)}
                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-2 py-1 text-xs"
                  />
                </label>
                <label className="block">
                  <span className="block text-[10px] text-slate-400 mb-1">Всего байт:</span>
                  <input
                    type="number"
                    min={1024}
                    max={131072}
                    step={1024}
                    value={config.maxBytes ?? 24576}
                    onChange={(e) => handleFieldChange('maxBytes', parseInt(e.target.value, 10) || 1024)}
                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-2 py-1 text-xs"
                  />
                </label>
                <label className="block">
                  <span className="block text-[10px] text-slate-400 mb-1">Байт на файл:</span>
                  <input
                    type="number"
                    min={512}
                    max={32768}
                    step={512}
                    value={config.perFileBytes ?? 4096}
                    onChange={(e) => handleFieldChange('perFileBytes', parseInt(e.target.value, 10) || 512)}
                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-2 py-1 text-xs"
                  />
                </label>
              </div>
              <p className="text-[10px] text-slate-500">
                Провайдер, модель и ключ задаются только на сервере. Выбор блока разрешает отправку ограниченных redacted-фрагментов.
              </p>
            </div>
          )}

          {node.kind === 'ai_explain' && (
            <div className="space-y-3">
              <div>
                <label className="block text-[10px] text-slate-400 mb-1">Модель LLM:</label>
                <div className="w-full rounded-lg border border-slate-700 bg-slate-800 px-2.5 py-1.5 text-xs text-slate-200">
                  reformboss · deepseek-v4.1-flash-1017
                </div>
                <p className="mt-1 text-[10px] text-slate-500">
                  Модель и ключ задаются сервером через AI_MODEL/REFORMBOSS_API_KEY.
                </p>
              </div>

              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={config.strictValidation !== false}
                  onChange={(e) => handleFieldChange('strictValidation', e.target.checked)}
                  className="rounded border-slate-700 bg-slate-800 text-fuchsia-500 focus:ring-0"
                />
                <span>Строгая валидация CVE и findingId</span>
              </label>
            </div>
          )}

          {node.kind === 'notify' && (
            <div className="space-y-3">
              <div className="p-2.5 rounded-lg bg-slate-950 border border-slate-700 text-[10px] text-slate-400 leading-relaxed">
                Уведомления по умолчанию выключены. Внешняя отправка не включена; для демо доступен только локальный mock-приёмник.
              </div>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={config.enabled === true}
                  onChange={(e) => handleFieldChange('enabled', e.target.checked)}
                  className="rounded border-slate-700 bg-slate-800 text-sky-500 focus:ring-0"
                />
                <span>Включить отправку уведомления</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={config.allowLocal === true}
                  onChange={(e) => handleFieldChange('allowLocal', e.target.checked)}
                  className="rounded border-slate-700 bg-slate-800 text-sky-500 focus:ring-0"
                />
                <span>Разрешить локальный mock (127.0.0.1)</span>
              </label>
              <div>
                <label className="block text-[10px] text-slate-400 mb-1">URL локального mock:</label>
                <input
                  type="url"
                  value={config.webhookUrl || 'http://127.0.0.1:18080/api/webhook-test'}
                  onChange={(e) => handleFieldChange('webhookUrl', e.target.value)}
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-1 font-mono text-xs text-sky-300"
                />
                <p className="mt-1 text-[10px] text-slate-500">Только loopback при allowLocal; внешние https-адреса этим UI не разрешаются.</p>
              </div>

              <div>
                <label className="block text-[10px] text-slate-400 mb-1">Секретный токен подписи (HMAC):</label>
                <input
                  type="password"
                  value={config.secretToken || ''}
                  onChange={(e) => handleFieldChange('secretToken', e.target.value)}
                  placeholder="Опциональный ключ для X-Signature"
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-1 text-xs"
                />
              </div>

              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={config.sendOnFailOnly === true}
                  onChange={(e) => handleFieldChange('sendOnFailOnly', e.target.checked)}
                  className="rounded border-slate-700 bg-slate-800 text-sky-500 focus:ring-0"
                />
                <span>Отправлять только при SecurityVerdict=FAIL</span>
              </label>
            </div>
          )}
        </div>
      </div>

      {/* Footer */}
      <div className="p-3 border-t border-slate-800 bg-slate-900 flex justify-end">
        <button
          onClick={() => onDeleteNode(node.id)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-rose-950/60 hover:bg-rose-900 border border-rose-800/80 text-rose-300 text-xs font-medium transition cursor-pointer"
        >
          <Trash2 size={13} />
          <span>Удалить блок</span>
        </button>
      </div>
    </div>
  );
};
