import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Navbar } from './components/Navbar';
import { WorkflowCanvas } from './components/WorkflowCanvas';
import { NodePalette } from './components/NodePalette';
import { InspectorPanel } from './components/InspectorPanel';
import { LiveRunView } from './components/LiveRunView';
import { FindingsView } from './components/FindingsView';
import { ReportView } from './components/ReportView';
import { UploadModal } from './components/UploadModal';
import { ScheduleModal } from './components/ScheduleModal';
import { AiTrustModal } from './components/AiTrustModal';
import { AccessGate } from './components/AccessGate';

import {
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

export const App: React.FC = () => {
  // Navigation
  const [activeTab, setActiveTab] = useState<'editor' | 'live' | 'findings' | 'report' | 'schedule'>('editor');

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
  const [workflowId, setWorkflowId] = useState<string>('wf_secure_pr_gate');
  const [draft, setDraft] = useState<WorkflowDraft>({
    schemaVersion: 1,
    name: 'Загрузка сценария...',
    description: '',
    nodes: [],
    edges: [],
  });
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [validationIssues, setValidationIssues] = useState<Array<{ level: string; message: string }>>([]);
  const [isSavingDraft, setIsSavingDraft] = useState(false);

  // Artifacts state
  const [artifacts, setArtifacts] = useState<ArtifactManifest[]>([]);
  const [currentArtifactId, setCurrentArtifactId] = useState<string>('');

  // Run execution state
  const [currentRunId, setCurrentRunId] = useState<string | null>(null);
  const [currentRun, setCurrentRun] = useState<RunSummary | null>(null);
  const [nodeRuns, setNodeRuns] = useState<NodeRunRecord[]>([]);
  const [events, setEvents] = useState<Array<{ seq: number; timestamp: string; type: string; nodeId?: string; payload: any }>>([]);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [report, setReport] = useState<ReportData | null>(null);
  const [isRunning, setIsRunning] = useState(false);

  // EventSource ref
  const eventSourceRef = useRef<EventSource | null>(null);

  // Load initial data
  const loadInitialData = async () => {
    try {
      // 1. Load artifacts
      const arts = await apiClient.listArtifacts();
      setArtifacts(arts);
      if (arts.length > 0) {
        setCurrentArtifactId(arts[0].id);
      }

      // 2. Load workflows
      const wfs = await apiClient.listWorkflows();
      if (wfs.length > 0) {
        const wf = wfs[0];
        setWorkflowId(wf.id);
        if (wf.draft) {
          setDraft(wf.draft);
          validateDraft(wf.id, wf.draft);
        }
      }

      // 3. Load latest run if any
      const runs = await apiClient.listRuns();
      if (runs.length > 0) {
        const latest = runs[0];
        setCurrentRunId(latest.id);
        setCurrentRun(latest);
        loadRunDetails(latest.id);
      }
    } catch (e: any) {
      if (e instanceof AuthRequiredError) {
        setSession((prev) => ({ authenticated: false, authRequired: true, workspaceId: prev?.workspaceId || '', expiresInSec: 0 }));
      }
      console.error('Failed to load initial data:', e);
    }
  };

  useEffect(() => {
    (async () => {
      try {
        const state = await apiClient.getSession();
        setSession(state);
        if (!state.authRequired || state.authenticated) {
          await loadInitialData();
        }
      } catch (e: any) {
        if (e instanceof AuthRequiredError) {
          setSession({ authenticated: false, authRequired: true, workspaceId: '', expiresInSec: 0 });
        } else {
          setAuthError(e?.message || 'Сервер недоступен');
          setSession({ authenticated: false, authRequired: true, workspaceId: '', expiresInSec: 0 });
        }
      } finally {
        setAuthLoading(false);
      }
    })();
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
    };
  }, []);

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
      // cookie already gone / server unavailable — local state still resets
    }
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    setSession((prev) => ({ authenticated: false, authRequired: prev?.authRequired ?? true, workspaceId: prev?.workspaceId || '', expiresInSec: 0 }));
    setCurrentRunId(null);
    setCurrentRun(null);
    setNodeRuns([]);
    setFindings([]);
    setReport(null);
    setEvents([]);
  };

  // Validate draft helper
  const validateDraft = async (wfId: string, d: WorkflowDraft) => {
    try {
      const res = await apiClient.validateWorkflow(wfId, d);
      setValidationIssues(res.issues || []);
    } catch (e) {
      console.error(e);
    }
  };

  // Load run details from server
  const loadRunDetails = async (rId: string) => {
    try {
      const runInfo = await apiClient.getRun(rId);
      setCurrentRun(runInfo);
      if (runInfo.execution_status === 'running' || runInfo.execution_status === 'queued') {
        setIsRunning(true);
      } else {
        setIsRunning(false);
      }

      const nodes = await apiClient.getRunNodes(rId);
      setNodeRuns(nodes);

      const fnds = await apiClient.getRunFindings(rId);
      setFindings(fnds);

      try {
        const rep = await apiClient.getRunReport(rId);
        setReport(rep);
      } catch {
        setReport(null);
      }
    } catch (e) {
      console.error(e);
    }
  };

  // Connect SSE for live updates
  const connectSSE = useCallback((rId: string) => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    const es = apiClient.createEventSource(rId, 0);
    eventSourceRef.current = es;

    es.onmessage = (msg) => {
      try {
        const ev = JSON.parse(msg.data);
        setEvents((prev) => [...prev, ev]);

        if (ev.type === 'run_status') {
          const status = ev.payload?.status;
          if (status === 'succeeded' || status === 'failed') {
            setIsRunning(false);
            loadRunDetails(rId);
          }
        } else if (ev.type === 'node_status') {
          loadRunDetails(rId);
        }
      } catch (err) {
        console.error('SSE parse error:', err);
      }
    };

    es.onerror = () => {
      // Reconnection or close
    };
  }, []);

  // Draft changes
  const handleDraftChange = (updated: WorkflowDraft) => {
    setDraft(updated);
    validateDraft(workflowId, updated);
  };

  const handleSaveDraft = async () => {
    setIsSavingDraft(true);
    try {
      await apiClient.saveWorkflowDraft(workflowId, draft);
      await validateDraft(workflowId, draft);
    } catch (e: any) {
      alert(`Ошибка сохранения: ${e.message}`);
    } finally {
      setIsSavingDraft(false);
    }
  };

  // Add node from palette
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
    const updated = { ...draft, nodes: [...draft.nodes, newNode] };
    handleDraftChange(updated);
    setSelectedNodeId(newId);
  };

  // Update node config
  const handleUpdateNodeConfig = (nodeId: string, newConfig: Record<string, any>, newTitle?: string) => {
    const updatedNodes = draft.nodes.map((n) => {
      if (n.id === nodeId) {
        return {
          ...n,
          config: newConfig,
          title: newTitle !== undefined ? newTitle : n.title,
        };
      }
      return n;
    });
    handleDraftChange({ ...draft, nodes: updatedNodes });
  };

  // Delete node
  const handleDeleteNode = (nodeId: string) => {
    const updatedNodes = draft.nodes.filter((n) => n.id !== nodeId);
    const updatedEdges = draft.edges.filter((e) => e.source !== nodeId && e.target !== nodeId);
    handleDraftChange({ ...draft, nodes: updatedNodes, edges: updatedEdges });
    setSelectedNodeId(null);
  };

  // Reset to default template
  const handleResetToTemplate = async () => {
    try {
      const wfs = await apiClient.listWorkflows();
      if (wfs.length > 0 && wfs[0].draft) {
        handleDraftChange(wfs[0].draft);
      }
    } catch (e) {
      console.error(e);
    }
  };

  // Trigger Run
  const handleTriggerRun = async () => {
    if (isRunning) return;
    setIsRunning(true);
    setEvents([]);
    try {
      // First save draft
      await apiClient.saveWorkflowDraft(workflowId, draft);
      const res = await apiClient.triggerRun(workflowId, currentArtifactId || undefined, 'manual');
      setCurrentRunId(res.runId);
      setActiveTab('live');
      connectSSE(res.runId);
    } catch (e: any) {
      alert(`Ошибка запуска: ${e.message}`);
      setIsRunning(false);
    }
  };

  // Artifact loaded
  const handleArtifactLoaded = (manifest: ArtifactManifest) => {
    setArtifacts((prev) => [manifest, ...prev]);
    setCurrentArtifactId(manifest.id);
    // Update project_input node if present
    const updatedNodes = draft.nodes.map((n) => {
      if (n.kind === 'project_input') {
        return { ...n, config: { ...n.config, artifactId: manifest.id } };
      }
      return n;
    });
    handleDraftChange({ ...draft, nodes: updatedNodes });
  };

  // Map node statuses for canvas
  const nodeStatusMap: Record<string, NodeRunStatus> = {};
  nodeRuns.forEach((nr) => {
    nodeStatusMap[nr.node_id] = nr.status;
  });

  const selectedNode = draft.nodes.find((n) => n.id === selectedNodeId) || null;

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
      {/* Navbar */}
      <Navbar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        onOpenUpload={() => setIsUploadOpen(true)}
        onTriggerRun={handleTriggerRun}
        onOpenAiTest={() => setIsAiTestOpen(true)}
        isRunning={isRunning}
        latestVerdict={currentRun?.security_verdict}
        findingsCount={findings.length}
      />

      {/* Main Content by Tab */}
      <main className="flex-1 flex overflow-hidden relative">
        {activeTab === 'editor' && (
          <div className="flex-1 flex h-full overflow-hidden">
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
            onSelectNode={(nId) => setSelectedNodeId(nId)}
          />
        )}

        {activeTab === 'findings' && <FindingsView findings={findings} />}

        {activeTab === 'report' && <ReportView report={report} runId={currentRunId} />}

        {activeTab === 'schedule' && (
          <div className="flex-1 flex items-center justify-center p-6 bg-slate-950">
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

      {/* Modals */}
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

      {session?.authenticated && (
        <button
          onClick={handleLogout}
          className="fixed bottom-3 right-3 z-30 rounded-lg border border-slate-700 bg-slate-900/95 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800 hover:text-white shadow-lg"
          title="Завершить сессию"
        >
          Выйти
        </button>
      )}
    </div>
  );
};

export default App;
