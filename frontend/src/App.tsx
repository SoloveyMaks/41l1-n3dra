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
import { Navbar } from './components/Navbar';
import { ScenarioCreateModal } from './components/ScenarioCreateModal';
import { AccountModal } from './components/AccountModal';
import { UserSettingsPanel } from './components/UserSettingsPanel';
import { AdminShell, AdminSection } from './components/AdminShell';
import {
  AdminAccessPanel,
  AdminAiPanel,
  AdminAnalyzersPanel,
  AdminOverviewPanel,
  AdminProjectsPanel,
} from './components/AdminPanels';

import {
  AccountCredentials,
  AdminOverview,
  AiScanResult,
  AppTab,
  ArtifactManifest,
  Finding,
  NodeKind,
  NodeRunRecord,
  NodeRunStatus,
  ProfileData,
  ReportData,
  RunHistoryItem,
  RunSummary,
  SessionRole,
  SessionState,
  WorkflowDraft,
  WorkflowNode,
} from './types';
import { apiClient, ApiError, AuthRequiredError } from './api/client';
import { artifactLabel, shortCommit } from './repository';

type Notice = { kind: 'ok' | 'error'; text: string };

const EMPTY_DRAFT: WorkflowDraft = {
  schemaVersion: 1,
  name: 'Загрузка сценария...',
  description: '',
  nodes: [],
  edges: [],
};

const SELECTED_WORKFLOW_KEY = 'nedra.selectedWorkflowId';

const readSelectedWorkflowId = (): string => {
  try {
    return window.localStorage.getItem(SELECTED_WORKFLOW_KEY) || '';
  } catch {
    return '';
  }
};

const storeSelectedWorkflowId = (workflowId: string) => {
  try {
    window.localStorage.setItem(SELECTED_WORKFLOW_KEY, workflowId);
  } catch {
    // Storage unavailable: selection still works for the current page session.
  }
};

const extractAiScan = (nodes: NodeRunRecord[]): AiScanResult | null => {
  const aiNode = nodes.find((node) => node.kind === 'ai_scan');
  const raw = aiNode?.output_data_masked?.ai_scan_result;
  if (!raw || typeof raw !== 'object') return null;
  return raw as AiScanResult;
};

export const App: React.FC = () => {
  const [adminSection, setAdminSection] = useState<AdminSection>('overview');
  const [adminOverview, setAdminOverview] = useState<AdminOverview | null>(null);

  // Access gate (public demo requires an access code; local mode gets a session automatically)
  const [session, setSession] = useState<SessionState | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [authBusy, setAuthBusy] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [isAccountOpen, setIsAccountOpen] = useState(false);
  const [profile, setProfile] = useState<ProfileData | null>(null);
  const [accountHistory, setAccountHistory] = useState<RunHistoryItem[]>([]);
  const [accountLoading, setAccountLoading] = useState(false);
  const [accountError, setAccountError] = useState<string | null>(null);

  // Modals
  const [isUploadOpen, setIsUploadOpen] = useState(false);
  const [isAiTestOpen, setIsAiTestOpen] = useState(false);
  const [isScenarioOpen, setIsScenarioOpen] = useState(false);
  const [scenarioName, setScenarioName] = useState('');
  const [scenarioBusy, setScenarioBusy] = useState(false);
  const [scenarioError, setScenarioError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<AppTab>('editor');

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
  const [aiScan, setAiScan] = useState<AiScanResult | null>(null);
  const [report, setReport] = useState<ReportData | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);

  const eventSourceRef = useRef<EventSource | null>(null);
  const selectedRunRef = useRef<string | null>(null);
  const artifactIdsRef = useRef<Set<string>>(new Set());
  const artifactFetchRef = useRef<Map<string, Promise<ArtifactManifest | null>>>(new Map());
  const artifactCacheRef = useRef<Map<string, ArtifactManifest>>(new Map());
  const runLoadSeq = useRef(0);
  const validateSeq = useRef(0);
  const workflowSelectSeq = useRef(0);
  const adminOverviewSeq = useRef(0);
  const createScenarioSeq = useRef(0);
  const authRef = useRef<{ epoch: number; role: SessionRole | undefined }>({ epoch: 0, role: undefined });
  const noticeTimer = useRef<number | null>(null);

  const showNotice = useCallback((kind: Notice['kind'], text: string) => {
    setNotice({ kind, text });
    if (noticeTimer.current !== null) window.clearTimeout(noticeTimer.current);
    noticeTimer.current = window.setTimeout(() => setNotice(null), 7000);
  }, []);

  useEffect(() => {
    artifactIdsRef.current = new Set(artifacts.map((artifact) => artifact.id));
  }, [artifacts]);

  useEffect(() => () => {
    if (noticeTimer.current !== null) window.clearTimeout(noticeTimer.current);
  }, []);

  const closeSSE = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
  }, []);

  const refreshAdminOverview = useCallback(async (): Promise<void> => {
    const epoch = authRef.current.epoch;
    if (authRef.current.role !== 'admin') return;
    if (epoch !== authRef.current.epoch || authRef.current.role !== 'admin') return;
    const seq = ++adminOverviewSeq.current;
    try {
      const value = await apiClient.getAdminOverview();
      if (epoch !== authRef.current.epoch || seq !== adminOverviewSeq.current || authRef.current.role !== 'admin') return;
      setAdminOverview(value);
    } catch {
      // Never keep stale counters looking current after a failed refresh.
      if (epoch === authRef.current.epoch && seq === adminOverviewSeq.current && authRef.current.role === 'admin') {
        setAdminOverview(null);
      }
    }
  }, []);

  const refreshRuns = useCallback(async (): Promise<RunSummary[]> => {
    const epoch = authRef.current.epoch;
    setRunsLoading(true);
    try {
      const list = await apiClient.listRuns();
      if (epoch !== authRef.current.epoch) return [];
      setRuns(list);
      // Overview counters must reflect a completed run and a manual refresh.
      // This is a no-op for user sessions; node-level SSE does not call it.
      if (authRef.current.role === 'admin') void refreshAdminOverview();
      return list;
    } catch (e: any) {
      if (epoch !== authRef.current.epoch) return [];
      if (e instanceof AuthRequiredError) {
        setSession((prev) => ({ authenticated: false, authRequired: true, workspaceId: prev?.workspaceId || '', expiresInSec: 0 }));
      } else {
        showNotice('error', e?.message || 'Не удалось загрузить историю запусков');
      }
      return [];
    } finally {
      if (epoch === authRef.current.epoch) setRunsLoading(false);
    }
  }, [refreshAdminOverview, showNotice]);

  const ensureArtifact = useCallback(async (artifactId: string): Promise<ArtifactManifest | null> => {
    if (artifactIdsRef.current.has(artifactId)) return null;
    const cached = artifactCacheRef.current.get(artifactId);
    if (cached) return cached;
    let pending = artifactFetchRef.current.get(artifactId);
    if (!pending) {
      pending = apiClient.getArtifact(artifactId)
        .then((artifact) => {
          artifactCacheRef.current.set(artifactId, artifact);
          return artifact;
        })
        .catch(() => null)
        .finally(() => {
          artifactFetchRef.current.delete(artifactId);
        });
      artifactFetchRef.current.set(artifactId, pending);
    }
    return pending;
  }, []);

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
      // Сервер мог создать артефакт уже после загрузки страницы (наблюдение за
      // коммитом). Догружаем только его, чтобы источник запуска не зависел от reload.
      let runArtifact: ArtifactManifest | null = null;
      if (runInfo.artifact_id) {
        runArtifact = await ensureArtifact(runInfo.artifact_id);
      }
      if (seq !== runLoadSeq.current || selectedRunRef.current !== runId) return;
      if (runArtifact) {
        const freshArtifact = runArtifact;
        artifactIdsRef.current.add(freshArtifact.id);
        artifactCacheRef.current.delete(freshArtifact.id);
        setArtifacts((prev) => (
          prev.some((artifact) => artifact.id === freshArtifact.id)
            ? prev
            : [freshArtifact, ...prev]
        ));
      }

      setCurrentRun(runInfo);
      setNodeRuns(nodes);
      setFindings(runFindings);
      setAiScan(extractAiScan(nodes));
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
  }, [ensureArtifact, refreshRuns, showNotice]);

  const connectSSE = useCallback((runId: string) => {
    closeSSE();
    const source = apiClient.createEventSource(runId, 0);
    eventSourceRef.current = source;

    const handleEvent = (message: MessageEvent) => {
      if (selectedRunRef.current !== runId) return;
      try {
        const parsed = JSON.parse(message.data);
        // Historical replay uses `node_id`; live broker uses `nodeId`. Live broker
        // events carry no timestamp, so the client receive time keeps the
        // running-stage duration honest instead of showing zero.
        const event = {
          ...parsed,
          nodeId: parsed?.nodeId ?? parsed?.node_id,
          timestamp: parsed?.timestamp ?? new Date().toISOString(),
        };
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
        } else if (event.type === 'ai_scan_result') {
          void loadRunDetails(runId);
        }
      } catch (error) {
        console.error('SSE parse error:', error);
      }
    };
    // Backend sends named events (`event: run_status`), so EventSource does
    // not dispatch them to `onmessage`; subscribe to the known event names too.
    source.onmessage = handleEvent;
    for (const eventName of ['run_status', 'node_status', 'artifact_status', 'finding_summary', 'coverage', 'edge_data', 'delivery_status', 'ai_scan_result']) {
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

  const resetRunSelection = () => {
    closeSSE();
    selectedRunRef.current = null;
    runLoadSeq.current += 1;
    setCurrentRunId(null);
    setCurrentRun(null);
    setNodeRuns([]);
    setFindings([]);
    setAiScan(null);
    setReport(null);
    setEvents([]);
    setIsRunning(false);
  };

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

      const storedWorkflowId = readSelectedWorkflowId();
      const selectedWorkflow =
        workflowList.find((item) => item.id === storedWorkflowId) ||
        workflowList.find((item) => item.id === 'wf_secure_pr_gate') ||
        workflowList[0];
      if (selectedWorkflow?.draft) {
        setWorkflowId(selectedWorkflow.id);
        setDraft(selectedWorkflow.draft);
        storeSelectedWorkflowId(selectedWorkflow.id);
        await validateDraft(selectedWorkflow.id, selectedWorkflow.draft);
      } else {
        showNotice('error', 'На сервере нет доступного сценария');
      }

      const selectedRuns = selectedWorkflow
        ? runList.filter((run) => run.workflow_id === selectedWorkflow.id)
        : [];
      const latest =
        selectedRuns.find((run) => run.trigger_type !== 'schedule') ||
        selectedRuns[0];
      if (latest) {
        selectedRunRef.current = latest.id;
        setCurrentRunId(latest.id);
        setCurrentRun(latest);
        await loadRunDetails(latest.id);
        connectSSE(latest.id);
      } else {
        resetRunSelection();
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
      authRef.current = { epoch: authRef.current.epoch + 1, role: state.role };
      setAdminOverview(null);
      adminOverviewSeq.current += 1;
      setSession(state);
      await loadInitialData();
      setAdminSection('overview');
      setActiveTab('editor');
    } catch (e: any) {
      setAuthError(e?.message || 'Не удалось войти');
    } finally {
      setAuthBusy(false);
    }
  };

  const handleAccountAuth = async (mode: 'login' | 'register', input: AccountCredentials) => {
    setAuthBusy(true);
    setAuthError(null);
    try {
      const state = mode === 'register'
        ? await apiClient.registerAccount(input)
        : await apiClient.loginAccount(input.email, input.password);
      authRef.current = { epoch: authRef.current.epoch + 1, role: state.role };
      setAdminOverview(null);
      adminOverviewSeq.current += 1;
      setSession(state);
      await loadInitialData();
      setAdminSection('overview');
      setActiveTab('editor');
    } catch (error: unknown) {
      if (error instanceof ApiError) {
        if (error.code === 'EMAIL_TAKEN') setAuthError('Этот email уже зарегистрирован');
        else if (error.code === 'INVALID_CREDENTIALS') setAuthError('Неверный email или пароль');
        else if (error.code === 'REGISTRATION_CODE_REQUIRED') setAuthError('Регистрация по приглашению: нужен код');
        else setAuthError(error.message);
      } else {
        setAuthError(error instanceof Error ? error.message : 'Не удалось войти');
      }
    } finally {
      setAuthBusy(false);
    }
  };

  const openAccount = async () => {
    setIsAccountOpen(true);
    setAccountLoading(true);
    setAccountError(null);
    setProfile(null);
    setAccountHistory([]);
    try {
      const profilePromise = session?.mode === 'demo'
        ? Promise.resolve(null)
        : apiClient.getProfile().catch((error: unknown) => {
            if (error instanceof ApiError && error.code === 'DEMO_SESSION') return null;
            throw error;
          });
      const [profileValue, historyValue] = await Promise.all([
        profilePromise,
        apiClient.getHistory(20),
      ]);
      setProfile(profileValue);
      setAccountHistory(historyValue.items);
    } catch (error: unknown) {
      setAccountError(error instanceof Error ? error.message : 'Не удалось загрузить аккаунт');
    } finally {
      setAccountLoading(false);
    }
  };

  const handleLogout = async () => {
    // Invalidate in-flight admin/overview work before the network await.
    authRef.current = { epoch: authRef.current.epoch + 1, role: 'user' };
    adminOverviewSeq.current += 1;
    setAdminOverview(null);
    try {
      await apiClient.logout();
    } catch {
      // Cookie already gone or server unavailable; local state still resets.
    }
    closeSSE();
    selectedRunRef.current = null;
    runLoadSeq.current += 1;
    workflowSelectSeq.current += 1;
    adminOverviewSeq.current += 1;
    setAdminOverview(null);
    setSession((prev) => ({ authenticated: false, authRequired: prev?.authRequired ?? true, workspaceId: prev?.workspaceId || '', expiresInSec: 0 }));
    setCurrentRunId(null);
    setCurrentRun(null);
    setNodeRuns([]);
    setFindings([]);
    setAiScan(null);
    setReport(null);
    setEvents([]);
    setRuns([]);
    setIsRunning(false);
    setSelectedNodeId(null);
    setIsAccountOpen(false);
    setProfile(null);
    setAccountHistory([]);
    setAccountError(null);
  };

  const handleSelectRun = (runId: string) => {
    if (!runId) return;
    closeSSE();
    selectedRunRef.current = runId;
    setCurrentRunId(runId);
    setCurrentRun(null);
    setNodeRuns([]);
    setFindings([]);
    setAiScan(null);
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
      storeSelectedWorkflowId(nextWorkflowId);
      setSelectedNodeId(null);
      const nextRun = runs.find((run) => run.workflow_id === nextWorkflowId);
      if (nextRun) handleSelectRun(nextRun.id);
      else resetRunSelection();
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

  const openScenarioModal = () => {
    const baseName = workflows.find((workflow) => workflow.id === workflowId)?.name || draft.name || 'Мой сценарий';
    setScenarioName(`${baseName.slice(0, 108)} (копия)`);
    setScenarioError(null);
    setIsScenarioOpen(true);
  };

  const handleCreateScenario = async () => {
    const name = scenarioName.trim();
    if (!name) {
      setScenarioError('Введите название сценария');
      return;
    }
    const seq = ++createScenarioSeq.current;
    setScenarioBusy(true);
    setScenarioError(null);
    const nextDraft: WorkflowDraft = {
      schemaVersion: draft.schemaVersion || 1,
      name,
      description: draft.description || 'Пользовательский сценарий проверки',
      nodes: draft.nodes.map((node) => ({
        ...node,
        config: { ...node.config },
        position: { ...node.position },
      })),
      edges: draft.edges.map((edge) => ({ ...edge })),
    };
    try {
      const created = await apiClient.createWorkflow(nextDraft);
      if (seq !== createScenarioSeq.current) return;
      const entry = { id: created.id, name, draft: nextDraft };
      setWorkflows((prev) => [entry, ...prev.filter((workflow) => workflow.id !== created.id)]);
      setWorkflowId(created.id);
      setDraft(nextDraft);
      storeSelectedWorkflowId(created.id);
      setSelectedNodeId(null);
      resetRunSelection();
      setIsScenarioOpen(false);
      if (session?.role === 'admin') setAdminSection('editor');
      else setActiveTab('editor');
      await validateDraft(created.id, nextDraft);
      if (seq !== createScenarioSeq.current) return;
      showNotice('ok', `Сценарий создан: ${name}`);
    } catch (error: unknown) {
      if (seq !== createScenarioSeq.current) return;
      if (error instanceof ApiError && error.status === 403) {
        setScenarioError('Создание сценариев пока недоступно на сервере для вашей роли.');
      } else {
        setScenarioError(error instanceof Error ? error.message : 'Не удалось создать сценарий');
      }
    } finally {
      if (seq === createScenarioSeq.current) setScenarioBusy(false);
    }
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
      if (navigate) {
        if (session?.role === 'admin') setAdminSection('editor');
        else setActiveTab('editor');
      }
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
      setAiScan(null);
      setReport(null);
      setSelectedNodeId(null);
      if (navigate) {
        if (session?.role === 'admin') setAdminSection('live');
        else setActiveTab('live');
      }
      connectSSE(result.runId);
      await loadRunDetails(result.runId);
      await refreshRuns();
    } catch (e: any) {
      setIsRunning(false);
      showNotice('error', e?.message || 'Ошибка запуска проверки');
    }
  };

  useEffect(() => {
    authRef.current = { ...authRef.current, role: session?.role };
    if (session?.role !== 'admin') {
      adminOverviewSeq.current += 1;
      setAdminOverview(null);
      return;
    }
    void refreshAdminOverview();
  }, [session?.role, refreshAdminOverview]);

  const handleArtifactLoaded = (manifest: ArtifactManifest) => {
    artifactIdsRef.current.add(manifest.id);
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
    // Новый проект выбран: старый результат относился к другому артефакту.
    resetRunSelection();
    const repository = manifest.repository;
    showNotice(
      'ok',
      repository
        ? `Репозиторий импортирован: ${artifactLabel(manifest)} · ${repository.branch} · ${shortCommit(repository.commit_sha)}`
        : `Проект выбран: ${artifactLabel(manifest)}`,
    );
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
        registrationInviteRequired={session.registrationInviteRequired}
        onAccountSubmit={(mode, input) => void handleAccountAuth(mode, input)}
        onCodeSubmit={(code, role) => void handleLogin(code, role)}
      />
    );
  }

  if (!isAdmin) {
    const userRuns = runs.filter((run) => run.workflow_id === workflowId);
    return (
      <div className="flex h-screen w-screen flex-col overflow-hidden bg-slate-950 text-slate-100">
        <Navbar
          activeTab={activeTab}
          setActiveTab={setActiveTab}
          onOpenUpload={() => setIsUploadOpen(true)}
          onTriggerRun={() => void handleTriggerRun(false, true)}
          onOpenAiTest={() => setIsAiTestOpen(true)}
          isRunning={isRunning}
          latestVerdict={currentRun?.security_verdict}
          findingsCount={findings.length}
          runs={userRuns}
          currentRunId={currentRunId}
          runsLoading={runsLoading}
          onSelectRun={handleSelectRun}
          onRefreshRuns={() => void refreshRuns()}
          onLogout={() => void handleLogout()}
          isAdmin={false}
          user={session?.user || null}
          mode={session?.mode || null}
          onOpenAccount={() => void openAccount()}
        />

        <main className="relative flex flex-1 overflow-hidden">
          {activeTab === 'editor' && (
            <div className="flex h-full flex-1 flex-col overflow-auto md:flex-row md:overflow-hidden">
              <NodePalette onAddNode={handleAddNode} />
              <WorkflowCanvas
                draft={draft}
                onDraftChange={handleDraftChange}
                selectedNodeId={selectedNodeId}
                onSelectNode={setSelectedNodeId}
                nodeStatuses={nodeStatusMap}
                validationIssues={validationIssues}
                onSaveDraft={() => void handleSaveDraft()}
                onResetToTemplate={() => void handleResetToTemplate()}
                isSaving={isSavingDraft}
                workflows={workflows}
                workflowId={workflowId}
                onSelectWorkflow={(id) => void handleSelectWorkflow(id)}
                onCreateWorkflow={openScenarioModal}
                artifacts={artifacts}
                currentArtifactId={currentArtifactId}
                onSelectArtifact={handleUserSelectArtifact}
              />
              {selectedNode && (
                <div className="fixed inset-x-0 bottom-0 z-30 flex min-h-0 flex-col shadow-2xl md:static md:z-auto md:shrink-0">
                  <InspectorPanel
                    node={selectedNode}
                    artifacts={artifacts}
                    onUpdateConfig={handleUpdateNodeConfig}
                    onDeleteNode={handleDeleteNode}
                    onClose={() => setSelectedNodeId(null)}
                  />
                </div>
              )}
            </div>
          )}

          {activeTab === 'live' && (
            <LiveRunView
              currentRun={currentRun}
              nodeRuns={nodeRuns}
              events={events}
              plannedNodes={draft.nodes}
              onSelectNode={(nodeId) => setSelectedNodeId(nodeId)}
              onRefresh={currentRunId ? () => void loadRunDetails(currentRunId) : undefined}
              onOpenMap={() => setActiveTab('map')}
              onOpenFindings={() => setActiveTab('findings')}
              onOpenReport={() => setActiveTab('report')}
            />
          )}

          {activeTab === 'findings' && (
            <FindingsView
              findings={findings}
              runId={currentRunId}
              aiScan={aiScan}
              aiItems={report?.run_id === currentRunId ? report.ai_items : []}
              aiStatus={report?.run_id === currentRunId ? report.ai_status : undefined}
              onOpenReport={() => setActiveTab('report')}
            />
          )}

          {activeTab === 'map' && (
            <SecurityMapScreen key={currentRunId ?? 'no-run'} runId={currentRunId} />
          )}

          {activeTab === 'report' && (
            <ReportView
              report={report}
              runId={currentRunId}
              runStatus={currentRun?.execution_status}
              failureReason={failureReason}
              nodeRuns={nodeRuns}
              aiScan={aiScan}
            />
          )}

          {activeTab === 'comparison' && (
            <ComparisonView
              runs={runs}
              artifacts={artifacts}
              workflows={workflows}
              onRefreshRuns={() => void refreshRuns()}
            />
          )}

          {activeTab === 'settings' && (
            <UserSettingsPanel
              session={session}
              workflowId={workflowId}
              artifactId={currentArtifactId}
              onOpenRun={(runId) => {
                handleSelectRun(runId);
                setActiveTab('live');
              }}
            />
          )}
        </main>

        <UploadModal
          isOpen={isUploadOpen}
          onClose={() => setIsUploadOpen(false)}
          onArtifactLoaded={handleArtifactLoaded}
          showTraining={false}
        />
        <ScenarioCreateModal
          isOpen={isScenarioOpen}
          name={scenarioName}
          busy={scenarioBusy}
          error={scenarioError}
          onName={setScenarioName}
          onClose={() => setIsScenarioOpen(false)}
          onCreate={() => void handleCreateScenario()}
        />
        <AccountModal
          open={isAccountOpen}
          session={session}
          profile={profile}
          history={accountHistory}
          loading={accountLoading}
          error={accountError}
          onClose={() => setIsAccountOpen(false)}
          onLogout={() => void handleLogout()}
          onSelectRun={handleSelectRun}
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
                ZIP или репозиторий
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
                onCreateWorkflow={openScenarioModal}
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
            <div className="w-full max-w-2xl">
              <ScheduleModal
                workflowId={workflowId}
                artifactId={currentArtifactId}
                isOpen={true}
                onClose={() => setAdminSection('overview')}
                adminAllowed={isAdmin}
                onOpenRun={(runId) => {
                  if (!runId) return;
                  handleSelectRun(runId);
                  setAdminSection('live');
                }}
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
            plannedNodes={draft.nodes}
            onSelectNode={(nodeId) => setSelectedNodeId(nodeId)}
            onRefresh={currentRunId ? () => void loadRunDetails(currentRunId) : undefined}
            onOpenMap={() => setAdminSection('map')}
            onOpenFindings={() => setAdminSection('problems')}
            onOpenReport={() => setAdminSection('report')}
          />
        );
      case 'problems':
        return (
          <FindingsView
            findings={findings}
            runId={currentRunId}
            aiScan={aiScan}
            aiItems={report?.run_id === currentRunId ? report.ai_items : []}
            aiStatus={report?.run_id === currentRunId ? report.ai_status : undefined}
            onOpenReport={() => setAdminSection('report')}
          />
        );
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
            aiScan={aiScan}
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
      <ScenarioCreateModal
        isOpen={isScenarioOpen}
        name={scenarioName}
        busy={scenarioBusy}
        error={scenarioError}
        onName={setScenarioName}
        onClose={() => setIsScenarioOpen(false)}
        onCreate={() => void handleCreateScenario()}
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
