import React from 'react';
import {
  Activity, Bug, FileText, GitCompare, History, Layers, LogOut, Map,
  Play, RefreshCw, ShieldCheck, Upload, Loader2,
} from 'lucide-react';
import {
  ArtifactManifest, Finding, NodeKind, NodeRunRecord, NodeRunStatus, ReportData,
  RunSummary, WorkflowDraft, WorkflowNode,
} from '../types';
import { NodePalette } from './NodePalette';
import { WorkflowCanvas } from './WorkflowCanvas';
import { InspectorPanel } from './InspectorPanel';
import { LiveRunView } from './LiveRunView';
import { FindingsView } from './FindingsView';
import { ReportView } from './ReportView';
import { SecurityMapScreen } from './SecurityMapScreen';
import { ComparisonView } from './ComparisonView';
import { RepositorySourceView } from './RepositorySourceView';
import { artifactLabel, artifactRepository, findArtifact } from '../repository';

export type UserSection = 'editor' | 'history' | 'live' | 'problems' | 'map' | 'report' | 'comparison';

interface UserWorkspaceProps {
  workflows: Array<{ id: string; name?: string; draft?: WorkflowDraft }>;
  workflowId: string;
  draft: WorkflowDraft;
  selectedNode: WorkflowNode | null;
  selectedNodeId: string | null;
  artifacts: ArtifactManifest[];
  currentArtifactId: string;
  runs: RunSummary[];
  currentRun: RunSummary | null;
  currentRunId: string | null;
  findings: Finding[];
  report: ReportData | null;
  nodeRuns: NodeRunRecord[];
  events: Array<{ seq: number; timestamp: string; type: string; nodeId?: string; payload: any }>;
  failureReason?: string;
  isRunning: boolean;
  runsLoading: boolean;
  isSavingDraft: boolean;
  validationIssues: Array<{ level: string; message: string }>;
  section: UserSection;
  onSection: (section: UserSection) => void;
  onDraftChange: (draft: WorkflowDraft) => void;
  onSelectWorkflow: (workflowId: string) => void;
  onCreateWorkflow: () => void;
  onUpload: () => void;
  onSelectArtifact: (artifactId: string) => void;
  onRun: () => void;
  onSaveDraft: () => void;
  onResetDraft: () => void;
  onAddNode: (kind: NodeKind) => void;
  onUpdateNodeConfig: (nodeId: string, config: Record<string, any>, title?: string) => void;
  onDeleteNode: (nodeId: string) => void;
  onSelectNode: (nodeId: string | null) => void;
  onSelectRun: (runId: string) => void;
  onRefreshRuns: () => void;
  onLogout: () => void;
}

const verdictLabel = (verdict: RunSummary['security_verdict'], findings: number): string => {
  if (verdict === 'pass') return findings > 0 ? 'Нет блокирующих проблем' : 'Проблем не найдено';
  if (verdict === 'fail') return 'Найдены проблемы';
  if (verdict === 'inconclusive') return 'Неполная проверка';
  return 'Проверка идёт';
};

const formatDate = (value?: string): string => {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? '—'
    : date.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
};

export const UserWorkspace: React.FC<UserWorkspaceProps> = ({
  workflows,
  workflowId,
  draft,
  selectedNode,
  selectedNodeId,
  artifacts,
  currentArtifactId,
  runs,
  currentRun,
  currentRunId,
  findings,
  report,
  nodeRuns,
  events,
  failureReason,
  isRunning,
  runsLoading,
  isSavingDraft,
  validationIssues,
  section,
  onSection,
  onDraftChange,
  onSelectWorkflow,
  onCreateWorkflow,
  onUpload,
  onSelectArtifact,
  onRun,
  onSaveDraft,
  onResetDraft,
  onAddNode,
  onUpdateNodeConfig,
  onDeleteNode,
  onSelectNode,
  onSelectRun,
  onRefreshRuns,
  onLogout,
}) => {
  const nodeStatuses: Record<string, NodeRunStatus> = {};
  nodeRuns.forEach((nodeRun) => {
    nodeStatuses[nodeRun.node_id] = nodeRun.status;
  });

  const workflowRuns = runs.filter((run) => run.workflow_id === workflowId);
  const workflowName = workflows.find((workflow) => workflow.id === workflowId)?.name || draft.name || 'Мой сценарий';
  const selectedArtifact = findArtifact(currentArtifactId, artifacts);
  const selectedRepository = artifactRepository(selectedArtifact);

  const nav: Array<{ id: UserSection; label: string; icon: React.ComponentType<{ size?: number }>; count?: number }> = [
    { id: 'editor', label: 'Редактор', icon: Layers },
    { id: 'history', label: 'История', icon: History },
    { id: 'live', label: 'Ход проверки', icon: Activity },
    { id: 'problems', label: 'Проблемы', icon: Bug, count: findings.length },
    { id: 'map', label: 'Карта доступа', icon: Map },
    { id: 'report', label: 'Отчёт', icon: FileText },
    { id: 'comparison', label: 'Сравнение', icon: GitCompare },
  ];

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 bg-slate-900">
        <div className="flex flex-wrap items-center gap-2 px-3 py-2 sm:px-4">
          <div className="flex min-w-0 items-center gap-2.5">
            <div className="rounded-xl bg-gradient-to-tr from-indigo-600 to-violet-500 p-2 text-white">
              <ShieldCheck size={18} />
            </div>
            <div className="min-w-0">
              <div className="text-sm font-bold text-white">Проверка безопасности кода</div>
              <div className="hidden text-[11px] text-slate-400 sm:block">
                Соберите свою проверку из блоков
              </div>
            </div>
          </div>

          <div className="ml-auto flex items-center gap-1.5">
            <button
              onClick={onUpload}
              className="flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800 px-2.5 py-1.5 text-xs text-slate-200 hover:bg-slate-700"
            >
              <Upload size={13} /> <span className="hidden sm:inline">ZIP или репозиторий</span><span className="sm:hidden">Проект</span>
            </button>
            <button
              onClick={onRun}
              disabled={isRunning || !currentArtifactId}
              className="flex items-center gap-1.5 rounded-lg bg-emerald-600 px-2.5 py-1.5 text-xs font-semibold text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
              title={!currentArtifactId ? 'Сначала выберите или импортируйте проект' : 'Сохранить сценарий и запустить проверку'}
            >
              {isRunning ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
              {isRunning ? 'Проверяем…' : <><span className="hidden sm:inline">Запустить проверку</span><span className="sm:hidden">Запуск</span></>}
            </button>
            <button
              onClick={onLogout}
              className="flex items-center gap-1 rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5 text-[11px] text-slate-400 hover:bg-slate-800 hover:text-white"
            >
              <LogOut size={13} /> <span className="hidden sm:inline">Выйти</span>
            </button>
          </div>

          <nav className="order-last flex w-full gap-1 overflow-x-auto rounded-xl border border-slate-800 bg-slate-950/60 p-1 sm:order-none sm:ml-0">
            {nav.map((item) => {
              const Icon = item.icon;
              return (
                <button
                  key={item.id}
                  onClick={() => onSection(item.id)}
                  className={`flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium ${
                    section === item.id
                      ? 'bg-indigo-600 text-white'
                      : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
                  }`}
                >
                  <Icon size={13} /> {item.label}
                  {item.count ? (
                    <span className="rounded-full border border-rose-800 bg-rose-950 px-1.5 text-[10px] text-rose-300">
                      {item.count}
                    </span>
                  ) : null}
                </button>
              );
            })}
          </nav>
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-hidden">
        {section === 'editor' && (
          <div className="flex h-full min-h-0 flex-col overflow-hidden">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-slate-800 bg-slate-900/40 px-3 py-1.5 text-[11px] text-slate-400">
              <span>Сценарий: <span className="font-semibold text-slate-200">{workflowName}</span></span>
              {selectedArtifact ? (
                <span className="min-w-0 truncate">
                  Проект: <span className="text-slate-200">{artifactLabel(selectedArtifact)}</span>
                </span>
              ) : (
                <span className="text-amber-300">Проект не выбран — импортируйте ZIP или репозиторий</span>
              )}
              {selectedRepository && <RepositorySourceView repository={selectedRepository} showProvider={false} />}
              <span className="ml-auto hidden sm:inline">Блоков: {draft.nodes.length} · Связей: {draft.edges.length}</span>
            </div>
            <div className="flex min-h-0 flex-1 flex-col overflow-hidden md:flex-row">
              <NodePalette onAddNode={onAddNode} />
              <WorkflowCanvas
                draft={draft}
                onDraftChange={onDraftChange}
                selectedNodeId={selectedNodeId}
                onSelectNode={onSelectNode}
                nodeStatuses={nodeStatuses}
                validationIssues={validationIssues}
                onSaveDraft={onSaveDraft}
                onResetToTemplate={onResetDraft}
                isSaving={isSavingDraft}
                workflows={workflows}
                workflowId={workflowId}
                onSelectWorkflow={onSelectWorkflow}
                onCreateWorkflow={onCreateWorkflow}
                artifacts={artifacts}
                currentArtifactId={currentArtifactId}
                onSelectArtifact={onSelectArtifact}
              />
              {selectedNode && (
                <div className="fixed inset-x-0 bottom-0 z-30 max-h-[45vh] overflow-hidden shadow-2xl md:static md:z-auto md:max-h-none md:shrink-0">
                  <InspectorPanel
                    node={selectedNode}
                    artifacts={artifacts}
                    onUpdateConfig={onUpdateNodeConfig}
                    onDeleteNode={onDeleteNode}
                    onClose={() => onSelectNode(null)}
                  />
                </div>
              )}
            </div>
          </div>
        )}

        {section === 'history' && (
          <div className="h-full overflow-y-auto p-4">
            <div className="mx-auto max-w-5xl">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h1 className="text-lg font-bold text-white">История проверок</h1>
                  <p className="text-xs text-slate-400">Только запуски сценария «{workflowName}».</p>
                </div>
                <button
                  onClick={onRefreshRuns}
                  disabled={runsLoading}
                  className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-2.5 py-1.5 text-[11px] text-slate-300 hover:bg-slate-800 disabled:opacity-50"
                >
                  <RefreshCw size={12} className={runsLoading ? 'animate-spin' : ''} /> Обновить
                </button>
              </div>
              {workflowRuns.length === 0 ? (
                <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-6 text-center text-sm text-slate-500">
                  Для этого сценария проверок пока нет. Соберите блоки в редакторе и нажмите «Запустить проверку».
                </div>
              ) : (
                <div className="space-y-1.5">
                  {workflowRuns.map((run) => {
                    const artifact = findArtifact(run.artifact_id, artifacts);
                    const repository = artifactRepository(artifact);
                    return (
                      <button
                        key={run.id}
                        onClick={() => { onSelectRun(run.id); onSection('live'); }}
                        className={`flex w-full flex-wrap items-center justify-between gap-2 rounded-xl border px-3 py-2.5 text-left text-xs ${
                          run.id === currentRunId
                            ? 'border-indigo-700 bg-indigo-950/40'
                            : 'border-slate-800 bg-slate-900/60 hover:bg-slate-800/60'
                        }`}
                      >
                        <span className="min-w-0">
                          <span className="block truncate font-medium text-slate-200">{artifactLabel(artifact)}</span>
                          {repository && <RepositorySourceView repository={repository} className="mt-0.5" showProvider={false} />}
                          <span className="text-[11px] text-slate-500">{formatDate(run.created_at)}</span>
                        </span>
                        <span className="flex flex-wrap items-center gap-2 text-[11px]">
                          <span className={run.security_verdict === 'fail' ? 'text-rose-300' : run.security_verdict === 'pass' ? 'text-emerald-300' : 'text-amber-300'}>
                            {verdictLabel(run.security_verdict, run.total_findings)}
                          </span>
                          <span className="text-slate-500">{run.total_findings} проблем</span>
                          <span className="rounded border border-slate-700 px-2 py-1 text-[10px] text-slate-300">Открыть</span>
                        </span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        )}

        {section === 'live' && (
          <LiveRunView
            currentRun={currentRun}
            nodeRuns={nodeRuns}
            events={events}
            onSelectNode={(nodeId) => { onSelectNode(nodeId); onSection('editor'); }}
            onRefresh={currentRunId ? () => onSelectRun(currentRunId) : undefined}
          />
        )}

        {section === 'problems' && (
          findings.length > 0 ? (
            <div className="h-full overflow-y-auto">
              <FindingsView
                findings={findings}
                aiItems={report?.run_id === currentRunId ? report.ai_items : []}
                aiStatus={report?.run_id === currentRunId ? report.ai_status : undefined}
                onOpenReport={() => onSection('report')}
              />
            </div>
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center text-slate-400">
              <Bug size={32} className="text-slate-600" />
              <p className="text-sm">В выбранной проверке проблем нет либо она ещё не завершена.</p>
              <button onClick={() => onSection('history')} className="text-xs text-indigo-300 hover:text-indigo-200">
                Открыть историю проверок
              </button>
            </div>
          )
        )}

        {section === 'map' && <SecurityMapScreen key={currentRunId ?? 'no-run'} runId={currentRunId} />}

        {section === 'report' && (
          <div className="h-full overflow-y-auto">
            <ReportView
              report={report}
              runId={currentRunId}
              runStatus={currentRun?.execution_status}
              failureReason={failureReason}
              nodeRuns={nodeRuns}
            />
          </div>
        )}

        {section === 'comparison' && (
          <div className="h-full overflow-y-auto">
            <ComparisonView runs={runs} artifacts={artifacts} workflows={workflows} onRefreshRuns={onRefreshRuns} />
          </div>
        )}
      </main>
    </div>
  );
};