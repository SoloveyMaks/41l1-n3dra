import React, { useState, useEffect, useRef, useCallback } from 'react';
import { WorkflowCanvas } from './components/WorkflowCanvas';
import { NodePalette } from './components/NodePalette';
import { InspectorPanel } from './components/InspectorPanel';
import { LiveRunView } from './components/LiveRunView';
import { FindingsView } from './components/FindingsView';
import { ReportView } from './components/ReportView';
import { SecurityMapScreen } from './components/SecurityMapScreen';
import { UploadModal } from './components/UploadModal';
import { ScheduleModal } from './components/ScheduleModal';
import { AiTrustModal } from './components/AiTrustModal';
import { AccessGate } from './components/AccessGate';
import { ComparisonView } from './components/ComparisonView';
import { UserDashboard } from './components/UserDashboard';
import { AdminShell, AdminSection } from './components/AdminShell';
import {
  AdminAccessPanel,
  AdminAiPanel,
  AdminAnalyzersPanel,
  AdminOverviewPanel,
  AdminProjectsPanel,
} from './components/AdminPanels';

import {
  AdminOverview,
  ArtifactManifest,
  Finding,
  NodeKind,
  NodeRunRecord,
  NodeRunStatus,
  ReportData,
  RunSummary,
  SessionRole,
  SessionState,
  WorkflowDraft,
  WorkflowNode,
} from './types';
import { apiClient, AuthRequiredError } from './api/client';

type Notice = { kind: 'ok' | 'error'; text: string };

const EMPTY_DRAFT: WorkflowDraft = {
  schemaVersion: 1,
  name: 'Загрузка сценария...',
  description: '',
  nodes: [],
  edges: [],
};

export const App: React.FC = () => {
  const [adminSection, setAdminSection] = useState<AdminSection>('overview');
  const [adminOverview, setAdminOverview] = useState<AdminOverview | null>(null);

  // Access gate (public demo requires an access code; local mode gets a session automatically)
  const [session, setSession] = useState<SessionState | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [authBusy, setAuthBusy] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);

  // Modals
  const [isUploadOpen, setIsUploadOpen] = useState(false);
  
  const [isAiTestOpen, setIsAiTestOpen] = useState(false);

  // Workflow state
  const [workflows, setWorkflows] = useState<any[]>([]);
  const [workflowId, setWorkflowId] = useState<string>('wf_secure_pr_gate');
  const [draft, setDraft] = useState<WorkflowDraft>(EMPTY_DRAFT);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [validationIssues, setValidationIssues] = useState<Array<{ level: string; message: string }>>([]);
  const [isSavingDraft, setIsSavingDraft] = useState(false);

  // Artifacts state
  const [artifacts, setArtifacts] = useState<ArtifactManifest[]>([]);
  const [currentArtifactId, setCurrentArtifactId] = useState<string>('');

  // Run history and selected run state
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);
  const [currentRunId, setCurrentRunId] = useState<string | null>(null);
  const [currentRun, setCurrentRun] = useState<RunSummary | null>(null);
  const [nodeRuns, setNodeRuns] = useState<NodeRunRecord[]>([]);
  const [events, setEvents] = useState<Array<{ seq: number; timestamp: string; type: string; nodeId?: string; payload: any }>>([]);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [report, setReport] = useState<ReportData | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);

  const eventSourceRef = useRef<EventSource | null>(null);
  const selectedRunRef = useRef<string | null>(null);
  const runLoadSeq = useRef(0);
  const validateSeq = useRef(0);
  const workflowSelectSeq = useRef(0);
  const noticeTimer = useRef<number | null>(null);

  const showNotice = useCallback((kind: Notice['kind'], text: string) => {
    setNotice({ kind, text });
    if (noticeTimer.current !== null) window.clearTimeout(noticeTimer.current);
    noticeTimer.current = window.setTimeout(() => setNotice(null), 7000);
  }, []);

  useEffect(() => () => {
    if (noticeTimer.current !== null) window.clearTimeout(noticeTimer.current);
  }, []);

  const closeSSE = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
  }, []);

  const refreshRuns = useCallback(async (): Promise<RunSummary[]> => {
    setRunsLoading(true);
    try {
      const list = await apiClient.listRuns();
      setRuns(list);
      return list;
    } catch (e: any) {
      if (e instanceof AuthRequiredError) {
        setSession((prev) => ({ authenticated: false, authRequired: true, workspaceId: prev?.workspaceId || '', expiresInSec: 0 }));
      } else {
        showNotice('error', e?.message || 'Не удалось загрузить историю запусков');
      }
      return [];
    } finally {
      setRunsLoading(false);
    }
  }, [showNotice]);

  const loadRunDetails = useCallback(async (runId: string) => {
    const seq = ++runLoadSeq.current;
    try {
      const [runInfo, nodes, runFindings] = await Promise.all([
        apiClient.getRun(runId),
        apiClient.getRunNodes(runId),
        apiClient.getRunFindings(runId),
      ]);
      let runReport: ReportData | null = null;
      const isTerminal = runInfo.execution_status !== 'running' && runInfo.execution_status !== 'queued';
      if (isTerminal) {
        try {
          runReport = await apiClient.getRunReport(runId);
        } catch {
          runReport = null;
        }
      }
      if (seq !== runLoadSeq.current || selectedRunRef.current !== runId) return;

      setCurrentRun(runInfo);
      setNodeRuns(nodes);
      setFindings(runFindings);
      setReport(runReport);
      setIsRunning(runInfo.execution_status === 'running' || runInfo.execution_status === 'queued');
      if (runInfo.execution_status !== 'running' && runInfo.execution_status !== 'queued') {
        void refreshRuns();
      }
    } catch (e: any) {
      if (seq !== runLoadSeq.current || selectedRunRef.current !== runId) return;
      if (e instanceof AuthRequiredError) {
        setSession((prev) => ({ authenticated: false, authRequired: true, workspaceId: prev?.workspaceId || '', expiresInSec: 0 }));
      } else {
        showNotice('error', e?.message || 'Не удалось загрузить запуск');
      }
    }
  }, [refreshRuns, showNotice]);

  const connectSSE = useCallback((runId: string) => {
    closeSSE();
    const source = apiClient.createEventSource(runId, 0);
    eventSourceRef.current = source;

    const handleEvent = (message: MessageEvent) => {
      if (selectedRunRef.current !== runId) return;
      try {
        const event = JSON.parse(message.data);
        setEvents((prev) => {
          if (prev.some((item) => item.seq === event.seq)) return prev;
          const next = [...prev, event];
          return next.length > 1000 ? next.slice(-1000) : next;
        });
        if (event.type === 'run_status') {
          const status = event.payload?.status;
          if (status === 'succeeded' || status === 'failed' || status === 'cancelled' || status === 'interrupted') {
            setIsRunning(false);
            closeSSE();
            void loadRunDetails(runId);
            void refreshRuns();
          }
        } else if (event.type === 'node_status') {
          void loadRunDetails(runId);
        }
      } catch (error) {
        console.error('SSE parse error:', error);
      }
    };
    // Backend sends named events (`event: run_status`), so EventSource does
    // not dispatch them to `onmessage`; subscribe to the known event names too.
    source.onmessage = handleEvent;
    for (const eventName of ['run_status', 'node_status', 'artifact_status', 'finding_summary', 'coverage', 'edge_data', 'delivery_status']) {
      source.addEventListener(eventName, handleEvent as EventListener);
    }
    source.onerror = () => {
      // Polling below remains the source of truth while a run is active.
    };
  }, [closeSSE, loadRunDetails, refreshRuns]);

  const validateDraft = useCallback(async (wfId: string, nextDraft: WorkflowDraft) => {
    const seq = ++validateSeq.current;
    try {
      const result = await apiClient.validateWorkflow(wfId, nextDraft);
      if (seq === validateSeq.current) setValidationIssues(result.issues || []);
      return result;
    } catch (e: any) {
      if (e instanceof AuthRequiredError) {
        setSession((prev) => ({ authenticated: false, authRequired: true, workspaceId: prev?.workspaceId || '', expiresInSec: 0 }));
      }
      return null;
    }
  }, []);

  const loadInitialData = useCallback(async () => {
    try {
      const [artifactList, workflowList, runList] = await Promise.all([
        apiClient.listArtifacts(),
        apiClient.listWorkflows(),
        apiClient.listRuns(),
      ]);
      setArtifacts(artifactList);
      setCurrentArtifactId(artifactList[0]?.id || '');
      setWorkflows(workflowList);
      setRuns(runList);

      const selectedWorkflow =
        workflowList.find((item) => item.id === 'wf_secure_pr_gate') || workflowList[0];
      if (selectedWorkflow?.draft) {
        setWorkflowId(selectedWorkflow.id);
        setDraft(selectedWorkflow.draft);
        await validateDraft(selectedWorkflow.id, selectedWorkflow.draft);
      } else {
        showNotice('error', 'На сервере нет доступного workflow');
      }

      if (runList.length > 0) {
        const latest =
          runList.find((run) => run.workflow_id === 'wf_secure_pr_gate' && run.trigger_type !== 'schedule') ||
          runList.find((run) => run.trigger_type !== 'schedule') ||
          runList[0];
        selectedRunRef.current = latest.id;
        setCurrentRunId(latest.id);
        setCurrentRun(latest);
        await loadRunDetails(latest.id);
        connectSSE(latest.id);
      }
    } catch (e: any) {
      if (e instanceof AuthRequiredError) {
        setSession((prev) => ({ authenticated: false, authRequired: true, workspaceId: prev?.workspaceId || '', expiresInSec: 0 }));
      } else {
        setAuthError(e?.message || 'Сервер недоступен');
        showNotice('error', e?.message || 'Сервер недоступен');
      }
    }
  }, [connectSSE, loadRunDetails, showNotice, validateDraft]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const state = await apiClient.getSession();
        if (cancelled) return;
        setSession(state);
        if (!state.authRequired || state.authenticated) await loadInitialData();
      } catch (e: any) {
        if (cancelled) return;
        if (e instanceof AuthRequiredError) {
          setSession({ authenticated: false, authRequired: true, workspaceId: '', expiresInSec: 0 });
        } else {
          setAuthError(e?.message || 'Сервер недоступен');
          setSession({ authenticated: false, authRequired: true, workspaceId: '', expiresInSec: 0 });
        }
      } finally {
        if (!cancelled) setAuthLoading(false);
      }
    })();
    return () => {
      cancelled = true;
      closeSSE();
    };
  }, [closeSSE, loadInitialData]);

  // While a run is queued/running, poll as a fallback when SSE is silent or reconnects.
  useEffect(() => {
    if (!isRunning || !currentRunId) return;
    const timer = window.setInterval(() => {
      void loadRunDetails(currentRunId);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [currentRunId, isRunning, loadRunDetails]);

  const handleLogin = async (accessCode: string, role: SessionRole) => {
    setAuthBusy(true);
    setAuthError(null);
    try {
      const state = await apiClient.login(accessCode, role);
      if (role === 'admin' && state.role !== 'admin') {
        await apiClient.logout().catch(() => undefined);
        setAuthError('Этот код не даёт доступ к админ-панели');
        return;
      }
      setSession(state);
      await loadInitialData();
      setAdminSection('overview');
    } catch (e: any) {
      setAuthError(e?.message || 'Не удалось войти');
    } finally {
      setAuthBusy(false);
    }
  };

  const handleLogout = async () => {
    try {
      await apiClient.logout();
    } catch {
      // Cookie already gone or server unavailable; local state still resets.
    }
    closeSSE();
    selectedRunRef.current = null;
    runLoadSeq.current += 1;
    workflowSelectSeq.current += 1;
    setSession((prev) => ({ authenticated: false, authRequired: prev?.authRequired ?? true, workspaceId: prev?.workspaceId || '', expiresInSec: 0 }));
    setCurrentRunId(null);
    setCurrentRun(null);
    setNodeRuns([]);
    setFindings([]);
    setReport(null);
    setEvents([]);
    setRuns([]);
    setIsRunning(false);
    setSelectedNodeId(null);
  };

  const handleSelectRun = (runId: string) => {
    if (!runId) return;
    closeSSE();
    selectedRunRef.current = runId;
    setCurrentRunId(runId);
    setCurrentRun(null);
    setNodeRuns([]);
    setFindings([]);
    setReport(null);
    setEvents([]);
    setIsRunning(false);
    connectSSE(runId);
    void loadRunDetails(runId);
  };

  const handleSelectWorkflow = async (nextWorkflowId: string) => {
    if (!nextWorkflowId) return;
    const seq = ++workflowSelectSeq.current;
    try {
      const workflow = await apiClient.getWorkflow(nextWorkflowId);
      if (seq !== workflowSelectSeq.current) return;
      if (!workflow?.draft) {
        showNotice('error', 'У сценария нет сохранённого draft');
        return;
      }
      setWorkflowId(nextWorkflowId);
      setDraft(workflow.draft);
      setSelectedNodeId(null);
      await validateDraft(nextWorkflowId, workflow.draft);
      if (seq !== workflowSelectSeq.current) return;
      showNotice('ok', `Открыт сценарий: ${workflow.name || nextWorkflowId}`);
    } catch (e: any) {
      if (seq === workflowSelectSeq.current) showNotice('error', e?.message || 'Не удалось открыть сценарий');
    }
  };

  const handleDraftChange = (updated: WorkflowDraft) => {
    setDraft(updated);
    void validateDraft(workflowId, updated);
  };

  const handleSaveDraft = async () => {
    setIsSavingDraft(true);
    try {
      await apiClient.saveWorkflowDraft(workflowId, draft);
      const result = await validateDraft(workflowId, draft);
      if (result?.valid) {
        showNotice('ok', 'Draft сохранён, граф валиден');
      } else {
        showNotice('error', `Draft сохранён, но есть ошибки валидации (${result?.issues?.length || 0})`);
      }
    } catch (e: any) {
      showNotice('error', e?.message || 'Ошибка сохранения draft');
    } finally {
      setIsSavingDraft(false);
    }
  };

  const handleAddNode = (kind: NodeKind) => {
    const newId = `node_${kind}_${Date.now()}`;
    const newNode: WorkflowNode = {
      id: newId,
      kind,
      version: 1,
      title: kind.replace('_', ' ').replace(/\b\w/g, (c) => c.toUpperCase()),
      config: {},
      position: { x: 300 + Math.random() * 100, y: 150 + Math.random() * 100 },
    };
    handleDraftChange({ ...draft, nodes: [...draft.nodes, newNode] });
    setSelectedNodeId(newId);
  };

  const handleUpdateNodeConfig = (nodeId: string, newConfig: Record<string, any>, newTitle?: string) => {
    const updatedNodes = draft.nodes.map((node) => {
      if (node.id !== nodeId) return node;
      return {
        ...node,
        config: newConfig,
        title: newTitle !== undefined ? newTitle : node.title,
      };
    });
    handleDraftChange({ ...draft, nodes: updatedNodes });
  };

  const handleDeleteNode = (nodeId: string) => {
    handleDraftChange({
      ...draft,
      nodes: draft.nodes.filter((node) => node.id !== nodeId),
      edges: draft.edges.filter((edge) => edge.source !== nodeId && edge.target !== nodeId),
    });
    setSelectedNodeId(null);
  };

  const handleResetToTemplate = async () => {
    try {
      const workflow = await apiClient.getWorkflow(workflowId);
      if (!workflow?.draft) {
        showNotice('error', 'У сценария нет сохранённого draft');
        return;
      }
      setDraft(workflow.draft);
      setSelectedNodeId(null);
      await validateDraft(workflowId, workflow.draft);
      showNotice('ok', 'Draft сброшен к сохранённому состоянию');
    } catch (e: any) {
      showNotice('error', e?.message || 'Не удалось сбросить draft');
    }
  };

  const handleTriggerRun = async (skipDraftSave = false, navigate = true) => {
    if (isRunning) return;
    const hasErrors = validationIssues.some((issue) => issue.level === 'error');
    if (hasErrors) {
      if (navigate) setAdminSection('editor');
      showNotice('error', 'Сценарий проверки пока не готов к запуску');
      return;
    }
    setIsRunning(true);
    setEvents([]);
    setReport(null);
    try {
      if (!skipDraftSave) await apiClient.saveWorkflowDraft(workflowId, draft);
      const result = await apiClient.triggerRun(workflowId, currentArtifactId || undefined, 'manual');
      selectedRunRef.current = result.runId;
      setCurrentRunId(result.runId);
      setCurrentRun(null);
      setNodeRuns([]);
      setFindings([]);
      setReport(null);
      setSelectedNodeId(null);
      if (navigate) setAdminSection('live');
      connectSSE(result.runId);
      await loadRunDetails(result.runId);
      await refreshRuns();
    } catch (e: any) {
      setIsRunning(false);
      showNotice('error', e?.message || 'Ошибка запуска проверки');
    }
  };

  const resetRunSelection = () => {
    closeSSE();
    selectedRunRef.current = null;
    runLoadSeq.current += 1;
    setCurrentRunId(null);
    setCurrentRun(null);
    setNodeRuns([]);
    setFindings([]);
    setReport(null);
    setEvents([]);
    setIsRunning(false);
  };

  const handleLoadSample = async () => {
    try {
      const manifest = await apiClient.loadSampleArtifact();
      setArtifacts((prev) => [manifest, ...prev.filter((item) => item.id !== manifest.id)]);
      setCurrentArtifactId(manifest.id);
      if (session?.role !== 'admin') resetRunSelection();
      showNotice('ok', `Открыт пример: ${manifest.filename}`);
    } catch (e: any) {
      showNotice('error', e?.message || 'Не удалось открыть пример');
    }
  };

  useEffect(() => {
    if (session?.role !== 'admin') return;
    let cancelled = false;
    apiClient.getAdminOverview()
      .then((value) => { if (!cancelled) setAdminOverview(value); })
      .catch(() => { if (!cancelled) setAdminOverview(null); });
    return () => { cancelled = true; };
  }, [session?.role]);

  const handleArtifactLoaded = (manifest: ArtifactManifest) => {
    setArtifacts((prev) => [manifest, ...prev.filter((item) => item.id !== manifest.id)]);
    setCurrentArtifactId(manifest.id);
    handleDraftChange({
      ...draft,
      nodes: draft.nodes.map((node) =>
        node.kind === 'project_input'
          ? { ...node, config: { ...node.config, artifactId: manifest.id } }
          : node,
      ),
    });
    if (session?.role !== 'admin') resetRunSelection();
    showNotice('ok', `Артефакт выбран: ${manifest.filename}`);
  };

  const handleSelectArtifact = (artifactId: string) => {
    setCurrentArtifactId(artifactId);
    handleDraftChange({
      ...draft,
      nodes: draft.nodes.map((node) =>
        node.kind === 'project_input'
          ? { ...node, config: { ...node.config, artifactId } }
          : node,
      ),
    });
  };

  const handleUserSelectArtifact = (artifactId: string) => {
    handleSelectArtifact(artifactId);
    resetRunSelection();
  };

  const nodeStatusMap: Record<string, NodeRunStatus> = {};
  nodeRuns.forEach((nodeRun) => {
    nodeStatusMap[nodeRun.node_id] = nodeRun.status;
  });

  const selectedNode = draft.nodes.find((node) => node.id === selectedNodeId) || null;

  const isAdmin = session?.role === 'admin';
  const nodeFailureReason = nodeRuns.find((node) => node.error_message)?.error_message;
  const runFailureReason = [...events]
    .reverse()
    .find((event) => event.type === 'run_status' && Array.isArray(event.payload?.errors) && event.payload.errors.length > 0)
    ?.payload.errors.join('; ');
  const failureReason = nodeFailureReason || runFailureReason;

  const noticeNode = notice && (
    <div
      role={notice.kind === 'error' ? 'alert' : 'status'}
      className={`fixed bottom-3 left-3 z-40 max-w-sm rounded-lg border px-3.5 py-2.5 text-xs shadow-2xl ${
        notice.kind === 'error'
          ? 'border-rose-800 bg-rose-950/95 text-rose-200'
          : 'border-emerald-800 bg-emerald-950/95 text-emerald-200'
      }`}
    >
      {notice.text}
    </div>
  );

  if (authLoading) {
    return (
      <div className="min-h-screen w-screen bg-slate-950 text-slate-300 flex items-center justify-center text-sm">
        Проверяем доступ…
      </div>
    );
  }

  if (session?.authRequired && !session.authenticated) {
    return (
      <AccessGate
        busy={authBusy}
        error={authError}
        adminAvailable={session.adminAvailable}
        onSubmit={handleLogin}
      />
    );
  }

  if (!isAdmin) {
    return (
      <div className="h-screen w-screen overflow-hidden bg-slate-950">
        <UserDashboard
          artifacts={artifacts}
          currentArtifactId={currentArtifactId}
          runs={runs}
          workflows={workflows}
          currentRun={currentRun}
          currentRunId={currentRunId}
          findings={findings}
          report={report}
          nodeRuns={nodeRuns}
          failureReason={failureReason}
          isRunning={isRunning}
          runsLoading={runsLoading}
          onUpload={() => setIsUploadOpen(true)}
          onLoadSample={handleLoadSample}
          onSelectArtifact={handleUserSelectArtifact}
          onRun={() => void handleTriggerRun(true, false)}
          onSelectRun={handleSelectRun}
          onRefreshRuns={() => void refreshRuns()}
          onLogout={() => void handleLogout()}
        />
        <UploadModal
          isOpen={isUploadOpen}
          onClose={() => setIsUploadOpen(false)}
          onArtifactLoaded={handleArtifactLoaded}
          showTraining={false}
        />
        {noticeNode}
      </div>
    );
  }

  const adminContent = (() => {
    switch (adminSection) {
      case 'overview':
        return (
          <AdminOverviewPanel
            overview={adminOverview}
            artifacts={artifacts}
            runs={runs}
            findings={findings}
            currentRun={currentRun}
            report={report}
            nodeRuns={nodeRuns}
          />
        );
      case 'projects':
        return (
          <AdminProjectsPanel
            artifacts={artifacts}
            currentArtifactId={currentArtifactId}
            onSelectArtifact={handleSelectArtifact}
            runs={runs}
            currentRunId={currentRunId}
            onSelectRun={handleSelectRun}
            onSection={setAdminSection}
            onRefreshRuns={() => void refreshRuns()}
          />
        );
      case 'editor':
        return (
          <div className="flex h-full flex-col overflow-hidden">
            <div className="flex flex-wrap items-center gap-2 border-b border-slate-800 bg-slate-900/60 p-2">
              <button
                onClick={() => setIsUploadOpen(true)}
                className="rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-xs text-slate-300 hover:bg-slate-800"
              >
                Загрузить ZIP
              </button>
              <button
                onClick={() => void handleTriggerRun()}
                disabled={isRunning}
                className="rounded-lg bg-emerald-600 px-2.5 py-1.5 text-xs font-semibold text-white hover:bg-emerald-500 disabled:bg-slate-700 disabled:text-slate-400"
              >
                {isRunning ? 'Выполняется…' : 'Запустить проверку'}
              </button>
              <button
                onClick={() => setIsAiTestOpen(true)}
                className="rounded-lg border border-fuchsia-800 bg-fuchsia-950/60 px-2.5 py-1.5 text-xs text-fuchsia-300 hover:bg-fuchsia-900"
              >
                Тест AI
              </button>
              <span className="ml-auto text-[11px] text-slate-500">
                Сценарий: {workflowId}
              </span>
            </div>
            <div className="flex min-h-0 flex-1 flex-col md:flex-row">
              <NodePalette onAddNode={handleAddNode} />
              <WorkflowCanvas
                draft={draft}
                onDraftChange={handleDraftChange}
                selectedNodeId={selectedNodeId}
                onSelectNode={setSelectedNodeId}
                nodeStatuses={nodeStatusMap}
                validationIssues={validationIssues}
                onSaveDraft={handleSaveDraft}
                onResetToTemplate={handleResetToTemplate}
                isSaving={isSavingDraft}
                workflows={workflows}
                workflowId={workflowId}
                onSelectWorkflow={(id) => void handleSelectWorkflow(id)}
                artifacts={artifacts}
                currentArtifactId={currentArtifactId}
                onSelectArtifact={handleSelectArtifact}
              />
              {selectedNode && (
                <InspectorPanel
                  node={selectedNode}
                  artifacts={artifacts}
                  onUpdateConfig={handleUpdateNodeConfig}
                  onDeleteNode={handleDeleteNode}
                  onClose={() => setSelectedNodeId(null)}
                />
              )}
            </div>
          </div>
        );
      case 'schedule':
        return (
          <div className="flex h-full items-start justify-center overflow-y-auto bg-slate-950 p-4">
            <div className="w-full max-w-xl">
              <ScheduleModal
                workflowId={workflowId}
                artifactId={currentArtifactId}
                isOpen={true}
                onClose={() => setAdminSection('overview')}
                embedded
              />
            </div>
          </div>
        );
      case 'live':
        return (
          <LiveRunView
            currentRun={currentRun}
            nodeRuns={nodeRuns}
            events={events}
            onSelectNode={(nodeId) => setSelectedNodeId(nodeId)}
            onRefresh={currentRunId ? () => void loadRunDetails(currentRunId) : undefined}
          />
        );
      case 'problems':
        return <FindingsView findings={findings} />;
      case 'map':
        return <SecurityMapScreen key={currentRunId ?? 'no-run'} runId={currentRunId} />;
      case 'report':
        return (
          <ReportView
            report={report}
            runId={currentRunId}
            runStatus={currentRun?.execution_status}
            failureReason={failureReason}
            nodeRuns={nodeRuns}
          />
        );
      case 'comparison':
        return <ComparisonView runs={runs} artifacts={artifacts} workflows={workflows} onRefreshRuns={() => void refreshRuns()} />;
      case 'analyzers':
        return <AdminAnalyzersPanel report={report} nodeRuns={nodeRuns} />;
      case 'ai':
        return <AdminAiPanel report={report} />;
      case 'access':
        return <AdminAccessPanel session={session} />;
      default:
        return null;
    }
  })();

  return (
    <div className="h-screen w-screen overflow-hidden bg-slate-950 text-slate-100">
      <AdminShell
        section={adminSection}
        onSection={setAdminSection}
        onLogout={() => void handleLogout()}
      >
        {adminContent}
      </AdminShell>
      <UploadModal
        isOpen={isUploadOpen}
        onClose={() => setIsUploadOpen(false)}
        onArtifactLoaded={handleArtifactLoaded}
        showTraining
      />
      <AiTrustModal
        runId={currentRunId || undefined}
        isOpen={isAiTestOpen}
        onClose={() => setIsAiTestOpen(false)}
      />
      {noticeNode}
    </div>
  );
};

export default App;
