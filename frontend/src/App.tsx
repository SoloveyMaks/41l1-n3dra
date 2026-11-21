import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Navbar } from './components/Navbar';
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

import {
  AppTab,
  ArtifactManifest,
  Finding,
  NodeKind,
  NodeRunRecord,
  NodeRunStatus,
  ReportData,
  RunSummary,
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
  const [activeTab, setActiveTab] = useState<AppTab>('editor');

  // Access gate (public demo requires an access code; local mode gets a session automatically)
  const [session, setSession] = useState<SessionState | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [authBusy, setAuthBusy] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);

  // Modals
  const [isUploadOpen, setIsUploadOpen] = useState(false);
  const [isScheduleOpen, setIsScheduleOpen] = useState(false);
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
        setEvents((prev) => [...prev, event]);
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
        const latest = runList[0];
        selectedRunRef.current = latest.id;
        setCurrentRunId(latest.id);
        setCurrentRun(latest);
        await loadRunDetails(latest.id);
      }
    } catch (e: any) {
      if (e instanceof AuthRequiredError) {
        setSession((prev) => ({ authenticated: false, authRequired: true, workspaceId: prev?.workspaceId || '', expiresInSec: 0 }));
      } else {
        setAuthError(e?.message || 'Сервер недоступен');
        showNotice('error', e?.message || 'Сервер недоступен');
      }
    }
  }, [loadRunDetails, showNotice, validateDraft]);

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

  const handleLogin = async (accessCode: string) => {
    setAuthBusy(true);
    setAuthError(null);
    try {
      const state = await apiClient.login(accessCode);
      setSession(state);
      await loadInitialData();
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
    void loadRunDetails(runId);
  };

  const handleSelectWorkflow = async (nextWorkflowId: string) => {
    if (!nextWorkflowId) return;
    try {
      const workflow = await apiClient.getWorkflow(nextWorkflowId);
      if (!workflow?.draft) {
        showNotice('error', 'У сценария нет сохранённого draft');
        return;
      }
      setWorkflowId(nextWorkflowId);
      setDraft(workflow.draft);
      setSelectedNodeId(null);
      await validateDraft(nextWorkflowId, workflow.draft);
      showNotice('ok', `Открыт сценарий: ${workflow.name || nextWorkflowId}`);
    } catch (e: any) {
      showNotice('error', e?.message || 'Не удалось открыть сценарий');
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

  const handleTriggerRun = async () => {
    if (isRunning) return;
    const hasErrors = validationIssues.some((issue) => issue.level === 'error');
    if (hasErrors) {
      setActiveTab('editor');
      showNotice('error', 'Сначала исправьте ошибки валидации графа');
      return;
    }
    setIsRunning(true);
    setEvents([]);
    setReport(null);
    try {
      await apiClient.saveWorkflowDraft(workflowId, draft);
      const result = await apiClient.triggerRun(workflowId, currentArtifactId || undefined, 'manual');
      selectedRunRef.current = result.runId;
      setCurrentRunId(result.runId);
      setCurrentRun(null);
      setNodeRuns([]);
      setFindings([]);
      setReport(null);
      setSelectedNodeId(null);
      setActiveTab('live');
      connectSSE(result.runId);
      await loadRunDetails(result.runId);
      await refreshRuns();
    } catch (e: any) {
      setIsRunning(false);
      showNotice('error', e?.message || 'Ошибка запуска workflow');
    }
  };

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

  const nodeStatusMap: Record<string, NodeRunStatus> = {};
  nodeRuns.forEach((nodeRun) => {
    nodeStatusMap[nodeRun.node_id] = nodeRun.status;
  });

  const selectedNode = draft.nodes.find((node) => node.id === selectedNodeId) || null;

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
        onSubmit={handleLogin}
      />
    );
  }

  return (
    <div className="flex flex-col h-screen w-screen overflow-hidden bg-slate-950 text-slate-100 font-sans">
      <Navbar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        onOpenUpload={() => setIsUploadOpen(true)}
        onTriggerRun={handleTriggerRun}
        onOpenAiTest={() => setIsAiTestOpen(true)}
        isRunning={isRunning}
        latestVerdict={currentRun?.security_verdict}
        findingsCount={findings.length}
        runs={runs}
        currentRunId={currentRunId}
        runsLoading={runsLoading}
        onSelectRun={handleSelectRun}
        onRefreshRuns={() => void refreshRuns()}
        onLogout={() => void handleLogout()}
      />

      <main className="flex-1 flex overflow-hidden relative">
        {activeTab === 'editor' && (
          <div className="flex-1 flex flex-col md:flex-row h-full overflow-auto md:overflow-hidden">
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
        )}

        {activeTab === 'live' && (
          <LiveRunView
            currentRun={currentRun}
            nodeRuns={nodeRuns}
            events={events}
            onSelectNode={(nodeId) => setSelectedNodeId(nodeId)}
            onRefresh={currentRunId ? () => void loadRunDetails(currentRunId) : undefined}
          />
        )}

        {activeTab === 'findings' && <FindingsView findings={findings} />}

        {activeTab === 'map' && (
          <SecurityMapScreen key={currentRunId ?? 'no-run'} runId={currentRunId} />
        )}

        {activeTab === 'report' && (
          <ReportView
            report={report}
            runId={currentRunId}
            runStatus={currentRun?.execution_status}
            failureReason={nodeRuns.find((node) => node.error_message)?.error_message}
          />
        )}

        {activeTab === 'comparison' && (
          <ComparisonView runs={runs} onRefreshRuns={() => void refreshRuns()} />
        )}

        {activeTab === 'schedule' && (
          <div className="flex-1 flex items-center justify-center p-6 bg-slate-950 overflow-auto">
            <div className="w-full max-w-xl">
              <ScheduleModal
                workflowId={workflowId}
                artifactId={currentArtifactId}
                isOpen={true}
                onClose={() => setActiveTab('editor')}
              />
            </div>
          </div>
        )}
      </main>

      <UploadModal
        isOpen={isUploadOpen}
        onClose={() => setIsUploadOpen(false)}
        onArtifactLoaded={handleArtifactLoaded}
      />

      {activeTab !== 'schedule' && (
        <ScheduleModal
          workflowId={workflowId}
          artifactId={currentArtifactId}
          isOpen={isScheduleOpen}
          onClose={() => setIsScheduleOpen(false)}
        />
      )}

      <AiTrustModal
        runId={currentRunId || undefined}
        isOpen={isAiTestOpen}
        onClose={() => setIsAiTestOpen(false)}
      />

      {notice && (
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
      )}

    </div>
  );
};

export default App;
