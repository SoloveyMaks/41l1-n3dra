import React, { useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, ArrowLeftRight, GitCompare, RefreshCw, ShieldAlert } from 'lucide-react';
import {
  ArtifactManifest,
  ComparisonDto,
  ComparisonItem,
  ComparisonStatus,
  RunSummary,
  SqlPatternResult,
} from '../types';
import { apiClient, ApiError } from '../api/client';
import { getRunSecurityMap } from '../api/securityMap';
import type { Finding as CanonicalFinding } from '../../../docs/data-contracts/contracts';

const STATUS_LABEL: Record<ComparisonStatus, string> = {
  persistent: 'Осталось',
  introduced: 'Появилось',
  changed: 'Изменилось',
  resolved: 'Исчезло при сопоставимой проверке',
  inconclusive: 'Сравнить нельзя',
  file_deleted: 'Файл удалён',
};

const STATUS_HINT: Record<ComparisonStatus, string> = {
  persistent: 'Находка есть и в старом, и в новом снимке.',
  introduced: 'В старом снимке находки не было — она появилась в новом при полной проверке.',
  changed: 'Находка осталась, но её характеристики изменились.',
  resolved: 'В новом снимке находки нет, а файл и правило проверены полностью. Это исчезновение находки, а не доказанное исправление.',
  inconclusive: 'Сопоставить нельзя: не хватает доказательств или связь неоднозначна.',
  file_deleted: 'Файл исчез из нового снимка. Удаление — не доказательство исправления.',
};

const SQL_STATUS_LABEL: Record<string, string> = {
  persistent: 'Паттерн сохраняется',
  changed: 'Паттерн изменился',
  pattern_disappeared: 'Паттерн исчез при сопоставимой проверке',
  inconclusive: 'Сравнить нельзя: доказательств недостаточно',
  file_deleted: 'Файл удалён — это не исправление',
};

const SQL_STATUS_HINT: Record<string, string> = {
  persistent: 'Тот же SQL-паттерн остался в новом снимке.',
  changed: 'SQL-паттерн остался, но его содержимое изменилось.',
  pattern_disappeared: 'В новом снимке паттерн не найден, а файл проверен полностью. Это исчезновение паттерна, не подтверждённое исправление.',
  inconclusive: 'Проверка неполная или неоднозначная — вывода об изменении нет.',
  file_deleted: 'Файл исчез из нового снимка. Это не доказательство исправления.',
};

const BASIS_LABEL: Record<ComparisonItem['matchingBasis'], string> = {
  unique_anchor: 'Однозначно',
  ambiguous: 'Неуверенно',
  none: 'Без сопоставления',
};

const STATUS_CLASS: Record<ComparisonStatus, string> = {
  persistent: 'bg-amber-950 text-amber-300 border-amber-800',
  introduced: 'bg-rose-950 text-rose-300 border-rose-800',
  changed: 'bg-sky-950 text-sky-300 border-sky-800',
  resolved: 'bg-cyan-950 text-cyan-200 border-cyan-800',
  inconclusive: 'bg-slate-800 text-slate-300 border-slate-700',
  file_deleted: 'bg-fuchsia-950 text-fuchsia-300 border-fuchsia-800',
};

const SEVERITY_LABEL: Record<string, string> = {
  critical: 'Критично',
  high: 'Высокий',
  medium: 'Средний',
  low: 'Низкий',
  info: 'Инфо',
};

const SEVERITY_CLASS: Record<string, string> = {
  critical: 'bg-rose-950 text-rose-300 border-rose-800',
  high: 'bg-orange-950 text-orange-300 border-orange-800',
  medium: 'bg-yellow-950 text-yellow-200 border-yellow-800',
  low: 'bg-sky-950 text-sky-300 border-sky-800',
  info: 'bg-slate-800 text-slate-300 border-slate-700',
};

const CATEGORY_LABEL: Record<string, string> = {
  SECRET: 'Секреты',
  SCA: 'Зависимости',
  SAST: 'Код',
};

const MAIN_STATUS_ORDER: ComparisonStatus[] = [
  'persistent',
  'introduced',
  'changed',
  'resolved',
  'inconclusive',
];

const COVERAGE_REASON = /покрыти|checked|не завершён|неполн|пропуск|лимит|parser|unresolved|граф|manifest/i;

const unique = <T,>(values: T[]): T[] => [...new Set(values)];

type FindingContext = {
  id: string;
  ruleId: string;
  severity: string;
  category: string;
  path: string;
  line: number;
};

type RunContext =
  | { status: 'loading' }
  | { status: 'ready'; findings: Map<string, FindingContext> }
  | { status: 'unavailable'; message: string };

type ContextByRun = Record<string, RunContext>;

type CanonicalLookup = {
  baselineRunId: string;
  currentRunId: string;
  contexts: ContextByRun;
};

const toFindingContext = (finding: CanonicalFinding): FindingContext => ({
  id: finding.id,
  ruleId: finding.ruleId,
  severity: finding.severity,
  category: finding.category,
  path: finding.primaryLocation.path,
  line: finding.primaryLocation.line,
});

const findingFromContext = (
  lookup: CanonicalLookup,
  runId: string,
  findingId: string | null,
): FindingContext | undefined => {
  if (!findingId) return undefined;
  const context = lookup.contexts[runId];
  return context?.status === 'ready' ? context.findings.get(findingId) : undefined;
};

const missingContextNote = (
  lookup: CanonicalLookup,
  runId: string,
  findingId: string | null,
): string | null => {
  if (!findingId) return null;
  const context = lookup.contexts[runId];
  if (!context || context.status === 'loading') return 'Загружаем каноническую находку…';
  if (context.status === 'unavailable') return 'Канонический контекст недоступен';
  return 'Каноническая находка не найдена в published envelope';
};

const humanReason = (reason: string): string => {
  if (/Различаются статические версии\/hashes/i.test(reason)) return 'Разные версии проверок или их настроек.';
  if (/Нужны published envelopes/i.test(reason)) return 'Снимки опубликованы не полностью — сопоставимость не подтверждена.';
  if (/Различаются версии нормализации/i.test(reason)) return 'Разные версии нормализации находок.';
  if (/Ручная fixture|разные источники/i.test(reason)) return 'Синтетические данные или разные источники — это не две сопоставимые проверки.';
  if (/не подтвердили полноту ожидаемых scopes/i.test(reason)) return 'Не подтверждена полнота проверки ожидаемых файлов.';
  if (/domain policy/i.test(reason)) return 'Не подтверждена применимость политики к проекту.';
  if (/contract scenario/i.test(reason)) return 'Синтетический сценарий, а не настоящий скан.';
  if (/ruleVersion\/ruleFamily/i.test(reason)) return 'Разные версии правил для одной категории находок.';
  return 'Условия проверок различаются.';
};

const describeCompareError = (error: unknown): { message: string; technical: string | null } => {
  const message = error instanceof Error ? error.message : String(error);
  const code = error instanceof ApiError ? error.code || null : null;
  if (code === 'SAME_RUN' || /same run|same_run|одна и та же проверка/i.test(message)) {
    return { message: 'Нельзя сравнить проверку саму с собой. Выберите две разные проверки.', technical: null };
  }
  if (/same snapshot|same_snapshot|один и тот же снимок|runs\/snapshots must be distinct|snapshots must be distinct/i.test(message)) {
    return { message: 'Было и Стало — один и тот же снимок проекта. Выберите проверки разных снимков.', technical: null };
  }
  const byCode: Record<string, string> = {
    SNAPSHOT_UNKNOWN: 'Не найден снимок проекта для одной из проверок.',
    NOT_PUBLISHED: 'Проверка не опубликована полностью — сравнивать нечего.',
    PUBLISHED_HASH_MISSING: 'Не хватает подтверждения опубликованных данных проверки.',
    CANONICAL_INCOMPLETE: 'Опубликованные данные проверки неполные.',
    COMPARISON_TIMEOUT: 'Сравнение не уложилось в лимит времени. Повторите позже.',
    COMPARISON_BUSY: 'Сравнение уже выполняется. Повторите через несколько секунд.',
    COMPARISON_CLI_MISSING: 'Сервис сравнения недоступен на сервере.',
    NODE_MISSING: 'На сервере нет среды для сравнения.',
    COMPARISON_OUTPUT_TOO_LARGE: 'Сравнение вернуло слишком большой результат.',
    COMPARISON_BAD_OUTPUT: 'Сервис сравнения вернул некорректный результат.',
    COMPARISON_STORE_UNAVAILABLE: 'Хранилище сравнений недоступно.',
    COMPARISON_CONFLICT: 'Сравнение конфликтует с уже сохранённым результатом.',
  };
  if (code && byCode[code]) return { message: byCode[code], technical: message || null };
  if (code === 'COMPARISON_REJECTED') {
    return {
      message: 'Сравнение отклонено: проверки не удалось привести к общим условиям. Это не значит, что снимки одинаковые.',
      technical: message || null,
    };
  }
  return { message: message || 'Не удалось сравнить проверки', technical: null };
};

const itemMeaning = (item: ComparisonItem): string => {
  switch (item.status) {
    case 'persistent':
      return 'Находка есть в обоих снимках.';
    case 'introduced':
      return 'Находка появилась в новом снимке при полной проверке.';
    case 'changed':
      return 'Находка осталась, но её характеристики изменились.';
    case 'resolved':
      return 'В новом снимке находки нет, файл и правило проверены полностью. Это исчезновение находки, а не доказанное исправление.';
    case 'file_deleted':
      return 'Файл исчез из нового снимка. Удаление — не доказательство исправления.';
    case 'inconclusive':
    default:
      if (/Несколько находок|Повторяющийся|Fingerprint повторяется/i.test(item.reason)) {
        return 'Похожих находок несколько; сопоставить их однозначно нельзя.';
      }
      if (/symbolKey|переименован/i.test(item.reason)) {
        return 'Возможно, символ переименован; без карты переименований вывод сделать нельзя.';
      }
      if (/перенос/i.test(item.reason)) {
        return 'Находка могла измениться или переехать в другой символ; доказательств переноса нет.';
      }
      if (/На том же|resolved запрещён/i.test(item.reason)) {
        return 'На том же месте остаётся похожая находка; исчезновением это не считается.';
      }
      if (/появление не доказывает/i.test(item.reason)) {
        return 'Находка есть в новом снимке, но условия не позволяют считать её новой.';
      }
      if (/исчезновение не доказывает/i.test(item.reason)) {
        return 'Находки нет в новом снимке, но условия не позволяют считать её исчезнувшей.';
      }
      if (/Основания анализа несовместимы/i.test(item.reason)) {
        return 'Условия проверок не совпадают; сравнивать нельзя.';
      }
      return 'Сопоставить нельзя: доказательств недостаточно.';
  }
};

const coverageSide = (reason: string): string => {
  const before = /(^|; )До:/.test(reason);
  const after = /(^|; )После:/.test(reason);
  if (before && after) return 'Старый и новый снимок';
  if (before) return 'Старый снимок';
  if (after) return 'Новый снимок';
  return 'Оба снимка';
};

const coverageCauses = (reason: string): string[] => {
  // The engine's generic sentence lists examples of gaps, not actual gaps.
  // Only the explicit detail segments may become a factual cause.
  const explicit = reason
    .split(/;\s*/)
    .filter((part) => !/не доказывают отсутствие паттерна/i.test(part))
    .join('; ');
  const causes: string[] = [];
  if (/покрытие графа неполное/i.test(explicit)) causes.push('граф связей проверен не полностью');
  if (/нет checked/i.test(explicit)) causes.push('файл и правило не отмечены как полностью проверенные');
  if (/не завершён полностью/i.test(explicit)) causes.push('анализ категории неполный');
  if (/предупреждения о покрытии/i.test(explicit)) causes.push('у находки есть предупреждения о покрытии');
  if (/manifest/i.test(explicit)) causes.push('нет полного снимка проекта с обеих сторон');
  if (/пропуск|лимит|limit/i.test(explicit)) causes.push('в проверке были пропуски или лимиты');
  if (/parser|ошибк[аи] разбора/i.test(explicit)) causes.push('были ошибки разбора');
  return causes.length ? unique(causes) : ['проверка покрытия неполная'];
};

const verdictRu = (run: RunSummary): string => {
  if (run.security_verdict === 'fail') return 'Найдены проблемы';
  if (run.security_verdict === 'pass') return 'Нет блокирующих проблем';
  if (run.security_verdict === 'inconclusive') return 'Неполная проверка';
  return 'Проверка идёт';
};

const dateRu = (value: string): string => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
};

interface ComparisonViewProps {
  runs: RunSummary[];
  artifacts: ArtifactManifest[];
  workflows: Array<{ id: string; name?: string }>;
  onRefreshRuns?: () => void;
}

const TechnicalDetails: React.FC<{ text: string; label?: string }> = ({ text, label = 'Техническая формулировка' }) => (
  <details className="mt-1 text-[10px] text-slate-500">
    <summary className="cursor-pointer select-none">{label}</summary>
    <p className="mt-1 whitespace-pre-wrap break-words">{text}</p>
  </details>
);

const FindingCell: React.FC<{
  lookup: CanonicalLookup;
  baselineFindingId: string | null;
  currentFindingId: string | null;
}> = ({ lookup, baselineFindingId, currentFindingId }) => {
  const baseline = findingFromContext(lookup, lookup.baselineRunId, baselineFindingId);
  const current = findingFromContext(lookup, lookup.currentRunId, currentFindingId);
  const primary = current ?? baseline;
  const notes = unique(
    [
      missingContextNote(lookup, lookup.baselineRunId, baselineFindingId),
      missingContextNote(lookup, lookup.currentRunId, currentFindingId),
    ].filter((note): note is string => Boolean(note)),
  );
  if (!primary) {
    return (
      <div className="space-y-1 text-[11px] text-slate-500">
        {notes.length > 0
          ? notes.map((note) => <div key={note}>{note}</div>)
          : <div>Без привязки к находке</div>}
      </div>
    );
  }
  const showBaseline = Boolean(
    baseline &&
    current &&
    (baseline.ruleId !== current.ruleId || baseline.path !== current.path),
  );
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-medium text-slate-100">{primary.ruleId}</span>
        <span className={`inline-block rounded border px-1.5 py-0.5 text-[10px] font-semibold ${SEVERITY_CLASS[primary.severity] || SEVERITY_CLASS.info}`}>
          {SEVERITY_LABEL[primary.severity] || primary.severity}
        </span>
        <span className="text-[10px] text-slate-500">{CATEGORY_LABEL[primary.category] || primary.category}</span>
      </div>
      <div className="font-mono text-[10px] break-all text-indigo-300">
        {primary.path}:{primary.line}
      </div>
      {showBaseline && baseline && (
        <div className="font-mono text-[10px] break-all text-slate-500">
          Было: {baseline.ruleId} · {baseline.path}:{baseline.line}
        </div>
      )}
      {notes.length > 0 && (
        <div className="space-y-0.5 text-[10px] text-amber-400/80">
          {notes.map((note) => <div key={note}>{note}</div>)}
        </div>
      )}
    </div>
  );
};

const ItemRow: React.FC<{ item: ComparisonItem; lookup: CanonicalLookup }> = ({ item, lookup }) => (
  <tr className="align-top">
    <td className="min-w-[220px] px-3 py-2">
      <FindingCell
        lookup={lookup}
        baselineFindingId={item.baselineFindingId}
        currentFindingId={item.currentFindingId}
      />
    </td>
    <td className="px-3 py-2 text-slate-300">
      <p>{itemMeaning(item)}</p>
      <TechnicalDetails text={item.reason} />
    </td>
    <td className="whitespace-nowrap px-3 py-2 text-slate-400">{BASIS_LABEL[item.matchingBasis]}</td>
  </tr>
);

const StatusGroup: React.FC<{ status: ComparisonStatus; items: ComparisonItem[]; lookup: CanonicalLookup }> = ({
  status,
  items,
  lookup,
}) => {
  if (!items.length) return null;
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/40">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-800 px-3 py-2">
        <span className={`inline-block rounded border px-1.5 py-0.5 text-[10px] font-semibold ${STATUS_CLASS[status]}`}>
          {STATUS_LABEL[status]}
        </span>
        <span className="text-[11px] text-slate-400">{STATUS_HINT[status]}</span>
        <span className="ml-auto text-[11px] text-slate-500">{items.length}</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-left text-xs">
          <thead className="bg-slate-900 text-slate-400">
            <tr>
              <th className="px-3 py-2">Находка</th>
              <th className="px-3 py-2">Что это значит</th>
              <th className="px-3 py-2">Как сопоставлено</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {items.map((item) => (
              <ItemRow key={item.id} item={item} lookup={lookup} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

const CoverageSection: React.FC<{ items: ComparisonItem[]; lookup: CanonicalLookup }> = ({ items, lookup }) => {
  if (!items.length) return null;
  return (
    <div className="rounded-xl border border-amber-900 bg-amber-950/20">
      <div className="flex flex-wrap items-center gap-2 border-b border-amber-900/60 px-3 py-2">
        <span className="inline-block rounded border border-amber-800 bg-amber-950 px-1.5 py-0.5 text-[10px] font-semibold text-amber-300">
          Потеря покрытия
        </span>
        <span className="text-[11px] text-amber-200/80">
          Проверка не завершена полностью. Отсутствие находки при таком покрытии ничего не доказывает.
        </span>
        <span className="ml-auto text-[11px] text-amber-300/70">{items.length}</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-left text-xs">
          <thead className="bg-amber-950/40 text-amber-200/70">
            <tr>
              <th className="px-3 py-2">Находка</th>
              <th className="px-3 py-2">Где неполно</th>
              <th className="px-3 py-2">Что именно</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-amber-900/40">
            {items.map((item) => {
              const causes = coverageCauses(item.reason);
              return (
                <tr key={item.id} className="align-top">
                  <td className="min-w-[220px] px-3 py-2">
                    <FindingCell
                      lookup={lookup}
                      baselineFindingId={item.baselineFindingId}
                      currentFindingId={item.currentFindingId}
                    />
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-amber-100/80">{coverageSide(item.reason)}</td>
                  <td className="px-3 py-2 text-amber-100/80">
                    <ul className="list-disc space-y-0.5 pl-4">
                      {causes.map((cause) => <li key={cause}>{cause}</li>)}
                    </ul>
                    <TechnicalDetails text={item.reason} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};

const DeletedSection: React.FC<{
  deletedFiles: ComparisonDto['deletedFiles'];
  items: ComparisonItem[];
  lookup: CanonicalLookup;
}> = ({ deletedFiles, items, lookup }) => {
  const baselineContext = lookup.contexts[lookup.baselineRunId];
  const baselineFindings = baselineContext?.status === 'ready' ? baselineContext.findings : null;

  const { entries, unmatched } = useMemo(() => {
    const byPath = new Map<string, ComparisonItem[]>();
    deletedFiles.forEach((file) => byPath.set(file.path, []));
    const unmatchedItems: ComparisonItem[] = [];
    items.forEach((item) => {
      const finding = item.baselineFindingId && baselineFindings
        ? baselineFindings.get(item.baselineFindingId)
        : undefined;
      const path = finding?.path;
      if (!path) {
        unmatchedItems.push(item);
        return;
      }
      if (!byPath.has(path)) byPath.set(path, []);
      byPath.get(path)!.push(item);
    });
    return {
      entries: [...byPath.entries()].sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0)),
      unmatched: unmatchedItems,
    };
  }, [baselineFindings, deletedFiles, items]);

  const findingsByPath = useMemo(() => {
    const counts = new Map<string, number>();
    baselineFindings?.forEach((finding) => {
      counts.set(finding.path, (counts.get(finding.path) ?? 0) + 1);
    });
    return counts;
  }, [baselineFindings]);

  const emptyMessage = (path: string): string => {
    if (!baselineContext || baselineContext.status === 'loading') {
      return 'Загружаем канонические находки: пока нельзя сказать, были ли в файле находки.';
    }
    if (baselineContext.status === 'unavailable') {
      return 'Канонические находки недоступны: нельзя утверждать, что находок в файле не было.';
    }
    if ((findingsByPath.get(path) ?? 0) > 0) {
      return 'В старом снимке в этом файле были находки, но движок не связал их с удалением файла.';
    }
    if (unmatched.length > 0) {
      return 'Связь с находками не подтверждена: есть удалённые пункты без сопоставленного пути.';
    }
    return 'Сравнение не привязало к этому файлу ни одной находки.';
  };

  const unmappedMessage = (item: ComparisonItem): string => {
    if (!item.baselineFindingId) return 'Движок пометил файл удалённым без ссылки на находку.';
    if (!baselineContext || baselineContext.status === 'loading') {
      return 'Каноническая находка ещё загружается: связь с файлом пока не определена.';
    }
    if (baselineContext.status === 'unavailable') {
      return 'Канонический контекст недоступен: это не значит, что находок не было.';
    }
    return 'Каноническая находка не найдена в published envelope: это не означает отсутствие находки.';
  };

  if (!entries.length && !unmatched.length) return null;
  return (
    <div className="rounded-xl border border-fuchsia-900 bg-fuchsia-950/20">
      <div className="flex flex-wrap items-center gap-2 border-b border-fuchsia-900/60 px-3 py-2">
        <span className="flex items-center gap-1.5 text-xs font-semibold text-fuchsia-200">
          <ShieldAlert size={15} /> Удалённые файлы
        </span>
        <span className="text-[11px] text-fuchsia-100/70">
          Это не исправление: файл могли удалить, переименовать или исключить из проверки.
        </span>
        <span className="ml-auto text-[11px] text-fuchsia-300/70">{entries.length + unmatched.length}</span>
      </div>
      <ul className="max-h-64 space-y-1 overflow-y-auto p-3 text-xs">
        {entries.map(([path, affected]) => (
          <li key={`file:${path}`} className="rounded-lg border border-fuchsia-900/40 bg-slate-950/40 p-2">
            <code className="break-all text-fuchsia-200">{path}</code>
            {affected.length > 0 ? (
              <div className="mt-1 space-y-2">
                {affected.map((item) => (
                  <div key={item.id} className="rounded border border-fuchsia-900/30 bg-slate-950/30 p-2">
                    <FindingCell
                      lookup={lookup}
                      baselineFindingId={item.baselineFindingId}
                      currentFindingId={item.currentFindingId}
                    />
                    <div className="mt-1 text-[11px] text-fuchsia-100/70">Файл удалён — это не исправление.</div>
                    <TechnicalDetails text={item.reason} />
                  </div>
                ))}
              </div>
            ) : (
              <div className="mt-1 text-[11px] text-fuchsia-100/60">{emptyMessage(path)}</div>
            )}
          </li>
        ))}
        {unmatched.map((item) => (
          <li key={`item:${item.id}`} className="rounded-lg border border-fuchsia-900/40 bg-slate-950/40 p-2">
            <div className="font-medium text-fuchsia-200">Файл удалён, путь находки недоступен</div>
            <div className="mt-1 text-[11px] text-fuchsia-100/60">{unmappedMessage(item)}</div>
            <TechnicalDetails text={item.reason} />
          </li>
        ))}
      </ul>
    </div>
  );
};

export const ComparisonView: React.FC<ComparisonViewProps> = ({ runs, artifacts, workflows, onRefreshRuns }) => {
  const finished = useMemo(
    () => runs.filter((run) => run.execution_status === 'succeeded' || run.execution_status === 'failed'),
    [runs],
  );
  const [baseline, setBaseline] = useState('');
  const [current, setCurrent] = useState('');
  const [result, setResult] = useState<ComparisonDto | null>(null);
  const [runContexts, setRunContexts] = useState<ContextByRun>({});
  const [error, setError] = useState<string | null>(null);
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const [errorTechnical, setErrorTechnical] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const requestSeq = useRef(0);
  const [sqlResult, setSqlResult] = useState<SqlPatternResult | null>(null);
  const [sqlError, setSqlError] = useState<string | null>(null);
  const [sqlBusy, setSqlBusy] = useState(false);

  const clearComparison = () => {
    setResult(null);
    setRunContexts({});
    setError(null);
    setErrorCode(null);
    setErrorTechnical(null);
    setSqlResult(null);
    setSqlError(null);
  };

  const changeBaseline = (value: string) => {
    requestSeq.current += 1;
    setBaseline(value);
    clearComparison();
    setBusy(false);
    setSqlBusy(false);
  };
  const changeCurrent = (value: string) => {
    requestSeq.current += 1;
    setCurrent(value);
    clearComparison();
    setBusy(false);
    setSqlBusy(false);
  };

  useEffect(() => {
    if (finished.length >= 2 && (!baseline || !current)) {
      setCurrent(finished[0]?.id || '');
      setBaseline(finished[1]?.id || '');
    }
  }, [finished, baseline, current]);

  // Comparison items reference canonical bundle finding UUIDs (finding_uuid),
  // while /runs/{id}/findings returns runtime fnd_* records. Only the
  // security-map findings come from the same published envelope as comparison.
  useEffect(() => {
    if (!result) {
      setRunContexts({});
      return;
    }
    let cancelled = false;
    const runIds = unique([result.baselineRunId, result.currentRunId]);
    const initial: ContextByRun = {};
    runIds.forEach((runId) => {
      initial[runId] = { status: 'loading' };
    });
    setRunContexts(initial);
    runIds.forEach((runId) => {
      void getRunSecurityMap(runId)
        .then((payload) => {
          if (cancelled) return;
          const findings = new Map(payload.findings.map((finding) => [finding.id, toFindingContext(finding)]));
          setRunContexts((previous) => ({
            ...previous,
            [runId]: { status: 'ready', findings },
          }));
        })
        .catch((error: unknown) => {
          if (cancelled) return;
          setRunContexts((previous) => ({
            ...previous,
            [runId]: {
              status: 'unavailable',
              message: error instanceof Error ? error.message : 'Канонические находки недоступны',
            },
          }));
        });
    });
    return () => {
      cancelled = true;
    };
  }, [result]);

  const canCompare = Boolean(baseline && current && baseline !== current && !busy);
  const sameSelection = Boolean(baseline && current && baseline === current);

  const swap = () => {
    const nextBaseline = current;
    const nextCurrent = baseline;
    changeBaseline(nextBaseline);
    changeCurrent(nextCurrent);
  };

  const compare = async () => {
    if (!canCompare) {
      setError(sameSelection ? 'Было и Стало должны быть разными запусками' : 'Выберите два завершённых опубликованных запуска');
      return;
    }
    const seq = ++requestSeq.current;
    setBusy(true);
    clearComparison();
    try {
      const next = await apiClient.compareRuns(baseline, current);
      if (seq === requestSeq.current) {
        setResult(next);
        setSqlBusy(true);
        try {
          const sql = await apiClient.getSqlPattern(baseline, current);
          if (seq === requestSeq.current) setSqlResult(sql);
        } catch (sqlErr: unknown) {
          if (seq === requestSeq.current) {
            setSqlError(sqlErr instanceof Error && sqlErr.message ? sqlErr.message : 'Проверка SQL-паттерна недоступна');
          }
        } finally {
          if (seq === requestSeq.current) setSqlBusy(false);
        }
      }
    } catch (e: unknown) {
      if (seq === requestSeq.current) {
        const described = describeCompareError(e);
        setResult(null);
        setError(described.message);
        setErrorCode(e instanceof ApiError ? e.code || null : null);
        setErrorTechnical(described.technical);
      }
    } finally {
      if (seq === requestSeq.current) setBusy(false);
    }
  };

  const select = (value: string, onChange: (value: string) => void, label: string) => (
    <label className="block text-xs text-slate-400">
      <span className="mb-1 block">{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-lg bg-slate-950 border border-slate-700 px-3 py-2 text-xs text-slate-200"
      >
        <option value="">Выберите запуск</option>
        {finished.map((run) => (
          <option key={run.id} value={run.id}>
            {artifacts.find((artifact) => artifact.id === run.artifact_id)?.filename || 'Проект не указан'} · {dateRu(run.created_at)} · {workflows.find((workflow) => workflow.id === run.workflow_id)?.name || 'Сценарий проверки'} · {verdictRu(run)}
          </option>
        ))}
      </select>
    </label>
  );

  const mainGroups = useMemo(() => {
    const groups: Record<ComparisonStatus, ComparisonItem[]> = {
      persistent: [],
      introduced: [],
      changed: [],
      resolved: [],
      inconclusive: [],
      file_deleted: [],
    };
    result?.items.forEach((item) => groups[item.status].push(item));
    return groups;
  }, [result]);

  const coverageItems = useMemo(
    () => mainGroups.inconclusive.filter((item) => COVERAGE_REASON.test(item.reason)),
    [mainGroups],
  );
  const inconclusiveItems = useMemo(
    () => mainGroups.inconclusive.filter((item) => !COVERAGE_REASON.test(item.reason)),
    [mainGroups],
  );

  const artifactNameForRun = (runId: string): string => {
    const run = runs.find((item) => item.id === runId);
    return artifacts.find((artifact) => artifact.id === run?.artifact_id)?.filename || 'проект не указан';
  };

  const resultReasons = useMemo(
    () => (result ? unique(result.reasons.map(humanReason)) : []),
    [result],
  );

  const lookup: CanonicalLookup = {
    baselineRunId: result?.baselineRunId ?? '',
    currentRunId: result?.currentRunId ?? '',
    contexts: runContexts,
  };

  const contextUnavailable = useMemo(() => {
    if (!result) return false;
    return unique([result.baselineRunId, result.currentRunId])
      .some((runId) => runContexts[runId]?.status === 'unavailable');
  }, [result, runContexts]);

  return (
    <section className="flex-1 overflow-auto p-6 bg-slate-950">
      <div className="mx-auto max-w-5xl space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <GitCompare size={18} className="text-indigo-400" />
            <h2 className="text-sm font-semibold text-white">Сравнение опубликованных запусков</h2>
          </div>
          {onRefreshRuns && (
            <button
              onClick={onRefreshRuns}
              className="flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800 px-2.5 py-1.5 text-[11px] text-slate-300 hover:bg-slate-700"
            >
              <RefreshCw size={13} /> Обновить историю
            </button>
          )}
        </div>
        <p className="text-xs text-slate-400">
          Сравнение не запускает сканеры. «Исчезло при сопоставимой проверке» — это отсутствие находки при полной проверке,
          а не подтверждённое исправление. Потеря покрытия и удалённые файлы показаны отдельно.
        </p>

        <div className="grid gap-3 rounded-xl border border-slate-800 bg-slate-900/70 p-4 md:grid-cols-[1fr_1fr_auto] md:items-end">
          {select(baseline, changeBaseline, 'Было (до)')}
          {select(current, changeCurrent, 'Стало (после)')}
          <div className="flex flex-wrap items-center gap-2 md:flex-col">
            <button
              onClick={swap}
              disabled={!baseline || !current}
              className="flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-xs text-slate-300 hover:bg-slate-700 disabled:opacity-40"
              title="Поменять старый и новый запуск"
            >
              <ArrowLeftRight size={13} /> Поменять
            </button>
            <button
              onClick={compare}
              disabled={!canCompare}
              className="rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:bg-slate-700 disabled:text-slate-400 px-4 py-2 text-xs font-semibold text-white"
            >
              {busy ? 'Сравниваем…' : 'Сравнить'}
            </button>
          </div>
        </div>

        {finished.length < 2 && (
          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3 text-xs text-slate-400">
            Для сравнения нужны минимум два завершённых запуска. Запустите workflow или обновите историю.
          </div>
        )}
        {sameSelection && (
          <div className="rounded-xl border border-amber-900 bg-amber-950/30 p-3 text-xs text-amber-200">
            Было и Стало — одна и та же проверка. Выберите две разные проверки.
          </div>
        )}

        {error && (
          <div role="alert" className="flex items-start gap-2 rounded-xl border border-rose-900 bg-rose-950/50 p-3 text-xs text-rose-200">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" />
            <span>
              {error}
              {(errorCode || errorTechnical) && (
                <details className="mt-1 text-[10px] text-rose-300/70">
                  <summary>Технические детали</summary>
                  {errorCode && <div className="mt-1">Код: {errorCode}</div>}
                  {errorTechnical && <div className="mt-1 whitespace-pre-wrap break-words">{errorTechnical}</div>}
                </details>
              )}
            </span>
          </div>
        )}

        {result && (
          <div className="space-y-4">
            <div className={`rounded-xl border p-4 text-xs ${result.comparable ? 'border-emerald-900 bg-emerald-950/30 text-emerald-100' : 'border-amber-900 bg-amber-950/30 text-amber-100'}`}>
              <div className="font-semibold">
                {result.comparable ? 'Снимки сопоставимы' : 'Снимки несопоставимы'}
              </div>
              <p className="mt-1 text-[11px] opacity-90">
                {result.comparable
                  ? 'Условия проверок совпали. Статусы ниже показывают, что произошло с находками.'
                  : 'Условия проверок не совпали. Движок не подтверждает исправление, появление или изменение находок.'}
              </p>
              <div className="mt-1 text-[11px] opacity-80">
                Было: {artifactNameForRun(result.baselineRunId)} · Стало: {artifactNameForRun(result.currentRunId)}
              </div>
              <div className="mt-1 text-[11px] opacity-70">
                Пунктов сравнения: {result.items.length} · удалённых файлов: {result.deletedFiles.length}
              </div>
              {resultReasons.length > 0 && (
                <ul className="mt-2 list-disc space-y-1 pl-5">
                  {resultReasons.map((reason) => <li key={reason}>{reason}</li>)}
                </ul>
              )}
              {result.reasons.length > 0 && <TechnicalDetails text={result.reasons.join('\n')} label="Технические основания" />}
            </div>

            {contextUnavailable && (
              <div className="rounded-xl border border-amber-900 bg-amber-950/20 p-3 text-xs text-amber-200">
                Канонические находки недоступны для части запусков. Статусы сравнения не меняются;
                названия и пути находок могут отсутствовать.
              </div>
            )}

            {MAIN_STATUS_ORDER.filter((status) => status !== 'inconclusive').map((status) => (
              <StatusGroup key={status} status={status} items={mainGroups[status]} lookup={lookup} />
            ))}
            <StatusGroup status="inconclusive" items={inconclusiveItems} lookup={lookup} />
            <CoverageSection items={coverageItems} lookup={lookup} />
            <DeletedSection
              deletedFiles={result.deletedFiles}
              items={mainGroups.file_deleted}
              lookup={lookup}
            />

            {result.items.length === 0 && result.deletedFiles.length === 0 && (
              <div className={`rounded-xl border p-4 text-xs ${result.comparable ? 'border-slate-800 bg-slate-900/60 text-slate-300' : 'border-amber-900 bg-amber-950/20 text-amber-200'}`}>
                {result.comparable
                  ? 'Различий в находках нет: новых находок не появилось, старые не исчезли.'
                  : 'Пунктов для сравнения нет. При несопоставимых условиях это не означает отсутствие проблем.'}
              </div>
            )}

            <div className="rounded-xl border border-sky-900 bg-sky-950/20 p-3">
              <div className="mb-1 text-xs font-semibold text-sky-200">
                Дополнительно: проверка SQL-паттерна
              </div>
              {sqlBusy && <p className="text-xs text-slate-400">Проверяем отдельно от общего сравнения…</p>}
              {sqlError && (
                <div className="text-xs text-amber-300">
                  <p>Отдельная проверка недоступна. Общее сравнение от этого не меняется.</p>
                  <TechnicalDetails text={sqlError} label="Техническая причина" />
                </div>
              )}
              {sqlResult && (
                <div className="text-xs text-slate-300">
                  <div className="font-medium text-sky-100">
                    {SQL_STATUS_LABEL[sqlResult.status || 'inconclusive'] || SQL_STATUS_LABEL.inconclusive}
                  </div>
                  <p className="mt-1 text-slate-400">
                    {SQL_STATUS_HINT[sqlResult.status || 'inconclusive'] || SQL_STATUS_HINT.inconclusive}
                  </p>
                  {sqlResult.ruleId && (
                    <p className="mt-1 text-slate-400">Правило: <span className="text-slate-200">{sqlResult.ruleId}</span></p>
                  )}
                  {sqlResult.path && (
                    <p className="mt-1 text-slate-400">Файл: <code className="break-all text-indigo-300">{sqlResult.path}</code></p>
                  )}
                  {sqlResult.reason && <TechnicalDetails text={sqlResult.reason} />}
                  <p className="mt-1 text-[11px] text-slate-500">
                    Это узкая проверка SQL-паттерна, не общий вердикт и не подтверждённое исправление.
                  </p>
                </div>
              )}
              {!sqlBusy && !sqlError && !sqlResult && <p className="text-xs text-slate-500">Нет данных</p>}
            </div>
          </div>
        )}
      </div>
    </section>
  );
};