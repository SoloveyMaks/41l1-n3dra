import React, { useEffect, useMemo, useState } from 'react';
import {
  Play, CheckCircle2, AlertCircle, Loader2, MinusCircle, Clock,
  Terminal, Eye, ChevronRight, RefreshCw, FolderArchive, KeyRound,
  PackageSearch, FileCode2, GitMerge, ShieldAlert, GitFork, Sparkles,
  FileText, Send, Network,
} from 'lucide-react';
import { NodeKind, NodeRunRecord, RunSummary, WorkflowNode } from '../types';
import type { NodeTelemetry, RunTelemetry } from '../execution/runTelemetry';

type LiveEvent = {
  seq: number;
  timestamp: string;
  type: string;
  nodeId?: string;
  payload: unknown;
};

type IconComponent = React.ComponentType<{ size?: number; className?: string }>;

interface LiveRunViewProps {
  currentRun: RunSummary | null;
  nodeRuns: NodeRunRecord[];
  events: LiveEvent[];
  telemetry: RunTelemetry;
  plannedNodes?: WorkflowNode[];
  // False when the run revision is unavailable: the plan is unknown, so no
  // queued stages may be invented from a mutable draft.
  planKnown?: boolean;
  graphMismatch?: boolean;
  onSelectNode: (nodeId: string) => void;
  onRefresh?: () => void;
  onOpenMap?: () => void;
  onOpenFindings?: () => void;
  onOpenReport?: () => void;
}

type DisplayStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'skipped' | 'cancelled';
type PhaseId = 'snapshot' | 'scanners' | 'facts' | 'verdict' | 'ai' | 'report' | 'delivery' | 'other';
type ActionKind = 'map' | 'findings' | 'report';

type Chip = {
  label: string;
  value: string;
  tone?: 'danger' | 'warn' | 'ok' | 'muted';
};

type StageCopy = {
  short: string;
  detail?: string;
  chips?: Chip[];
  warning?: string;
  action?: { kind: ActionKind; label: string };
  statusOverride?: DisplayStatus;
};

type Stage = StageCopy & {
  nodeRun: NodeRunRecord;
  title: string;
  phase: PhaseId;
  status: DisplayStatus;
  durationMs: number;
  icon: IconComponent;
  color: string;
  live?: boolean;
};

type MapRequestState = 'waiting' | 'ready' | 'blocked';

type MapRequest = {
  state: MapRequestState;
  short: string;
  detail?: string;
  actionLabel?: string;
};

const PHASES: Array<{ id: PhaseId; title: string }> = [
  { id: 'snapshot', title: '1. Снимок проекта' },
  { id: 'scanners', title: '2. Сканеры' },
  { id: 'facts', title: '3. Факты кода' },
  { id: 'verdict', title: '4. Политика и ветвление' },
  { id: 'ai', title: '5. AI-анализ' },
  { id: 'report', title: '6. Отчёт и паспорт' },
  { id: 'delivery', title: '7. Уведомление' },
  { id: 'other', title: 'Другие блоки' },
];

const KIND_META: Record<string, { title: string; phase: PhaseId; icon: IconComponent; color: string }> = {
  manual_trigger: { title: 'Ручной запуск', phase: 'snapshot', icon: Play, color: 'text-purple-400' },
  schedule_trigger: { title: 'Плановый запуск', phase: 'snapshot', icon: Clock, color: 'text-purple-400' },
  project_input: { title: 'Снимок проекта', phase: 'snapshot', icon: FolderArchive, color: 'text-blue-400' },
  secret_scan: { title: 'Сканер секретов', phase: 'scanners', icon: KeyRound, color: 'text-amber-400' },
  sca_scan: { title: 'Сканер зависимостей', phase: 'scanners', icon: PackageSearch, color: 'text-orange-400' },
  kotlin_sast: { title: 'Kotlin SAST', phase: 'scanners', icon: FileCode2, color: 'text-rose-400' },
  merge_findings: { title: 'Факты кода', phase: 'facts', icon: GitMerge, color: 'text-teal-400' },
  policy_gate: { title: 'Политика и вердикт', phase: 'verdict', icon: ShieldAlert, color: 'text-emerald-400' },
  condition: { title: 'Ветвление по риску', phase: 'verdict', icon: GitFork, color: 'text-yellow-400' },
  branch_merge: { title: 'Сведение ветвей', phase: 'verdict', icon: GitMerge, color: 'text-indigo-400' },
  ai_explain: { title: 'AI-объяснение', phase: 'ai', icon: Sparkles, color: 'text-fuchsia-400' },
  ai_scan: { title: 'AI-гипотезы', phase: 'ai', icon: Sparkles, color: 'text-fuchsia-300' },
  build_report: { title: 'Отчёт и паспорт проверки', phase: 'report', icon: FileText, color: 'text-cyan-400' },
  notify: { title: 'Уведомление', phase: 'delivery', icon: Send, color: 'text-sky-400' },
};

const STATUS_META: Record<DisplayStatus, { label: string; icon: IconComponent; chip: string; dot: string }> = {
  queued: {
    label: 'В очереди',
    icon: Clock,
    chip: 'bg-amber-950 text-amber-300 border-amber-800',
    dot: 'bg-amber-400',
  },
  running: {
    label: 'Выполняется',
    icon: Loader2,
    chip: 'bg-blue-950 text-blue-300 border-blue-800',
    dot: 'bg-blue-400',
  },
  succeeded: {
    label: 'Готово',
    icon: CheckCircle2,
    chip: 'bg-emerald-950 text-emerald-300 border-emerald-800',
    dot: 'bg-emerald-400',
  },
  failed: {
    label: 'Ошибка',
    icon: AlertCircle,
    chip: 'bg-rose-950 text-rose-300 border-rose-800',
    dot: 'bg-rose-400',
  },
  skipped: {
    label: 'Пропущено',
    icon: MinusCircle,
    chip: 'bg-slate-800 text-slate-400 border-slate-700',
    dot: 'bg-slate-500',
  },
  cancelled: {
    label: 'Отменено',
    icon: MinusCircle,
    chip: 'bg-slate-800 text-slate-400 border-slate-700',
    dot: 'bg-slate-500',
  },
};

const SEVERITY_ORDER = ['critical', 'high', 'medium', 'low', 'info'] as const;
const SEVERITY_LABEL: Record<string, string> = {
  critical: 'крит',
  high: 'выс',
  medium: 'сред',
  low: 'низ',
  info: 'инфо',
};

const CHIP_TONE: Record<NonNullable<Chip['tone']>, string> = {
  danger: 'border-rose-900/70 bg-rose-950/40 text-rose-300',
  warn: 'border-amber-900/70 bg-amber-950/40 text-amber-300',
  ok: 'border-emerald-900/70 bg-emerald-950/40 text-emerald-300',
  muted: 'border-slate-800 bg-slate-900/70 text-slate-400',
};

const MAP_STATE_META: Record<MapRequestState, { label: string; chip: string }> = {
  waiting: { label: 'Ждёт факты кода', chip: 'bg-amber-950 text-amber-300 border-amber-800' },
  ready: { label: 'По запросу', chip: 'bg-indigo-950 text-indigo-300 border-indigo-800' },
  blocked: { label: 'Недоступна', chip: 'bg-rose-950 text-rose-300 border-rose-800' },
};

const asObject = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
const asArray = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);
const asString = (value: unknown): string => (typeof value === 'string' ? value : '');
const asNumber = (value: unknown): number =>
  typeof value === 'number' && Number.isFinite(value) ? value : 0;
const shortId = (value: unknown): string => (typeof value === 'string' ? value.slice(0, 8) : '');

type SeverityKey = (typeof SEVERITY_ORDER)[number];
const isSeverity = (value: string): value is SeverityKey =>
  (SEVERITY_ORDER as readonly string[]).includes(value);

function plural(count: number, one: string, few: string, many: string): string {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return '0 мс';
  if (ms >= 60_000) {
    const minutes = Math.floor(ms / 60_000);
    const seconds = Math.round((ms % 60_000) / 1000);
    return `${minutes} мин ${seconds} с`;
  }
  if (ms >= 1000) return `${(ms / 1000).toFixed(ms >= 10_000 ? 0 : 1)} с`;
  return `${Math.round(ms)} мс`;
}

function formatClock(value?: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function displayStatus(status: string): DisplayStatus {
  if (status === 'running' || status === 'retrying') return 'running';
  if (status === 'succeeded') return 'succeeded';
  if (status === 'failed') return 'failed';
  if (status === 'skipped') return 'skipped';
  if (status === 'cancelled') return 'cancelled';
  return 'queued';
}

function severityCounts(findings: unknown[]): Record<SeverityKey, number> {
  const counts: Record<SeverityKey, number> = { critical: 0, high: 0, medium: 0, low: 0, info: 0 };
  for (const finding of findings) {
    const severity = asString(asObject(finding).severity);
    if (isSeverity(severity)) counts[severity] += 1;
  }
  return counts;
}

function severityChips(findings: unknown[]): Chip[] {
  const counts = severityCounts(findings);
  return SEVERITY_ORDER
    .filter((severity) => counts[severity] > 0)
    .map((severity) => ({
      label: SEVERITY_LABEL[severity],
      value: String(counts[severity]),
      tone: severity === 'critical' ? 'danger' : severity === 'high' ? 'warn' : 'muted',
    }));
}

function codeFactsMapUnavailable(nodeRun: NodeRunRecord): string | undefined {
  const output = asObject(nodeRun.output_data_masked);
  const bundle = asObject(output.findings_bundle);
  for (const item of asArray(bundle.diagnostics)) {
    const diagnostic = asObject(item);
    if (
      asString(diagnostic.source) === 'codefacts' &&
      asString(asObject(diagnostic.details).code) === 'CF-MAP-UNAVAILABLE'
    ) {
      return asString(diagnostic.message) || 'ошибка code facts';
    }
  }
  return undefined;
}

function describeStage(nodeRun: NodeRunRecord): StageCopy {
  const output = asObject(nodeRun.output_data_masked);
  const kind: string = nodeRun.kind;

  // Never describe a failed or skipped block from empty output: a failed
  // scanner would otherwise read as "0 findings, 0 files".
  if (nodeRun.status === 'failed') {
    return {
      short: 'Ошибка выполнения блока',
      detail: nodeRun.error_message || 'Сервер не передал текст ошибки.',
    };
  }
  if (nodeRun.status === 'skipped') {
    return {
      short: 'Пропущен: нет активных входных данных',
      detail: 'Ветка не выбрана или вход не сработал; шаг не влиял на результат.',
    };
  }
  if (nodeRun.status === 'cancelled') {
    return {
      short: 'Отменён вместе с запуском',
      detail: 'Результат по этому шагу не публиковался.',
    };
  }

  if (kind === 'manual_trigger' || kind === 'schedule_trigger') {
    const trigger = asObject(output.out);
    const triggerType = asString(trigger.triggerType) || (kind === 'schedule_trigger' ? 'schedule' : 'manual');
    const timestamp = asString(trigger.timestamp);
    return {
      short: `Триггер: ${triggerType}`,
      detail: timestamp ? `Старт: ${formatClock(timestamp)}` : undefined,
      chips: [{ label: 'Тип', value: triggerType, tone: 'muted' }],
    };
  }

  if (kind === 'project_input') {
    const artifact = asObject(output.artifact);
    const filename = asString(artifact.filename);
    const artifactId = asString(artifact.artifactId);
    return {
      short: filename ? `Снимок: ${filename}` : 'Снимок проекта привязан к запуску',
      detail: artifactId
        ? `Артефакт ${shortId(artifactId)} закреплён за этой проверкой.`
        : undefined,
      chips: artifactId
        ? [{ label: 'Артефакт', value: shortId(artifactId), tone: 'muted' }]
        : [],
    };
  }

  if (kind === 'secret_scan' || kind === 'sca_scan' || kind === 'kotlin_sast') {
    const scan = asObject(output.scan_result);
    const findings = asArray(scan.findings);
    const coverage = asObject(scan.coverage);
    const diagnostics = asArray(scan.diagnostics);
    const errors = diagnostics.filter((item) => asString(asObject(item).level) === 'error');
    const filesScanned = asNumber(coverage.files_scanned);
    return {
      short: `${findings.length} ${plural(findings.length, 'находка', 'находки', 'находок')} · ${filesScanned} ${plural(filesScanned, 'файл', 'файла', 'файлов')} просканировано`,
      detail: `Правил: ${asArray(coverage.rules_executed).length} · диагностика: ${diagnostics.length}`,
      chips: severityChips(findings),
      warning: errors.length
        ? `${errors.length} ${plural(errors.length, 'ошибка', 'ошибки', 'ошибок')} анализатора: ${asString(asObject(errors[0]).message)}`
        : undefined,
      action: { kind: 'findings', label: 'Открыть проблемы' },
    };
  }

  if (kind === 'merge_findings') {
    const bundle = asObject(output.findings_bundle);
    const findings = asArray(bundle.findings);
    const coverage = asObject(bundle.coverage);
    const diagnostics = asArray(bundle.diagnostics);
    const rules = asArray(coverage.rules_executed);
    const codeFactsDiagnostics = diagnostics.filter(
      (item) => asString(asObject(item).source) === 'codefacts',
    );
    const mapUnavailableMessage = codeFactsMapUnavailable(nodeRun);
    const codeFactRules = rules.filter(
      (rule) => typeof rule === 'string' && rule.startsWith('CF-'),
    ).length;
    const parserErrors = asNumber(coverage.parser_errors);
    const unresolved = asNumber(coverage.unresolved_dependencies);
    const firstCodeFactMessage = asString(asObject(codeFactsDiagnostics[0]).message);
    return {
      short: `Находок: ${findings.length} · файлов: ${asNumber(coverage.files_scanned)} · CF-правил: ${codeFactRules}`,
      detail: 'Факты кода собраны. Карта доступа строится отдельно по запросу при открытии.',
      chips: [
        { label: 'Правил', value: String(rules.length), tone: 'muted' },
        { label: 'Карта', value: 'по запросу', tone: 'muted' },
        { label: 'Ошибок парсера', value: String(parserErrors), tone: parserErrors ? 'warn' : 'muted' },
        { label: 'Зависимостей без версии', value: String(unresolved), tone: unresolved ? 'warn' : 'muted' },
      ],
      warning: mapUnavailableMessage
        ? `Карта доступа не построена: ${mapUnavailableMessage}`
        : codeFactsDiagnostics.length
          ? `Факты кода: ${codeFactsDiagnostics.length} ${plural(codeFactsDiagnostics.length, 'замечание', 'замечания', 'замечаний')}. ${firstCodeFactMessage}`
          : undefined,
    };
  }

  if (kind === 'policy_gate') {
    const policy = asObject(output.policy_result);
    const verdict = asString(policy.verdict) || 'не определён';
    const reasons = asArray(policy.reasons).map(asString).filter(Boolean);
    const criticalCount = asNumber(policy.critical_count);
    const highCount = asNumber(policy.high_count);
    return {
      short: `Вердикт: ${verdict} · находок: ${asNumber(policy.findings_count)}`,
      detail: reasons[0],
      chips: [
        { label: 'крит', value: String(criticalCount), tone: criticalCount ? 'danger' : 'muted' },
        { label: 'выс', value: String(highCount), tone: highCount ? 'warn' : 'muted' },
        { label: 'сред', value: String(asNumber(policy.medium_count)), tone: 'muted' },
      ],
      warning: verdict === 'inconclusive' ? 'Проверка неполная: вердикт не доказывает безопасность.' : undefined,
      action: { kind: 'findings', label: 'Открыть проблемы' },
    };
  }

  if (kind === 'condition') {
    const isTrue = Boolean(output.true_branch);
    const branch = asObject(output.true_branch ?? output.false_branch);
    const field = asString(branch.field) || 'поле';
    const actualValue = branch.actualValue;
    return {
      short: `Ветка: ${isTrue ? 'истина' : 'ложь'} · ${field}: ${String(actualValue ?? '—')}`,
      detail: branch.targetValue !== undefined
        ? `Ожидалось ${field} = ${String(branch.targetValue)}`
        : undefined,
      chips: [{ label: 'Решение', value: isTrue ? 'true' : 'false', tone: isTrue ? 'ok' : 'muted' }],
    };
  }

  if (kind === 'branch_merge') {
    return { short: 'Ветви сведены, данные переданы дальше' };
  }

  if (kind === 'ai_scan') {
    const result = asObject(output.ai_scan_result);
    const observations = asArray(result.observations);
    const coverage = asObject(result.coverage);
    const provenance = asObject(result.provenance);
    const status = asString(result.status) || '—';
    const coverageStatus = asString(coverage.status) || '—';
    const model = asString(provenance.model);
    const sent = asNumber(coverage.filesSent);
    const selected = asNumber(coverage.filesSelected);
    const providerErrors = asArray(coverage.providerErrors).map(asString).filter(Boolean);
    const validationErrors = asArray(result.validationErrors).map(asString).filter(Boolean);
    const incomplete = status !== 'succeeded' || coverageStatus !== 'complete';
    return {
      short: `Гипотез: ${observations.length} · ${status} · не findings`,
      detail: `${model ? `Модель ${model}. ` : ''}Файлов отправлено ${sent}/${selected} · подавлено дублей: ${asNumber(result.suppressedDuplicates)}`,
      chips: [
        { label: 'Покрытие', value: coverageStatus, tone: incomplete ? 'warn' : 'ok' },
        ...severityChips(observations),
      ],
      warning: incomplete
        ? `AI-проверка неполная (${status}/${coverageStatus}). ${providerErrors[0] || validationErrors[0] || 'Гипотезы не влияют на вердикт.'}`
        : validationErrors.length
          ? `Валидация AI: ${validationErrors[0]}`
          : undefined,
      action: { kind: 'findings', label: 'Открыть AI-гипотезы' },
    };
  }

  if (kind === 'ai_explain') {
    const explain = asObject(output.explain_result);
    const items = asArray(explain.items);
    const status = asString(explain.status) || '—';
    const model = asString(explain.model) || 'модель не указана';
    const validationErrors = asArray(explain.validation_errors).map(asString).filter(Boolean);
    return {
      short: `AI: ${status} · объяснений: ${items.length} · ${model}`,
      detail: asString(explain.summary) || undefined,
      chips: [
        { label: 'Модель', value: model, tone: 'muted' },
        { label: 'Ошибок валидации', value: String(validationErrors.length), tone: validationErrors.length ? 'warn' : 'muted' },
      ],
      warning: validationErrors.length
        ? `Валидация AI: ${validationErrors.join('; ')}`
        : undefined,
      action: { kind: 'report', label: 'Смотреть в отчёте' },
    };
  }

  if (kind === 'build_report') {
    const report = asObject(output.report);
    const passport = asObject(output.passport);
    const parserErrors = asNumber(passport.parser_errors);
    return {
      short: `Отчёт собран · вердикт ${asString(report.verdict) || '—'} · находок: ${asNumber(report.findingsCount)}`,
      detail: `Файлов: ${asNumber(passport.files_scanned)} · строк: ${asNumber(passport.lines_of_code)} · готов ${formatClock(asString(report.generatedAt))}`,
      chips: [
        { label: 'Паспорт', value: asString(passport.verdict) || '—', tone: asString(passport.verdict) === 'fail' ? 'danger' : 'muted' },
        { label: 'Ошибок парсера', value: String(parserErrors), tone: parserErrors ? 'warn' : 'muted' },
      ],
      action: { kind: 'report', label: 'Открыть отчёт' },
    };
  }

  if (kind === 'notify') {
    const delivery = asObject(output.delivery_result);
    const deliveryStatus = asString(delivery.status);
    const target = asString(delivery.target);
    return {
      short: `Доставка: ${deliveryStatus || '—'} · ${asString(delivery.channel) || 'webhook'}`,
      detail: asString(delivery.message) || undefined,
      chips: target ? [{ label: 'Адрес', value: target, tone: 'muted' }] : [],
      warning: deliveryStatus === 'failed'
        ? 'Уведомление не отправлено.'
        : deliveryStatus === 'skipped'
          ? 'Отправка пропущена по настройкам сценария.'
          : undefined,
      statusOverride:
        deliveryStatus === 'failed'
          ? 'failed'
          : deliveryStatus === 'skipped'
            ? 'skipped'
            : deliveryStatus === 'succeeded'
              ? 'succeeded'
              : undefined,
    };
  }

  return {
    short: nodeRun.status === 'succeeded' ? 'Блок выполнен' : 'Блок не завершился',
    detail: `Тип блока: ${kind}`,
  };
}

// The access map is assembled by GET /runs/{id}/security-map, so no SSE event
// ever says "map built". This state only reports whether the facts stage
// produced map source; it never fakes a build.
function describeMapRequest(nodeRuns: NodeRunRecord[], runIsActive: boolean): MapRequest {
  const facts = [...nodeRuns].reverse().find((nodeRun) => nodeRun.kind === 'merge_findings');
  if (!facts) {
    return runIsActive
      ? {
          state: 'waiting',
          short: 'Карта станет доступна после сбора фактов кода.',
          detail: 'Этап «Факты кода» ещё не завершён; отдельного события построения карты нет.',
        }
      : {
          state: 'blocked',
          short: 'В этом запуске нет записанного этапа фактов кода.',
          detail: 'Карта доступа отдаётся только при наличии опубликованных фактов.',
        };
  }
  const unavailable = codeFactsMapUnavailable(facts);
  if (unavailable) {
    return {
      state: 'blocked',
      short: 'Карта недоступна: ошибка фактов кода.',
      detail: unavailable,
    };
  }
  if (facts.status === 'failed') {
    return {
      state: 'blocked',
      short: 'Факты кода не собраны: этап завершился ошибкой.',
      detail: facts.error_message || 'Карту строить не из чего.',
    };
  }
  if (facts.status === 'skipped') {
    return {
      state: 'blocked',
      short: 'Этап фактов кода пропущен.',
      detail: 'В этом запуске не было активных входных данных для карты.',
    };
  }
  if (facts.status !== 'succeeded') {
    return {
      state: 'waiting',
      short: 'Идёт сбор фактов кода; карта станет доступна после этапа.',
      detail: 'Статус построения карты не приходит отдельным SSE-событием.',
    };
  }
  if (runIsActive) {
    return {
      state: 'waiting',
      short: 'Факты кода собраны; карта станет доступна после завершения запуска.',
      detail: 'Канонический набор публикуется в конце запуска, а карта собирается по запросу при открытии.',
    };
  }
  return {
    state: 'ready',
    short: 'Карта строится сервером в момент открытия.',
    detail:
      'Отдельного SSE-события построения карты нет: endpoint собирает карту по запросу из опубликованных фактов.',
    actionLabel: 'Открыть карту (построится сейчас)',
  };
}

function buildStage(nodeRun: NodeRunRecord): Stage {
  const meta: { title: string; phase: PhaseId; icon: IconComponent; color: string } =
    KIND_META[nodeRun.kind] || {
      title: nodeRun.kind,
      phase: 'other',
      icon: Play,
      color: 'text-slate-400',
    };
  const copy = describeStage(nodeRun);
  return {
    ...copy,
    nodeRun,
    title: meta.title,
    phase: meta.phase,
    status: copy.statusOverride || displayStatus(nodeRun.status),
    durationMs: asNumber(nodeRun.duration_ms),
    icon: meta.icon,
    color: meta.color,
  };
}

const NODE_ID_KIND_HINTS: Array<[string, NodeKind]> = [
  ['node_start', 'manual_trigger'],
  ['node_project', 'project_input'],
  ['node_secret', 'secret_scan'],
  ['node_sca', 'sca_scan'],
  ['node_sast', 'kotlin_sast'],
  ['node_merge', 'merge_findings'],
  ['node_policy', 'policy_gate'],
  ['node_cond', 'condition'],
  ['node_ai_scan', 'ai_scan'],
  ['node_ai', 'ai_explain'],
  ['node_report', 'build_report'],
  ['node_notify', 'notify'],
];

function inferNodeKind(nodeId: string, plannedNodes: WorkflowNode[]): NodeKind | null {
  const planned = plannedNodes.find((node) => node.id === nodeId);
  if (planned) return planned.kind;
  const hint = NODE_ID_KIND_HINTS.find(([prefix]) => nodeId.startsWith(prefix));
  if (hint) return hint[1];
  const kindMatch = (Object.keys(KIND_META) as NodeKind[]).find((kind) => nodeId.includes(kind));
  return kindMatch ?? null;
}

function liveStageText(kind: NodeKind | null): string {
  switch (kind) {
    case 'secret_scan':
    case 'sca_scan':
    case 'kotlin_sast':
      return 'Сканер выполняет проверку…';
    case 'merge_findings':
      return 'Собираем находки, факты кода и карту доступа…';
    case 'ai_explain':
      return 'Модель объясняет находки…';
    case 'ai_scan':
      return 'Модель ищет дополнительные гипотезы…';
    case 'build_report':
      return 'Собираем отчёт и паспорт проверки…';
    case 'notify':
      return 'Отправляем уведомление…';
    case 'policy_gate':
    case 'condition':
      return 'Оцениваем результат проверки…';
    case 'project_input':
      return 'Готовим снимок проекта…';
    default:
      return 'Блок выполняется…';
  }
}

function buildTelemetryStage(
  nodeId: string,
  state: NodeTelemetry,
  runId: string,
  plannedNodes: WorkflowNode[],
  nowMs: number,
): Stage {
  const kind = inferNodeKind(nodeId, plannedNodes);
  const meta = kind ? KIND_META[kind] : undefined;
  const status = displayStatus(state.status);
  const startedAtMs = state.startedAtMs;
  const durationMs =
    status === 'running'
      ? startedAtMs !== undefined
        ? Math.max(0, nowMs - startedAtMs)
        : asNumber(state.durationMs)
      : asNumber(state.durationMs);
  const startedAt = startedAtMs !== undefined ? new Date(startedAtMs).toISOString() : undefined;
  const nodeRun: NodeRunRecord = {
    id: `telemetry:${nodeId}`,
    run_id: runId,
    node_id: nodeId,
    kind: kind ?? 'notify',
    status: state.status,
    started_at: startedAt,
    duration_ms: durationMs,
    input_data_masked: {},
    output_data_masked: {},
  };
  const short =
    status === 'running'
      ? liveStageText(kind)
      : status === 'failed'
        ? 'Блок завершился ошибкой'
        : status === 'skipped'
          ? 'Пропущен: нет активных входных данных'
          : status === 'cancelled'
            ? 'Отменён вместе с запуском'
            : status === 'succeeded'
              ? 'Блок завершён, результат ещё загружается'
              : 'Ожидает запуска';
  const detail =
    status === 'running'
      ? startedAt
        ? `Событие старта получено в ${formatClock(startedAt)}. Итог появится после завершения блока.`
        : 'Статус выполнения получен из SSE.'
      : status === 'failed'
        ? state.error || 'Сервер не передал текст ошибки.'
        : status === 'succeeded'
          ? 'Статус уже подтверждён SSE; запись блока с выходными данными ещё не загружена.'
          : undefined;
  return {
    nodeRun,
    title: meta?.title || `Блок ${nodeId}`,
    phase: meta?.phase || 'other',
    status,
    durationMs,
    icon: meta?.icon || Play,
    color: meta?.color || 'text-slate-400',
    short,
    detail,
    chips: [],
    live: status === 'running',
  };
}

// Planned nodes are shown as queued only while the run is active and only for
// nodes that have not produced any telemetry yet. A completed run never grows
// invented stages: its real node_runs already include skipped blocks.
function buildPlannedStage(node: WorkflowNode, runId: string): Stage {
  const meta = KIND_META[node.kind];
  const nodeRun: NodeRunRecord = {
    id: `queued:${node.id}`,
    run_id: runId,
    node_id: node.id,
    kind: node.kind,
    status: 'pending',
    duration_ms: 0,
    input_data_masked: {},
    output_data_masked: {},
  };
  return {
    nodeRun,
    title: meta?.title || node.title || node.kind,
    phase: meta?.phase || 'other',
    status: 'queued',
    durationMs: 0,
    icon: meta?.icon || Play,
    color: meta?.color || 'text-slate-400',
    short: 'Ожидает запуска',
    detail: 'План сценария: исполнитель ещё не присылал статус этого блока.',
    chips: [],
  };
}

const PHASE_RANK = new Map<PhaseId, number>(PHASES.map((phase, index) => [phase.id, index]));

function compareStages(left: Stage, right: Stage): number {
  const phaseDiff =
    (PHASE_RANK.get(left.phase) ?? PHASES.length) - (PHASE_RANK.get(right.phase) ?? PHASES.length);
  if (phaseDiff !== 0) return phaseDiff;
  const leftStart = left.nodeRun.started_at || '';
  const rightStart = right.nodeRun.started_at || '';
  if (leftStart !== rightStart) return leftStart.localeCompare(rightStart);
  return left.nodeRun.node_id.localeCompare(right.nodeRun.node_id);
}

function eventLabel(type: string): string {
  const labels: Record<string, string> = {
    run_status: 'Статус запуска',
    node_status: 'Статус блока',
    edge_data: 'Данные связи',
    finding_summary: 'Сводка находок',
    coverage: 'Покрытие',
    delivery_status: 'Доставка',
    artifact_status: 'Артефакт',
  };
  return labels[type] || type;
}

function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value ?? {}, null, 2);
  } catch {
    return String(value);
  }
}

export const LiveRunView: React.FC<LiveRunViewProps> = ({
  currentRun,
  nodeRuns,
  events,
  telemetry,
  plannedNodes = [],
  planKnown = true,
  graphMismatch = false,
  onSelectNode,
  onRefresh,
  onOpenMap,
  onOpenFindings,
  onOpenReport,
}) => {
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [detailTab, setDetailTab] = useState<'data' | 'events'>('data');
  const [onlySelectedEvents, setOnlySelectedEvents] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const runIsActive =
    currentRun?.execution_status === 'queued' || currentRun?.execution_status === 'running';

  useEffect(() => {
    setSelectedNodeId(null);
    setOnlySelectedEvents(false);
  }, [currentRun?.id]);

  useEffect(() => {
    if (!runIsActive) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [runIsActive]);

  const recordedStages = useMemo(
    () => nodeRuns.map(buildStage).sort(compareStages),
    [nodeRuns],
  );

  // Real per-node statuses from SSE + API. These also cover a reload where the
  // API node_runs list has not caught up with the latest node_status event.
  const telemetryStages = useMemo(() => {
    if (!currentRun) return [];
    const recordedIds = new Set(recordedStages.map((stage) => stage.nodeRun.node_id));
    return Object.entries(telemetry.nodes)
      .filter(([nodeId, state]) => !recordedIds.has(nodeId) && state.status !== 'pending')
      .map(([nodeId, state]) =>
        buildTelemetryStage(nodeId, state, currentRun.id, plannedNodes, now),
      );
  }, [currentRun, recordedStages, telemetry.nodes, plannedNodes, now]);

  // Upcoming plan blocks are honest "queued" cards only while the run is
  // active. A finished run shows recorded/skipped nodes instead of a fake plan.
  const queuedStages = useMemo(() => {
    if (!runIsActive || !currentRun || !planKnown) return [];
    const seen = new Set(
      [...recordedStages, ...telemetryStages].map((stage) => stage.nodeRun.node_id),
    );
    return plannedNodes
      .filter((node) => !seen.has(node.id))
      .map((node) => buildPlannedStage(node, currentRun.id));
  }, [runIsActive, currentRun, recordedStages, telemetryStages, plannedNodes, planKnown]);

  const stages = useMemo(
    () => [...recordedStages, ...telemetryStages, ...queuedStages].sort(compareStages),
    [recordedStages, telemetryStages, queuedStages],
  );

  const mapRequest = useMemo(
    () => describeMapRequest(nodeRuns, runIsActive),
    [nodeRuns, runIsActive],
  );

  const groupedStages = useMemo(
    () =>
      PHASES
        .map((phase) => ({ ...phase, stages: stages.filter((stage) => stage.phase === phase.id) }))
        .filter((phase) => phase.stages.length > 0),
    [stages],
  );

  const selectedStage = stages.find((stage) => stage.nodeRun.node_id === selectedNodeId) || null;

  const statusCounts = useMemo(() => {
    const counts: Record<DisplayStatus, number> = {
      queued: 0,
      running: 0,
      succeeded: 0,
      failed: 0,
      skipped: 0,
      cancelled: 0,
    };
    for (const stage of stages) counts[stage.status] += 1;
    return counts;
  }, [stages]);

  const runDurationMs = useMemo(() => {
    if (!currentRun) return 0;
    const startValue = currentRun.started_at || currentRun.created_at;
    const start = Date.parse(startValue);
    if (Number.isNaN(start)) return 0;
    const end = currentRun.finished_at
      ? Date.parse(currentRun.finished_at)
      : runIsActive
        ? now
        : start;
    return Math.max(0, end - start);
  }, [currentRun, now, runIsActive]);

  const failureReason = useMemo(() => {
    if (currentRun?.execution_status !== 'failed') return undefined;
    const nodeError = nodeRuns.find((nodeRun) => nodeRun.error_message)?.error_message;
    if (nodeError) return nodeError;
    const runError = [...events]
      .reverse()
      .find((event) => {
        if (event.type !== 'run_status') return false;
        const payload = asObject(event.payload);
        return asArray(payload.errors).length > 0 || asString(payload.error).length > 0;
      });
    if (!runError) return undefined;
    const payload = asObject(runError.payload);
    const errors = asArray(payload.errors).map(asString).filter(Boolean);
    return errors.length ? errors.join('; ') : asString(payload.error);
  }, [currentRun?.execution_status, nodeRuns, events]);

  const visibleEvents = useMemo(() => {
    if (onlySelectedEvents && selectedStage) {
      return events.filter((event) => event.nodeId === selectedStage.nodeRun.node_id);
    }
    return events;
  }, [events, onlySelectedEvents, selectedStage]);

  const selectStage = (stage: Stage) => {
    setSelectedNodeId(stage.nodeRun.node_id);
    setDetailTab('data');
    onSelectNode(stage.nodeRun.node_id);
  };

  const openAction = (kind: ActionKind) => {
    if (kind === 'map') onOpenMap?.();
    if (kind === 'findings') onOpenFindings?.();
    if (kind === 'report') onOpenReport?.();
  };

  if (!currentRun) {
    return (
      <div className="flex-1 h-full flex flex-col items-center justify-center text-slate-500 space-y-2">
        <Play size={36} className="text-slate-600 animate-pulse" />
        <p className="text-sm">Нет активного или выбранного запуска</p>
        <p className="text-xs text-slate-600">Запустите сценарий из редактора или выберите запуск из истории</p>
      </div>
    );
  }

  const isQueued = currentRun.execution_status === 'queued';
  const isRunning = currentRun.execution_status === 'running';
  const isActiveRun = isQueued || isRunning;
  const isFailed = currentRun.execution_status === 'failed';
  const statusLabel = isQueued
    ? 'В очереди'
    : isRunning
      ? 'Выполняется'
      : isFailed
        ? 'Ошибка'
        : currentRun.execution_status === 'succeeded'
          ? 'Завершён'
          : currentRun.execution_status;

  const totalStages = stages.length;
  const finishedStages =
    statusCounts.succeeded + statusCounts.failed + statusCounts.skipped + statusCounts.cancelled;

  const progressSegments: Array<{ status: DisplayStatus; width: number }> = totalStages
    ? (['succeeded', 'failed', 'skipped', 'running', 'queued'] as DisplayStatus[])
        .filter((status) => statusCounts[status] > 0)
        .map((status) => ({ status, width: (statusCounts[status] / totalStages) * 100 }))
    : [];

  const mapMeta = MAP_STATE_META[mapRequest.state];
  const mapCard = (
    <section className="space-y-2" data-map-state={mapRequest.state}>
      <div className="flex items-center gap-2 px-1">
        <span className="h-1.5 w-1.5 rounded-full bg-indigo-500" />
        <h3 className="text-[10px] font-bold uppercase tracking-wider text-slate-400">
          Карта доступа
        </h3>
        <span className="text-[10px] text-slate-600">request-time</span>
      </div>
      <div className="rounded-xl border border-slate-800 bg-slate-900/80">
        <div className="flex items-start gap-2.5 p-3">
          <div className="mt-0.5 rounded-lg border border-slate-700/70 bg-slate-950/50 p-1.5 text-indigo-300">
            <Network size={16} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-semibold text-slate-100">Карта доступа</span>
              <span className={`rounded border px-1.5 py-0.5 text-[10px] font-semibold ${mapMeta.chip}`}>
                {mapMeta.label}
              </span>
              <span className="text-[10px] font-mono text-slate-500">без SSE-события</span>
            </div>
            <p className="mt-1 text-[11px] leading-snug text-slate-300">{mapRequest.short}</p>
            {mapRequest.detail && (
              <p className="mt-0.5 text-[11px] leading-snug text-slate-500">{mapRequest.detail}</p>
            )}
          </div>
        </div>
        {mapRequest.actionLabel && onOpenMap && (
          <div className="flex items-center justify-end border-t border-slate-800/80 px-3 py-1.5">
            <button
              type="button"
              onClick={() => openAction('map')}
              className="flex items-center gap-1 text-[10px] font-medium text-indigo-300 hover:text-indigo-200"
            >
              {mapRequest.actionLabel}
              <ChevronRight size={11} />
            </button>
          </div>
        )}
      </div>
    </section>
  );

  return (
    <div className="flex-1 h-full flex flex-col overflow-hidden bg-slate-950">
      <div className="p-3 sm:p-4 bg-slate-900 border-b border-slate-800">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-start gap-3 min-w-0">
            <div
              className={`p-2 rounded-xl text-white shadow-lg shrink-0 ${
                isQueued
                  ? 'bg-amber-600 shadow-amber-500/20'
                  : isRunning
                    ? 'bg-blue-600 shadow-blue-500/20'
                    : isFailed
                      ? 'bg-rose-600 shadow-rose-500/20'
                      : 'bg-emerald-600 shadow-emerald-500/20'
              }`}
            >
              {isActiveRun ? (
                <Loader2 size={18} className="animate-spin" />
              ) : isFailed ? (
                <AlertCircle size={18} />
              ) : (
                <CheckCircle2 size={18} />
              )}
            </div>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span
                  className="font-mono text-xs sm:text-sm font-bold text-slate-200 truncate max-w-[190px] sm:max-w-none"
                  title={currentRun.id}
                >
                  Запуск: {currentRun.id}
                </span>
                <span
                  className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider border ${
                    isQueued
                      ? 'bg-amber-950 text-amber-300 border-amber-800'
                      : isRunning
                        ? 'bg-blue-950 text-blue-300 border-blue-800'
                        : isFailed
                          ? 'bg-rose-950 text-rose-300 border-rose-800'
                          : 'bg-emerald-950 text-emerald-300 border-emerald-800'
                  }`}
                >
                  {statusLabel}
                </span>
                <span
                  className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider border ${
                    currentRun.security_verdict === 'fail'
                      ? 'bg-rose-950 text-rose-300 border-rose-800'
                      : currentRun.security_verdict === 'pass'
                        ? 'bg-emerald-950 text-emerald-300 border-emerald-800'
                        : 'bg-yellow-950 text-yellow-300 border-yellow-800'
                  }`}
                >
                  Вердикт: {currentRun.security_verdict}
                </span>
              </div>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-slate-400 mt-1">
                <span>Триггер: <strong className="text-slate-300">{currentRun.trigger_type}</strong></span>
                <span>•</span>
                <span>Длительность: <strong className="text-slate-300">{formatDuration(runDurationMs)}</strong></span>
                <span>•</span>
                <span>Снимок: <strong className="text-slate-300 font-mono">{shortId(currentRun.artifact_id) || '—'}</strong></span>
                <span>•</span>
                <span>Находок: <strong className="text-slate-300">{currentRun.total_findings}</strong> (крит: {currentRun.critical_count}, выс: {currentRun.high_count})</span>
                <span>•</span>
                <span>Событий: <strong className="text-slate-300">{events.length}</strong></span>
              </div>
            </div>
          </div>
          {onRefresh && (
            <button
              onClick={onRefresh}
              className="flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800 px-2.5 py-1.5 text-[11px] text-slate-300 hover:bg-slate-700 shrink-0"
              title="Обновить состояние запуска"
            >
              <RefreshCw size={13} />
              <span>Обновить</span>
            </button>
          )}
        </div>

        {failureReason && (
          <div className="mt-2 rounded-lg border border-rose-900 bg-rose-950/60 px-2.5 py-1.5 text-[11px] text-rose-200">
            {failureReason}
          </div>
        )}

        <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-400">
          <span>
            Этапы: <strong className="text-slate-200">{finishedStages}</strong> из {totalStages}
          </span>
          {runIsActive && !planKnown && (
            <span className="text-amber-300" data-plan-state="unknown">
              план запуска неизвестен — только подтверждённые этапы
            </span>
          )}
          {graphMismatch && (
            <span className="text-amber-300" data-graph-binding="mismatch">
              граф ревизии запуска отличается от черновика
            </span>
          )}
          {statusCounts.running > 0 && <span className="text-blue-300">в работе: {statusCounts.running}</span>}
          {statusCounts.failed > 0 && <span className="text-rose-300">ошибок: {statusCounts.failed}</span>}
          {statusCounts.skipped > 0 && <span className="text-slate-500">пропущено: {statusCounts.skipped}</span>}
          {totalStages > 0 && (
            <div className="flex h-1.5 min-w-[120px] flex-1 overflow-hidden rounded-full bg-slate-800" aria-hidden>
              {progressSegments.map((segment) => (
                <span
                  key={segment.status}
                  className={`h-full ${STATUS_META[segment.status].dot}`}
                  style={{ width: `${segment.width}%` }}
                />
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto lg:overflow-hidden min-h-0">
        <div className="grid grid-cols-1 lg:grid-cols-12 lg:h-full min-h-0">
          <div className="lg:col-span-8 flex flex-col min-h-0 border-b lg:border-b-0 lg:border-r border-slate-800">
            <div className="lg:flex-1 lg:overflow-y-auto p-3 sm:p-4 space-y-4">
              {stages.length === 0 && (
                <div className="rounded-xl border border-dashed border-slate-800 bg-slate-900/40 p-5 text-center">
                  <p className="text-sm font-medium text-slate-300">
                    {isActiveRun ? 'Исполнитель ещё не начал блоки' : 'Записей блоков для этого запуска нет'}
                  </p>
                  <p className="mt-1 text-xs text-slate-500">
                    {isActiveRun
                      ? 'Этапы появятся из реальных событий и записей блоков — без искусственного прогресса.'
                      : 'Статус запуска и вердикт доступны выше; детали блоков сервер не сохранил.'}
                  </p>
                </div>
              )}

              {groupedStages.map((phase) => (
                <React.Fragment key={phase.id}>
                <section className="space-y-2">
                  <div className="flex items-center gap-2 px-1">
                    <span className="h-1.5 w-1.5 rounded-full bg-indigo-500" />
                    <h3 className="text-[10px] font-bold uppercase tracking-wider text-slate-400">
                      {phase.title}
                    </h3>
                    <span className="text-[10px] text-slate-600">
                      {phase.stages.filter((stage) => stage.status === 'succeeded').length}/{phase.stages.length} готово
                    </span>
                  </div>
                  <div className="space-y-2">
                    {phase.stages.map((stage) => {
                      const Icon = stage.icon;
                      const status = STATUS_META[stage.status];
                      const StatusIcon = status.icon;
                      const isSelected = selectedNodeId === stage.nodeRun.node_id;
                      return (
                        <div
                          key={stage.nodeRun.id}
                          data-stage-id={stage.nodeRun.node_id}
                          data-stage-status={stage.status}
                          className={`rounded-xl border transition-all ${
                            isSelected
                              ? 'bg-slate-800 border-indigo-500 shadow-md ring-1 ring-indigo-400/50'
                              : 'bg-slate-900/80 hover:bg-slate-800/60 border-slate-800'
                          }`}
                        >
                          <button
                            type="button"
                            onClick={() => selectStage(stage)}
                            aria-pressed={isSelected}
                            className="w-full text-left p-3"
                          >
                            <div className="flex items-start gap-2.5">
                              <div className={`mt-0.5 rounded-lg border border-slate-700/70 bg-slate-950/50 p-1.5 ${stage.color}`}>
                                <Icon size={16} />
                              </div>
                              <div className="min-w-0 flex-1">
                                <div className="flex flex-wrap items-center gap-2">
                                  <span className="text-xs font-semibold text-slate-100">{stage.title}</span>
                                  <span className={`flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-semibold ${status.chip}`}>
                                    <StatusIcon
                                      size={11}
                                      className={stage.status === 'running' ? 'animate-spin' : undefined}
                                    />
                                    {status.label}
                                  </span>
                                  <span className="text-[10px] font-mono text-slate-500">
                                    {stage.status === 'queued' ? 'не начат' : formatDuration(stage.durationMs)}
                                  </span>
                                </div>
                                <p className="mt-1 text-[11px] leading-snug text-slate-300">{stage.short}</p>
                                {stage.detail && (
                                  <p className="mt-0.5 text-[11px] leading-snug text-slate-500 line-clamp-2">
                                    {stage.detail}
                                  </p>
                                )}
                                {stage.chips && stage.chips.length > 0 && (
                                  <div className="mt-1.5 flex flex-wrap gap-1">
                                    {stage.chips.map((chip, index) => (
                                      <span
                                        key={`${chip.label}-${index}`}
                                        className={`rounded border px-1.5 py-0.5 text-[10px] ${
                                          CHIP_TONE[chip.tone || 'muted']
                                        }`}
                                      >
                                        {chip.label}: <strong className="font-semibold">{chip.value}</strong>
                                      </span>
                                    ))}
                                  </div>
                                )}
                                {(stage.warning || stage.nodeRun.error_message) && (
                                  <div className="mt-1.5 rounded border border-amber-900/70 bg-amber-950/30 px-2 py-1 text-[10px] leading-snug text-amber-200">
                                    {stage.warning || stage.nodeRun.error_message}
                                  </div>
                                )}
                              </div>
                              <ChevronRight size={16} className="mt-1 shrink-0 text-slate-600" />
                            </div>
                          </button>
                          {stage.action && (
                            <div className="flex items-center justify-end border-t border-slate-800/80 px-3 py-1.5">
                              <button
                                type="button"
                                onClick={() => openAction(stage.action!.kind)}
                                className="flex items-center gap-1 text-[10px] font-medium text-indigo-300 hover:text-indigo-200"
                              >
                                {stage.action.label}
                                <ChevronRight size={11} />
                              </button>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </section>
                {phase.id === 'facts' && mapCard}
                </React.Fragment>
              ))}
              {!groupedStages.some((phase) => phase.id === 'facts') && mapCard}
            </div>
          </div>

          <div className="lg:col-span-4 flex flex-col min-h-0 bg-slate-900/40">
            <div className="p-3 border-b border-slate-800">
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="text-[10px] font-bold uppercase tracking-wider text-slate-500">
                    Детали этапа
                  </div>
                  <div className="truncate text-xs font-semibold text-slate-200">
                    {selectedStage ? selectedStage.title : 'Выберите этап слева'}
                  </div>
                </div>
                {selectedStage && (
                  <span className="shrink-0 font-mono text-[10px] text-slate-500">
                    {selectedStage.nodeRun.node_id}
                  </span>
                )}
              </div>
              <div className="mt-2 grid grid-cols-2 gap-1 rounded-lg border border-slate-800 bg-slate-950/60 p-0.5">
                <button
                  type="button"
                  onClick={() => setDetailTab('data')}
                  className={`flex items-center justify-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium ${
                    detailTab === 'data'
                      ? 'bg-indigo-600 text-white'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  <Eye size={12} /> Данные
                </button>
                <button
                  type="button"
                  onClick={() => setDetailTab('events')}
                  className={`flex items-center justify-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium ${
                    detailTab === 'events'
                      ? 'bg-emerald-700 text-white'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  <Terminal size={12} /> События ({events.length})
                </button>
              </div>
            </div>

            {detailTab === 'data' ? (
              <div className="flex-1 min-h-0 overflow-y-auto p-3 text-xs font-mono space-y-3">
                {selectedStage ? (
                  <>
                    <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-2 font-sans">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[11px] font-semibold text-slate-200">
                          {selectedStage.short}
                        </span>
                        <span className={`rounded border px-1.5 py-0.5 text-[10px] ${STATUS_META[selectedStage.status].chip}`}>
                          {STATUS_META[selectedStage.status].label}
                        </span>
                      </div>
                      {selectedStage.detail && (
                        <p className="mt-1 text-[11px] leading-snug text-slate-400">{selectedStage.detail}</p>
                      )}
                    </div>
                    <div>
                      <div className="mb-1 text-[10px] font-bold uppercase tracking-wider text-slate-500">
                        Входные данные
                      </div>
                      <pre className="rounded-lg border border-slate-800 bg-slate-900 p-2.5 text-[11px] leading-relaxed text-slate-300 overflow-x-auto">
                        {prettyJson(selectedStage.nodeRun.input_data_masked)}
                      </pre>
                    </div>
                    <div>
                      <div className="mb-1 text-[10px] font-bold uppercase tracking-wider text-slate-500">
                        Выходные данные
                      </div>
                      <pre className="rounded-lg border border-slate-800 bg-slate-900 p-2.5 text-[11px] leading-relaxed text-indigo-200 overflow-x-auto">
                        {prettyJson(selectedStage.nodeRun.output_data_masked)}
                      </pre>
                    </div>
                  </>
                ) : (
                  <div className="flex h-full flex-col items-center justify-center gap-2 p-4 text-center text-slate-600">
                    <Eye size={24} />
                    <p className="font-sans text-xs">
                      Нажмите на этап, чтобы увидеть входные и выходные данные блока.
                    </p>
                  </div>
                )}
              </div>
            ) : (
              <div className="flex flex-col flex-1 min-h-0">
                <div className="flex items-center justify-between gap-2 border-b border-slate-800 px-3 py-1.5">
                  <span className="text-[10px] text-slate-500">
                    {visibleEvents.length} из {events.length}
                  </span>
                  <button
                    type="button"
                    onClick={() => setOnlySelectedEvents((value) => !value)}
                    disabled={!selectedStage}
                    className={`rounded border px-2 py-0.5 text-[10px] ${
                      onlySelectedEvents
                        ? 'border-indigo-700 bg-indigo-950 text-indigo-300'
                        : 'border-slate-700 bg-slate-900 text-slate-400 hover:text-slate-200'
                    } disabled:opacity-40`}
                    title={selectedStage ? 'Показать только события выбранного блока' : 'Сначала выберите этап'}
                  >
                    Только выбранный блок
                  </button>
                </div>
                <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-2 font-mono text-[11px]">
                  {visibleEvents.length === 0 && (
                    <div className="p-4 text-center font-sans text-xs text-slate-500">
                      {isActiveRun ? 'Ожидаем события SSE; статус также обновляется опросом.' : 'События для этого запуска не сохранены в текущей сессии.'}
                    </div>
                  )}
                  {visibleEvents.map((event) => {
                    const parsed = event.timestamp ? new Date(event.timestamp) : null;
                    const timeLabel = parsed && !Number.isNaN(parsed.getTime())
                      ? parsed.toLocaleTimeString()
                      : '—';
                    const payload = asObject(event.payload);
                    const eventStatus = asString(payload.status);
                    const eventError = asString(payload.error);
                    const eventVerdict = asString(payload.verdict);
                    const payloadText = JSON.stringify(event.payload ?? null);
                    const shortPayload = payloadText.length > 320 ? `${payloadText.slice(0, 320)}…` : payloadText;
                    const matchesSelected =
                      selectedStage && event.nodeId === selectedStage.nodeRun.node_id;
                    return (
                      <div
                        key={`${event.seq}-${event.type}`}
                        className={`rounded border p-2 space-y-0.5 ${
                          matchesSelected
                            ? 'border-indigo-800/70 bg-indigo-950/20'
                            : 'border-slate-800/80 bg-slate-950/80'
                        }`}
                      >
                        <div className="flex items-center justify-between gap-2 text-[10px] text-slate-500">
                          <span className="truncate">#{event.seq} • {eventLabel(event.type)}</span>
                          <span className="shrink-0">{timeLabel}</span>
                        </div>
                        {event.nodeId && (
                          <div className="text-[10px] font-semibold text-indigo-400">
                            Блок: {event.nodeId}
                          </div>
                        )}
                        {event.type === 'node_status' && eventStatus && (
                          <div className="text-[10px] text-slate-300">
                            Статус: <strong>{eventStatus}</strong>
                            {payload.duration_ms !== undefined && ` · ${formatDuration(asNumber(payload.duration_ms))}`}
                            {eventError && ` · ${eventError}`}
                          </div>
                        )}
                        {event.type === 'run_status' && eventStatus && (
                          <div className="text-[10px] text-slate-300">
                            Запуск: <strong>{eventStatus}</strong>
                            {eventVerdict && ` · вердикт ${eventVerdict}`}
                          </div>
                        )}
                        <div className="text-[10px] break-all text-slate-400" title={payloadText.length > 320 ? payloadText : undefined}>
                          {shortPayload}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};