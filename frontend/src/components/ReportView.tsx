import React, { useState } from 'react';
import {
  ShieldCheck, ShieldAlert, FileText, Download, Sparkles,
  CheckCircle2, AlertTriangle, HelpCircle, Layers, ArrowDownToLine,
  FileCode2, KeyRound, PackageSearch
} from 'lucide-react';
import { ReportData, SecurityVerdict, ExecutionStatus, NodeRunRecord } from '../types';
import { coverageLabel, getCanonicalCoverage } from '../coverage';
import { apiClient } from '../api/client';

interface ReportViewProps {
  report: ReportData | null;
  runId: string | null;
  runStatus?: ExecutionStatus;
  failureReason?: string;
  nodeRuns?: NodeRunRecord[];
}

export const ReportView: React.FC<ReportViewProps> = ({ report, runId, runStatus, failureReason, nodeRuns = [] }) => {
  const [activeSubTab, setActiveSubTab] = useState<'passport' | 'ai' | 'markdown'>('passport');

  if (!report || !runId) {
    const failed = runStatus === 'failed';
    const active = runStatus === 'queued' || runStatus === 'running';
    return (
      <div className="flex-1 h-full flex flex-col items-center justify-center text-slate-500 px-4 text-center">
        <FileText size={36} className="text-slate-600 mb-2" />
        <p className="text-sm">
          {failed
            ? 'Отчёт не сформирован: запуск завершился с ошибкой'
            : active
            ? 'Отчёт появится после завершения запуска'
            : runId
            ? 'Отчёт проверки ещё не сформирован'
            : 'Запуск не выбран'}
        </p>
        <p className="text-xs text-slate-600 max-w-md mt-1">
          {failed
            ? failureReason || 'Причина в ошибке выполнения узла. Открой «Живое выполнение» и посмотри статус блока.'
            : active
            ? 'Пока run в очереди или выполняется, report на сервере ещё не создан. Дождись terminal-статуса.'
            : 'Запустите сценарий для генерации отчёта и карты безопасности.'}
        </p>
      </div>
    );
  }

  const p = report.passport;
  const isFail = report.verdict === 'fail';
  const isPass = report.verdict === 'pass';

  return (
    <div className="flex-1 h-full flex flex-col overflow-hidden bg-slate-950">
      {/* Top Action Bar */}
      <div className="p-3 sm:p-4 bg-slate-900 border-b border-slate-800 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-3 min-w-0">
          <div
            className={`p-2.5 rounded-xl text-white shadow-lg ${
              isFail
                ? 'bg-rose-600 shadow-rose-600/20'
                : isPass
                ? 'bg-emerald-600 shadow-emerald-600/20'
                : 'bg-yellow-600 shadow-yellow-600/20'
            }`}
          >
            {isFail ? <ShieldAlert size={22} /> : isPass ? <ShieldCheck size={22} /> : <AlertTriangle size={22} />}
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-base font-bold text-slate-100">
                Итоговый вердикт:{' '}
                <span className={isFail ? 'text-rose-400' : isPass ? 'text-emerald-400' : 'text-yellow-400'}>
                  {report.verdict.toUpperCase()}
                </span>
              </span>
              <span className="text-xs font-mono text-slate-500">Запуск #{runId}</span>
            </div>
            <p className="text-xs text-slate-400">
              Покрытие: <strong className="text-indigo-400">{coverageLabel(getCanonicalCoverage(nodeRuns))}</strong> • Подтверждено находок: <strong className="text-rose-400">{p.confirmed_findings}</strong>
            </p>
          </div>
        </div>

        {/* Export Buttons */}
        <div className="flex flex-wrap items-center gap-2">
          <a
            href={apiClient.getOfficialJsonUrl(runId)}
            download
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-md shadow-indigo-600/20 transition cursor-pointer"
          >
            <Download size={14} />
            <span>Скачать официальный JSON</span>
          </a>

          <a
            href={apiClient.getReadableMarkdownUrl(runId)}
            download
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 text-xs font-medium transition cursor-pointer"
          >
            <ArrowDownToLine size={14} />
            <span>Скачать Markdown</span>
          </a>
        </div>
      </div>

      {/* Sub Tabs */}
      <div className="px-3 sm:px-4 pt-3 bg-slate-900/60 border-b border-slate-800 flex items-center gap-2 overflow-x-auto">
        <button
          onClick={() => setActiveSubTab('passport')}
          className={`px-3 py-1.5 rounded-t-lg text-xs font-medium transition border-b-2 cursor-pointer ${
            activeSubTab === 'passport'
              ? 'border-indigo-500 text-indigo-400 bg-slate-900'
              : 'border-transparent text-slate-400 hover:text-slate-200'
          }`}
        >
          1. Карта безопасности (Security Passport)
        </button>

        <button
          onClick={() => setActiveSubTab('ai')}
          className={`px-3 py-1.5 rounded-t-lg text-xs font-medium transition border-b-2 cursor-pointer flex items-center gap-1.5 ${
            activeSubTab === 'ai'
              ? 'border-fuchsia-500 text-fuchsia-400 bg-slate-900'
              : 'border-transparent text-slate-400 hover:text-slate-200'
          }`}
        >
          <Sparkles size={13} />
          <span>2. AI Trust Layer & Рекомендации</span>
        </button>

        <button
          onClick={() => setActiveSubTab('markdown')}
          className={`px-3 py-1.5 rounded-t-lg text-xs font-medium transition border-b-2 cursor-pointer ${
            activeSubTab === 'markdown'
              ? 'border-indigo-500 text-indigo-400 bg-slate-900'
              : 'border-transparent text-slate-400 hover:text-slate-200'
          }`}
        >
          3. Текстовый отчёт (Markdown)
        </button>
      </div>

      {/* Content Area */}
      <div className="flex-1 overflow-y-auto p-6">
        {activeSubTab === 'passport' && (
          <div className="max-w-5xl mx-auto space-y-6">
            {/* Passport Grid Cards */}
            <div>
              <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-3">
                Метрики покрытия и полноты анализа
              </h3>
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                <div className="p-3.5 rounded-xl bg-slate-900 border border-slate-800 space-y-1">
                  <div className="text-[11px] text-slate-400">Проверено файлов</div>
                  <div className="text-xl font-bold font-mono text-slate-100">{p.files_scanned}</div>
                  <div className="text-[10px] text-slate-500">Пропущено: {p.files_skipped}</div>
                </div>

                <div className="p-3.5 rounded-xl bg-slate-900 border border-slate-800 space-y-1">
                  <div className="text-[11px] text-slate-400">Строк кода (LOC)</div>
                  <div className="text-xl font-bold font-mono text-indigo-400">{p.lines_of_code}</div>
                  <div className="text-[10px] text-slate-500">Kotlin + Gradle DSL</div>
                </div>

                <div className="p-3.5 rounded-xl bg-slate-900 border border-slate-800 space-y-1">
                  <div className="text-[11px] text-slate-400">Активных правил</div>
                  <div className="text-xl font-bold font-mono text-teal-400">{p.rules_executed.length}</div>
                  <div className="text-[10px] text-slate-500">Secret, SCA, SAST</div>
                </div>

                <div className="p-3.5 rounded-xl bg-slate-900 border border-slate-800 space-y-1">
                  <div className="text-[11px] text-slate-400">Покрытие</div>
                  <div className="text-xl font-bold font-mono text-emerald-400">
                    {coverageLabel(getCanonicalCoverage(nodeRuns))}
                  </div>
                  <div className="text-[10px] text-slate-500">
                    Ошибок парсера: {p.parser_errors}
                  </div>
                </div>
              </div>
            </div>

            {/* Findings Breakdown */}
            <div>
              <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-3">
                Распределение находок по статусам доказанности
              </h3>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div className="p-3.5 rounded-xl bg-rose-950/40 border border-rose-800/60 space-y-1">
                  <div className="text-[11px] text-rose-300">Подтвержденные (Confirmed)</div>
                  <div className="text-2xl font-bold font-mono text-rose-200">{p.confirmed_findings}</div>
                  <div className="text-[10px] text-rose-400/80">Доказанные taint-пути и строковые литералы</div>
                </div>

                <div className="p-3.5 rounded-xl bg-yellow-950/40 border border-yellow-800/60 space-y-1">
                  <div className="text-[11px] text-yellow-300">Потенциальные (Potential)</div>
                  <div className="text-2xl font-bold font-mono text-yellow-200">{p.potential_findings}</div>
                  <div className="text-[10px] text-yellow-400/80">Требуют контекста сервисного слоя</div>
                </div>

                <div className="p-3.5 rounded-xl bg-slate-900 border border-slate-800 space-y-1">
                  <div className="text-[11px] text-slate-400">Неопределенные (Inconclusive)</div>
                  <div className="text-2xl font-bold font-mono text-slate-300">{p.inconclusive_findings}</div>
                  <div className="text-[10px] text-slate-500">Неполный разбор или динамические зависимости</div>
                </div>
              </div>
            </div>

            {/* Coverage Warnings */}
            {p.coverage_warnings && p.coverage_warnings.length > 0 && (
              <div className="p-4 rounded-xl bg-yellow-950/30 border border-yellow-800/50 space-y-2">
                <div className="flex items-center gap-2 text-xs font-bold text-yellow-300">
                  <AlertTriangle size={15} />
                  <span>Предупреждения полноты покрытия (Coverage Warnings):</span>
                </div>
                <ul className="list-disc list-inside space-y-1 text-xs text-yellow-200/90">
                  {p.coverage_warnings.map((w, idx) => (
                    <li key={idx}>{w}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {activeSubTab === 'ai' && (
          <div className="max-w-5xl mx-auto space-y-6">
            {/* AI Status Badge */}
            <div className="p-4 rounded-xl bg-fuchsia-950/40 border border-fuchsia-800/60 space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 text-sm font-bold text-fuchsia-200">
                  <Sparkles size={16} />
                  <span>AI Trust Layer: Нейросеть под программным присмотром</span>
                </div>
                <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-fuchsia-900 text-fuchsia-200 border border-fuchsia-700 uppercase">
                  Статус: {report.ai_status}
                </span>
              </div>
              <p className="text-xs text-slate-300 leading-relaxed">
                {report.ai_summary}
              </p>
            </div>

            {/* Trust Layer Principles Breakdown */}
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3 text-xs">
              <div className="p-3 rounded-xl bg-slate-900 border border-emerald-800/50 space-y-1">
                <div className="font-bold text-emerald-400 flex items-center gap-1.5">
                  <CheckCircle2 size={14} />
                  <span>1. ФАКТ</span>
                </div>
                <p className="text-[11px] text-slate-400">
                  Находки обнаружены исключительно нашими собственными детерминированными сканерами.
                </p>
              </div>

              <div className="p-3 rounded-xl bg-slate-900 border border-blue-800/50 space-y-1">
                <div className="font-bold text-blue-400 flex items-center gap-1.5">
                  <Sparkles size={14} />
                  <span>2. ОБЪЯСНЕНИЕ</span>
                </div>
                <p className="text-[11px] text-slate-400">
                  Модель переводит технические факты на человеческий язык и предлагает патчи.
                </p>
              </div>

              <div className="p-3 rounded-xl bg-slate-900 border border-purple-800/50 space-y-1">
                <div className="font-bold text-purple-400 flex items-center gap-1.5">
                  <ShieldCheck size={14} />
                  <span>3. ВАЛИДАЦИЯ</span>
                </div>
                <p className="text-[11px] text-slate-400">
                  Программный валидатор проверяет каждый findingId и CVE по официальному каталогу.
                </p>
              </div>

              <div className="p-3 rounded-xl bg-slate-900 border border-yellow-800/50 space-y-1">
                <div className="font-bold text-yellow-400 flex items-center gap-1.5">
                  <AlertTriangle size={14} />
                  <span>4. НЕПОЛНОТА</span>
                </div>
                <p className="text-[11px] text-slate-400">
                  Ошибки парсера и неизвестные версии отображаются явно и не маскируются.
                </p>
              </div>
            </div>

            {/* AI Items List */}
            <div className="space-y-3">
              <h4 className="text-xs font-bold text-slate-400 uppercase tracking-wider">
                Поэлементные объяснения и исправления ({report.ai_items.length})
              </h4>
              {report.ai_items.map((item, idx) => (
                <div key={idx} className="p-4 rounded-xl bg-slate-900 border border-slate-800 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-xs font-bold text-indigo-400">
                      ID: {item.findingId}
                    </span>
                    {item.suggestedFixedVersion && (
                      <span className="text-[10px] font-mono font-semibold text-emerald-400 bg-emerald-950 px-2 py-0.5 rounded border border-emerald-800">
                        Фикс-версия: {item.suggestedFixedVersion}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-slate-300 leading-relaxed">
                    {item.explanation}
                  </p>
                  <div className="p-2.5 rounded-lg bg-slate-950 border border-slate-800 font-mono text-xs text-fuchsia-300">
                    <strong className="text-slate-400 font-sans text-[11px]">Рекомендация: </strong>
                    {item.remediation}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {activeSubTab === 'markdown' && (
          <div className="max-w-4xl mx-auto">
            <pre className="p-4 rounded-xl bg-slate-900 border border-slate-800 text-xs font-mono text-slate-300 whitespace-pre-wrap leading-relaxed">
              {report.readable_markdown}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
};
