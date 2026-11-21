import React, { useEffect, useState } from 'react';
import {
  KeyRound, PackageSearch, FileCode2,
  Search, ChevronRight, CheckCircle2
} from 'lucide-react';
import { AiScanResult, Finding, Severity } from '../types';
import { EvidenceCard } from './EvidenceCard';
import { AiHypothesesPanel } from './AiHypothesesPanel';
import { ResponsibilityPanel } from './ResponsibilityPanel';

interface FindingsViewProps {
  findings: Finding[];
  runId?: string | null;
  aiScan?: AiScanResult | null;
  aiItems?: Array<{ findingId: string; explanation: string; remediation: string; suggestedFixedVersion?: string }>;
  aiStatus?: string;
  onOpenReport?: () => void;
}

const SEVERITY_LABEL: Record<Severity, string> = {
  critical: 'Критично',
  high: 'Высокий',
  medium: 'Средний',
  low: 'Низкий',
  info: 'Инфо',
};

const CONFIDENCE_LABEL: Record<string, string> = {
  confirmed: 'подтверждено',
  potential: 'возможно',
  inconclusive: 'не подтверждено',
};

type SortMode = 'severity' | 'file';

const SEVERITY_RANK: Record<Severity, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
  info: 4,
};

const CONFIDENCE_RANK: Record<string, number> = {
  confirmed: 0,
  potential: 1,
  inconclusive: 2,
};

const compareText = (left: string, right: string): number =>
  left < right ? -1 : left > right ? 1 : 0;

function compareFindings(left: Finding, right: Finding, mode: SortMode): number {
  if (mode === 'severity') {
    const severityDiff =
      (SEVERITY_RANK[left.severity] ?? 9) - (SEVERITY_RANK[right.severity] ?? 9);
    if (severityDiff !== 0) return severityDiff;
    const confidenceDiff =
      (CONFIDENCE_RANK[left.confidence] ?? 9) - (CONFIDENCE_RANK[right.confidence] ?? 9);
    if (confidenceDiff !== 0) return confidenceDiff;
  }
  const pathDiff = compareText(left.primaryLocation.path, right.primaryLocation.path);
  if (pathDiff !== 0) return pathDiff;
  const lineDiff = left.primaryLocation.startLine - right.primaryLocation.startLine;
  if (lineDiff !== 0) return lineDiff;
  const columnDiff = left.primaryLocation.startColumn - right.primaryLocation.startColumn;
  if (columnDiff !== 0) return columnDiff;
  const ruleDiff = compareText(left.ruleId, right.ruleId);
  if (ruleDiff !== 0) return ruleDiff;
  return compareText(left.id, right.id);
}

export const FindingsView: React.FC<FindingsViewProps> = ({ findings, runId = null, aiScan = null, aiItems = [], aiStatus, onOpenReport }) => {
  const [selectedFindingId, setSelectedFindingId] = useState<string | null>(null);
  const [analyzerFilter, setAnalyzerFilter] = useState<string>('all');
  const [severityFilter, setSeverityFilter] = useState<string>('all');
  const [sortMode, setSortMode] = useState<SortMode>('severity');
  const [searchQuery, setSearchQuery] = useState<string>('');

  useEffect(() => {
    if (selectedFindingId && !findings.some((finding) => finding.id === selectedFindingId)) {
      setSelectedFindingId(null);
    }
  }, [findings, selectedFindingId]);

  // Derive the object from the current findings array: a new run can reuse a
  // finding id with a different location/evidence, so holding the object is stale.
  const selectedFinding = selectedFindingId
    ? findings.find((finding) => finding.id === selectedFindingId) ?? null
    : null;
  const selectedAi = selectedFinding
    ? aiItems.find((item) => item.findingId === selectedFinding.id)
    : undefined;

  const filteredFindings = findings
    .filter((f) => {
      if (analyzerFilter !== 'all' && f.analyzer !== analyzerFilter) return false;
      if (severityFilter !== 'all' && f.severity !== severityFilter) return false;
      if (searchQuery) {
        const q = searchQuery.toLowerCase();
        return (
          f.ruleId.toLowerCase().includes(q) ||
          f.primaryLocation.path.toLowerCase().includes(q) ||
          f.severityBasis.toLowerCase().includes(q) ||
          f.advisoryIds.some((cve) => cve.toLowerCase().includes(q))
        );
      }
      return true;
    })
    .sort((left, right) => compareFindings(left, right, sortMode));

  const critCount = findings.filter((f) => f.severity === 'critical').length;
  const highCount = findings.filter((f) => f.severity === 'high').length;
  const medCount = findings.filter((f) => f.severity === 'medium').length;
  const lowCount = findings.filter((f) => f.severity === 'low').length;
  const infoCount = findings.filter((f) => f.severity === 'info').length;
  const confirmedCount = findings.filter((f) => f.confidence === 'confirmed').length;
  const potentialCount = findings.filter((f) => f.confidence === 'potential').length;
  const inconclusiveCount = findings.filter((f) => f.confidence === 'inconclusive').length;

  return (
    <div className="flex-1 h-full flex flex-col lg:flex-row overflow-hidden bg-slate-950">
      {/* Main List */}
      <div className="flex-1 flex flex-col h-full overflow-hidden">
        {/* Filter bar */}
        <div className="p-3.5 bg-slate-900 border-b border-slate-800 space-y-2">
          <div className="relative w-full">
              <Search size={14} className="absolute left-2.5 top-2.5 text-slate-500" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Поиск по файлу, CVE, правилу..."
                className="w-full bg-slate-800 border border-slate-700 rounded-lg pl-8 pr-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
              />
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {/* Analyzer filter buttons */}
            <div className="flex w-full sm:w-auto shrink-0 items-center bg-slate-950/80 p-0.5 rounded-lg border border-slate-800 text-xs overflow-x-auto max-w-full">
              {['all', 'secret', 'sca', 'sast'].map((a) => (
                <button
                  key={a}
                  onClick={() => setAnalyzerFilter(a)}
                  className={`shrink-0 px-2.5 py-1 rounded-md text-[11px] font-medium transition cursor-pointer capitalize ${
                    analyzerFilter === a
                      ? 'bg-indigo-600 text-white'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {a === 'all' ? 'Все анализаторы' : a.toUpperCase()}
                </button>
              ))}
            </div>

            {/* Severity filter */}
            <select
              value={severityFilter}
              onChange={(e) => setSeverityFilter(e.target.value)}
              className="w-full sm:w-auto bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-slate-300 focus:outline-none focus:border-indigo-500"
            >
              <option value="all">Любой риск</option>
              <option value="critical">Критично</option>
              <option value="high">Высокий</option>
              <option value="medium">Средний</option>
              <option value="low">Низкий</option>
              <option value="info">Инфо</option>
            </select>

            {/* Sort mode */}
            <label className="flex w-full sm:w-auto items-center gap-1.5">
              <span className="sr-only">Сортировка проблем</span>
              <select
                value={sortMode}
                onChange={(e) => setSortMode(e.target.value === 'file' ? 'file' : 'severity')}
                className="w-full sm:w-auto bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-slate-300 focus:outline-none focus:border-indigo-500"
                title="Порядок списка проблем"
              >
                <option value="severity">Сначала критичные</option>
                <option value="file">По файлу и строке</option>
              </select>
            </label>

          {/* Counts */}
          <div className="flex w-full sm:w-auto sm:ml-auto flex-wrap items-center gap-2 sm:gap-3 text-xs">
            <span className="text-rose-400 font-semibold font-mono">
              Критично: {critCount}
            </span>
            <span className="text-orange-400 font-semibold font-mono">
              Высокий: {highCount}
            </span>
            <span className="text-yellow-400 font-semibold font-mono">
              Средний: {medCount}
            </span>
            <span className="text-sky-400 font-mono">
              Низкий: {lowCount}
            </span>
            <span className="text-slate-500 font-mono">
              Инфо: {infoCount}
            </span>
            <span className="text-slate-400 font-mono">
              Проблем: {findings.length}
            </span>
          </div>
          <div className="w-full text-[10px] text-slate-500">
            Находок: {findings.length} · подтверждено: {confirmedCount} · возможно: {potentialCount} · не подтверждено: {inconclusiveCount}
          </div>
          </div>
        </div>

        {aiScan && <AiHypothesesPanel aiScan={aiScan} />}

        {/* Findings List */}
        <div className="flex-1 overflow-y-auto p-4 space-y-2.5">
          {filteredFindings.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center text-slate-500">
              <CheckCircle2 size={36} className="text-emerald-500 mb-2" />
              <p className="text-sm font-medium">Проблем по заданным фильтрам не обнаружено</p>
            </div>
          ) : (
            filteredFindings.map((f) => {
              const isSelected = selectedFinding?.id === f.id;
              const isCritical = f.severity === 'critical';
              const isHigh = f.severity === 'high';
              const severityBadgeClass =
                isCritical
                  ? 'bg-rose-950 text-rose-300 border border-rose-800'
                  : isHigh
                    ? 'bg-orange-950 text-orange-300 border border-orange-800'
                    : f.severity === 'medium'
                      ? 'bg-yellow-950 text-yellow-300 border border-yellow-800'
                      : f.severity === 'low'
                        ? 'bg-sky-950 text-sky-300 border border-sky-800'
                        : 'bg-slate-800 text-slate-300 border border-slate-700';

              return (
                <div
                  key={f.id}
                  onClick={() => setSelectedFindingId(f.id)}
                  className={`p-3.5 rounded-xl border transition-all cursor-pointer flex items-center justify-between gap-4 ${
                    isSelected
                      ? 'bg-slate-800 border-indigo-500 shadow-md ring-1 ring-indigo-400/50'
                      : 'bg-slate-900/80 hover:bg-slate-800/80 border-slate-800'
                  }`}
                >
                  <div className="flex items-start gap-3 min-w-0">
                    <div
                      className={`mt-0.5 p-2 rounded-lg shrink-0 ${
                        f.analyzer === 'secret'
                          ? 'bg-amber-950/60 text-amber-400 border border-amber-800/60'
                          : f.analyzer === 'sca'
                          ? 'bg-orange-950/60 text-orange-400 border border-orange-800/60'
                          : 'bg-rose-950/60 text-rose-400 border border-rose-800/60'
                      }`}
                    >
                      {f.analyzer === 'secret' ? (
                        <KeyRound size={16} />
                      ) : f.analyzer === 'sca' ? (
                        <PackageSearch size={16} />
                      ) : (
                        <FileCode2 size={16} />
                      )}
                    </div>

                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span
                          className={`px-1.5 py-0.2 rounded text-[9px] font-bold uppercase tracking-wider ${severityBadgeClass}`}
                        >
                          {SEVERITY_LABEL[f.severity] || f.severity}
                        </span>
                        <span className="font-semibold text-xs text-slate-100">
                          {f.ruleId}
                        </span>
                        {f.advisoryIds && f.advisoryIds.length > 0 && (
                          <span className="text-[10px] font-mono text-rose-400">
                            {f.advisoryIds.join(', ')}
                          </span>
                        )}
                      </div>

                      <div className="text-[11px] text-slate-400 mt-1 truncate">
                        {f.severityBasis}
                      </div>

                      <div className="flex items-center gap-3 text-[10px] font-mono text-slate-500 mt-1.5">
                        <span className="text-indigo-400 truncate">
                          {f.primaryLocation.path}:{f.primaryLocation.startLine}
                        </span>
                        <span>•</span>
                        <span>Достоверность: {CONFIDENCE_LABEL[f.confidence] || f.confidence}</span>
                      </div>
                    </div>
                  </div>

                  <div className="shrink-0 flex items-center text-slate-500">
                    <ChevronRight size={18} />
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* Right: Evidence Card + responsibility enrichment (separate from analyzer facts) */}
      {selectedFinding && (
        <div className="w-full lg:w-[450px] max-h-[75vh] lg:max-h-none flex flex-col h-full shrink-0 border-t lg:border-t-0 lg:border-l border-slate-800 bg-slate-900 overflow-y-auto lg:overflow-hidden">
          <div className="flex-1 min-h-0 overflow-hidden">
            <EvidenceCard
              finding={selectedFinding}
              aiEnrichment={selectedAi}
              aiStatus={aiStatus}
              onOpenReport={onOpenReport}
              onClose={() => setSelectedFindingId(null)}
            />
          </div>
          <ResponsibilityPanel runId={runId} findingId={selectedFinding.id} />
        </div>
      )}
    </div>
  );
};
