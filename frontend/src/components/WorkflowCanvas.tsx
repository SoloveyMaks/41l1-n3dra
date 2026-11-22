import React, { useEffect, useMemo, useCallback, useRef } from 'react';
import {
  ReactFlow,
  Controls,
  Background,
  MiniMap,
  useNodesState,
  useEdgesState,
  Connection,
  Edge,
  Node,
  BackgroundVariant,
  type ReactFlowInstance,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import { CustomNode } from './CustomNode';
import { ExecutionEdge, ExecutionEdgeData } from './ExecutionEdge';
import type { ArtifactManifest, SecurityVerdict, WorkflowDraft, WorkflowEdge } from '../types';
import type { RunTelemetry } from '../execution/runTelemetry';
import { IDLE_EDGE_STATE, selectActiveEdges } from '../execution/activeEdges';
import { formatEditorRunProgress, type EditorRunProgress } from '../execution/editorProgress';
import { AlertCircle, CheckCircle2, Plus, RotateCcw, Save, Sparkles } from 'lucide-react';
import { RepositorySourceView } from './RepositorySourceView';
import { artifactChoiceLabel, artifactRepository } from '../repository';

interface WorkflowCanvasProps {
  draft: WorkflowDraft;
  onDraftChange: (updated: WorkflowDraft) => void;
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string | null) => void;
  telemetry: RunTelemetry;
  executionNotice?: string | null;
  validationIssues: Array<{ level: string; message: string }>;
  validationState: 'checking' | 'ready' | 'unavailable';
  onSaveDraft: () => void;
  onResetToTemplate: () => void;
  isSaving: boolean;
  workflows: any[];
  workflowId: string;
  onSelectWorkflow: (workflowId: string) => void;
  onCreateWorkflow?: () => void;
  onGenerateWorkflow?: () => void;
  artifacts: ArtifactManifest[];
  currentArtifactId: string;
  onSelectArtifact: (artifactId: string) => void;
  /**
   * Run whose facts may open the file graph. Only set when the selected
   * artifact is the artifact of that run: a draft project must never show
   * another run's files.
   */
  runId?: string | null;
  onOpenMap?: () => void;
  /**
   * Run progress for this draft: counts, the block executing right now and the
   * final verdict. null/absent hides the bar (no run, or a foreign graph).
   */
  progress?: EditorRunProgress | null;
  /** Security verdict of that run, shown next to the result once it settles. */
  runVerdict?: SecurityVerdict | null;
  /** Opens «Ход проверки»; the editor itself never leaves its tab. */
  onOpenRun?: () => void;
  /**
   * Stops the run shown in the progress bar. Only passed while that run is
   * queued/running, so the button exists exactly as long as it can work.
   */
  onStopRun?: () => void;
  /** True while the stop request is in flight: the button reads «Останавливаем…». */
  stopRequested?: boolean;
}

export const WorkflowCanvas: React.FC<WorkflowCanvasProps> = ({
  draft,
  onDraftChange,
  selectedNodeId,
  onSelectNode,
  telemetry,
  executionNotice,
  validationIssues,
  validationState,
  onSaveDraft,
  onResetToTemplate,
  isSaving,
  workflows,
  workflowId,
  onSelectWorkflow,
  onCreateWorkflow,
  onGenerateWorkflow,
  artifacts,
  currentArtifactId,
  onSelectArtifact,
  runId = null,
  onOpenMap,
  progress = null,
  runVerdict = null,
  onOpenRun,
  onStopRun,
  stopRequested = false,
}) => {
  const nodeTypes = useMemo(() => ({ customNode: CustomNode }), []);
  const edgeTypes = useMemo(() => ({ executionEdge: ExecutionEdge }), []);
  const selectedArtifact = artifacts.find((artifact) => artifact.id === currentArtifactId);
  const selectedRepository = artifactRepository(selectedArtifact);
  const workflowName = workflows.find((workflow) => workflow.id === workflowId)?.name || workflowId || 'Сценарий не выбран';
  const artifactName = selectedArtifact
    ? artifactChoiceLabel(selectedArtifact)
    : artifacts.length
      ? 'Авто-выбор (последний загруженный)'
      : 'Нет артефактов';

  // Graph structure only. Live status is layered on separately so an SSE event
  // never resets positions or re-mounts edges mid-drag.
  const baseNodes: Node[] = useMemo(() => {
    return draft.nodes.map((n) => ({
      id: n.id,
      type: 'customNode',
      position: n.position || { x: 100, y: 100 },
      data: {
        kind: n.kind,
        title: n.title || n.kind,
        config: n.config,
      },
    }));
  }, [draft.nodes]);

  // Map our domain WorkflowEdge to ReactFlow Edge. The saved draft stays the
  // only source of graph topology; telemetry only decorates an edge.
  const baseEdges: Edge[] = useMemo(() => {
    return draft.edges.map((e) => {
      const tone: ExecutionEdgeData['tone'] =
        e.sourcePort === 'true_branch' ? 'true' : e.sourcePort === 'false_branch' ? 'false' : 'default';
      return {
        id: e.id,
        type: 'executionEdge',
        source: e.source,
        sourceHandle: e.sourcePort,
        target: e.target,
        targetHandle: e.targetPort,
        data: { tone, flow: 'idle', pulse: false },
      };
    });
  }, [draft.edges]);

  const [nodes, setNodes, onNodesChange] = useNodesState(baseNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(baseEdges);

  // Rebuild topology only when the draft itself changes (load, add/remove,
  // drag stop). Positions come from the draft, so this is not a snap.
  useEffect(() => {
    setNodes(baseNodes);
  }, [baseNodes, setNodes]);

  useEffect(() => {
    setEdges(baseEdges);
  }, [baseEdges, setEdges]);

  // Overlay live node status without touching node positions.
  useEffect(() => {
    setNodes((current) =>
      current.map((node) => {
        const live = telemetry.nodes[node.id];
        return {
          ...node,
          selected: node.id === selectedNodeId,
          data: {
            ...node.data,
            status: live?.status || 'pending',
            durationMs: live?.durationMs,
            startedAtMs: live?.startedAtMs,
            live: telemetry.live,
            // A terminal run never animates an active node record: the record
            // is history, not something executing right now.
            active: Boolean(live) && !live?.stalled,
            stalled: Boolean(live?.stalled),
          },
        };
      }),
    );
  }, [selectedNodeId, telemetry, baseNodes, setNodes]);

  // Overlay edge flow from recorded edge_data only. A branch the run skipped
  // never received data, so it stays idle instead of animating by port type;
  // replayed history restores highlights without motion.
  useEffect(() => {
    const active = selectActiveEdges(draft.edges, telemetry);
    setEdges((current) =>
      current.map((edge) => {
        const state = active[edge.id] || IDLE_EDGE_STATE;
        return {
          ...edge,
          data: {
            ...edge.data,
            flow: state.flow,
            pulse: state.pulse,
          },
        };
      }),
    );
  }, [draft.edges, telemetry, setEdges]);

  // Handle new connections
  const onConnect = useCallback(
    (params: Connection) => {
      if (!params.source || !params.target) return;
      const newEdge: WorkflowEdge = {
        id: `e_${params.source}_${params.target}_${Date.now()}`,
        source: params.source,
        sourcePort: params.sourceHandle || 'out',
        target: params.target,
        targetPort: params.targetHandle || 'in',
      };
      onDraftChange({ ...draft, edges: [...draft.edges, newEdge] });
    },
    [draft, onDraftChange]
  );

  // Sync positions when node drag finishes
  const onNodeDragStop = useCallback(
    (_: any, node: Node) => {
      const updatedNodes = draft.nodes.map((n) => {
        if (n.id === node.id) {
          return { ...n, position: node.position };
        }
        return n;
      });
      onDraftChange({ ...draft, nodes: updatedNodes });
    },
    [draft, onDraftChange]
  );

  const onNodeClick = useCallback(
    (_: any, node: Node) => {
      onSelectNode(node.id);
    },
    [onSelectNode]
  );

  const onPaneClick = useCallback(() => {
    onSelectNode(null);
  }, [onSelectNode]);

  // Auto-focus follows the block the run works on, but only when that block
  // changes and sits outside the viewport. Repeated telemetry for the same
  // block never moves the camera; a manual pan/zoom or a block drag wins for
  // the rest of the run, so the viewport never fights the user. Programmatic
  // moves (ours, fitView) report a null event and are not "manual".
  const activeNodeId = progress?.activeNodeId ?? null;
  const flowInstanceRef = useRef<ReactFlowInstance | null>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const lastFocusedNodeRef = useRef<string | null>(null);
  const manualViewportRef = useRef(false);
  const wasLiveRef = useRef(false);

  useEffect(() => {
    if (telemetry.live && !wasLiveRef.current) {
      manualViewportRef.current = false;
      lastFocusedNodeRef.current = null;
    }
    wasLiveRef.current = telemetry.live;
  }, [telemetry.live]);

  useEffect(() => {
    if (!activeNodeId || activeNodeId === lastFocusedNodeRef.current) return;
    lastFocusedNodeRef.current = activeNodeId;
    if (manualViewportRef.current) return;
    const instance = flowInstanceRef.current;
    const container = canvasRef.current;
    if (!instance || !container) return;
    const node = nodes.find((item) => item.id === activeNodeId);
    if (!node?.position) return;
    const rect = container.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;
    const width = node.measured?.width ?? node.width ?? 0;
    const height = node.measured?.height ?? node.height ?? 0;
    const { x, y, zoom } = instance.getViewport();
    const left = node.position.x * zoom + x;
    const top = node.position.y * zoom + y;
    // Margins keep the overlay toolbar and the canvas edges from hiding the block.
    const margin = { top: 150, right: 32, bottom: 40, left: 32 };
    const fullyVisible =
      left >= margin.left &&
      left + width * zoom <= rect.width - margin.right &&
      top >= margin.top &&
      top + height * zoom <= rect.height - margin.bottom;
    if (fullyVisible) return;
    void instance.setCenter(node.position.x + width / 2, node.position.y + height / 2, {
      zoom,
      duration: 600,
    });
  }, [activeNodeId, nodes]);

  const handleInit = useCallback((instance: ReactFlowInstance) => {
    flowInstanceRef.current = instance;
  }, []);

  const handleMoveStart = useCallback((event: MouseEvent | TouchEvent | null) => {
    if (event) manualViewportRef.current = true;
  }, []);

  const handleNodeDragStart = useCallback(() => {
    manualViewportRef.current = true;
  }, []);

  const onBeforeDelete = useCallback(async ({ nodes: removedNodes, edges: removedEdges }: { nodes: Node[]; edges: Edge[] }) => {
    const nodeIds = new Set(removedNodes.map((node) => node.id));
    const edgeIds = new Set(removedEdges.map((edge) => edge.id));
    onDraftChange({
      ...draft,
      nodes: draft.nodes.filter((node) => !nodeIds.has(node.id)),
      edges: draft.edges.filter((edge) => !edgeIds.has(edge.id) && !nodeIds.has(edge.source) && !nodeIds.has(edge.target)),
    });
    if (selectedNodeId && nodeIds.has(selectedNodeId)) onSelectNode(null);
    return false;
  }, [draft, onDraftChange, onSelectNode, selectedNodeId]);

  const errorCount = validationIssues.filter((issue) => issue.level === 'error').length;
  const progressLabel = progress ? formatEditorRunProgress(progress) : null;
  const hasErrors = validationState === 'ready' && errorCount > 0;
  const validationPending = validationState === 'checking';
  const validationUnavailable = validationState === 'unavailable';
  const validationLabel = hasErrors
    ? `Ошибок: ${errorCount}`
    : validationPending
      ? 'Проверяем граф…'
      : validationUnavailable
        ? 'Граф не проверен'
        : 'Граф валиден';

  return (
    <div ref={canvasRef} className="relative min-h-[420px] w-full min-w-0 bg-slate-950 xl:h-full xl:min-h-0 xl:flex-1">
      {/* Top Bar on Canvas */}
      <div className="pointer-events-none absolute inset-x-2 top-2 z-10 flex flex-col gap-2 min-[1760px]:flex-row min-[1760px]:items-start">
        <div className="pointer-events-auto grid w-full min-w-0 gap-2 rounded-xl border border-slate-800 bg-slate-900/95 p-2 shadow-lg backdrop-blur-md min-[1760px]:w-[460px] min-[1760px]:shrink-0">
          <label className="flex min-w-0 flex-col gap-1">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">Сценарий</span>
            <select
              value={workflowId}
              onChange={(event) => onSelectWorkflow(event.target.value)}
              disabled={workflows.length === 0}
              aria-label="Сценарий проверки"
              title={`Сценарий: ${workflowName}`}
              className="h-9 w-full min-w-0 rounded-lg border border-slate-700 bg-slate-950 px-2.5 text-xs text-slate-100 outline-none transition focus-visible:border-indigo-500 focus-visible:ring-2 focus-visible:ring-indigo-500/40 disabled:text-slate-500"
            >
              {workflows.length === 0 && <option value="">Нет сценариев</option>}
              {workflows.map((workflow) => (
                <option key={workflow.id} value={workflow.id}>
                  {workflow.name || workflow.id}
                </option>
              ))}
            </select>
          </label>

          <label className="flex min-w-0 flex-col gap-1">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">Проект для запуска</span>
            <select
              value={currentArtifactId}
              onChange={(event) => onSelectArtifact(event.target.value)}
              disabled={artifacts.length === 0}
              aria-label="Проект для запуска"
              title={`Проект: ${artifactName}`}
              className="h-9 w-full min-w-0 rounded-lg border border-slate-700 bg-slate-950 px-2.5 text-xs text-slate-100 outline-none transition focus-visible:border-indigo-500 focus-visible:ring-2 focus-visible:ring-indigo-500/40 disabled:text-slate-500"
            >
              <option value="">{artifacts.length ? 'Авто-выбор (последний загруженный)' : 'Нет артефактов'}</option>
              {artifacts.map((artifact) => (
                <option key={artifact.id} value={artifact.id}>
                  {artifactChoiceLabel(artifact)}
                </option>
              ))}
            </select>
          </label>

          {(onCreateWorkflow || onGenerateWorkflow) && (
            <div className="flex flex-wrap gap-1.5">
              {onCreateWorkflow && (
                <button
                  type="button"
                  onClick={onCreateWorkflow}
                  className="flex h-9 flex-1 items-center justify-center gap-1.5 rounded-lg border border-indigo-800 bg-indigo-950/60 px-2.5 text-xs font-medium text-indigo-200 transition hover:bg-indigo-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 sm:flex-none"
                  title="Создать новый сценарий на основе текущих блоков"
                >
                  <Plus size={13} /> Новый сценарий
                </button>
              )}
              {onGenerateWorkflow && (
                <button
                  type="button"
                  onClick={onGenerateWorkflow}
                  className="flex h-9 flex-1 items-center justify-center gap-1.5 rounded-lg border border-fuchsia-800 bg-fuchsia-950/60 px-2.5 text-xs font-medium text-fuchsia-100 transition hover:bg-fuchsia-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-fuchsia-400 sm:flex-none"
                  title="Собрать черновик сценария из текста или голоса"
                >
                  <Sparkles size={13} /> По описанию
                </button>
              )}
            </div>
          )}

          {selectedRepository && (
            <div className="min-w-0 rounded-lg border border-indigo-900/70 bg-indigo-950/40 px-2 py-1">
              <RepositorySourceView repository={selectedRepository} showProvider={false} runId={runId} />
            </div>
          )}

          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-slate-500">
            <span className="font-mono">{draft.nodes.length} блоков</span>
            <span aria-hidden="true">•</span>
            <span className="font-mono">{draft.edges.length} связей</span>
          </div>
        </div>

        <div className="pointer-events-auto flex min-w-0 flex-1 flex-wrap items-center gap-2">
          <div
            className={`flex h-9 items-center gap-1.5 rounded-xl border px-3 text-xs font-medium shadow-lg backdrop-blur-md ${
              hasErrors
                ? 'border-rose-800 bg-rose-950/90 text-rose-200'
                : validationUnavailable
                  ? 'border-amber-800 bg-amber-950/90 text-amber-100'
                  : validationPending
                    ? 'border-slate-700 bg-slate-900/90 text-slate-300'
                    : 'border-emerald-800 bg-emerald-950/90 text-emerald-200'
            }`}
            role="status"
            aria-live="polite"
          >
            {hasErrors || validationUnavailable ? <AlertCircle size={13} /> : <CheckCircle2 size={13} />}
            <span className="whitespace-nowrap">{validationLabel}</span>
          </div>

          {executionNotice && (
            <div
              className="flex min-w-0 max-w-xl items-start gap-1.5 rounded-xl border border-amber-800 bg-amber-950/90 px-2.5 py-2 text-xs leading-snug text-amber-100 shadow-lg backdrop-blur-md"
              data-execution-binding="mismatch"
            >
              <AlertCircle size={13} className="mt-0.5 shrink-0" />
              <span>{executionNotice}</span>
            </div>
          )}

          {progress && progressLabel && (
            <div
              className="flex h-9 min-w-0 max-w-full items-center gap-2 rounded-xl border border-indigo-900/70 bg-slate-900/95 px-3 shadow-lg backdrop-blur-md"
              data-run-progress={progress.phase}
              data-run-progress-label={progressLabel}
              role="status"
              aria-live="polite"
            >
              <span
                className="h-1.5 w-14 shrink-0 overflow-hidden rounded-full bg-slate-800 sm:w-20"
                aria-hidden="true"
              >
                <span
                  className="block h-full rounded-full transition-[width] duration-500 ease-out"
                  style={{
                    width: `${progress.percent}%`,
                    backgroundColor:
                      progress.phase === 'done'
                        ? progress.verdict === 'succeeded'
                          ? '#10b981'
                          : '#f43f5e'
                        : '#6366f1',
                  }}
                />
              </span>
              <span className="min-w-0 truncate text-[11px] font-medium text-slate-100">{progressLabel}</span>
              {onStopRun && progress.phase === 'running' && (
                <button
                  type="button"
                  onClick={onStopRun}
                  disabled={stopRequested}
                  data-run-stop={stopRequested ? 'stopping' : 'ready'}
                  className="shrink-0 rounded-lg border border-rose-700 bg-rose-600/90 px-2 py-1 text-[10px] font-semibold text-white transition hover:bg-rose-500 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300"
                  title="Остановить запуск"
                >
                  {stopRequested ? 'Останавливаем…' : 'Стоп'}
                </button>
              )}
              {progress.phase === 'done' && runVerdict && runVerdict !== 'pending' && (
                <span
                  className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                    runVerdict === 'pass'
                      ? 'bg-emerald-950/80 text-emerald-200'
                      : runVerdict === 'fail'
                        ? 'bg-rose-950/80 text-rose-200'
                        : 'bg-amber-950/80 text-amber-200'
                  }`}
                  data-run-verdict={runVerdict}
                >
                  Итог: {runVerdict}
                </span>
              )}
              {onOpenRun && (
                <button
                  type="button"
                  onClick={onOpenRun}
                  className="shrink-0 rounded-lg border border-indigo-700 bg-indigo-600/90 px-2 py-1 text-[10px] font-semibold text-white transition hover:bg-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300"
                  title="Открыть подробный ход проверки"
                >
                  Открыть ход проверки
                </button>
              )}
            </div>
          )}
        </div>

        <div className="pointer-events-auto grid w-full grid-cols-2 gap-2 min-[1760px]:ml-auto min-[1760px]:flex min-[1760px]:w-auto min-[1760px]:flex-wrap min-[1760px]:justify-end">
          {onOpenMap && (
            <button
              type="button"
              onClick={onOpenMap}
              className="col-span-2 flex min-h-9 min-w-0 items-center justify-center gap-1.5 rounded-xl border border-sky-800 bg-sky-950/90 px-2.5 py-1.5 text-xs font-medium text-sky-100 shadow-lg backdrop-blur-md transition hover:bg-sky-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 min-[1760px]:col-auto"
              title="Открыть карту доступа выбранного запуска"
            >
              Карта доступа →
            </button>
          )}
          <button
            type="button"
            onClick={onResetToTemplate}
            className="flex min-h-9 min-w-0 items-center justify-center gap-1.5 rounded-xl border border-slate-800 bg-slate-900/90 px-2.5 py-1.5 text-xs font-medium text-slate-200 shadow-lg backdrop-blur-md transition hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
            title="Вернуть сохранённый draft с сервера"
          >
            <RotateCcw size={13} />
            <span className="whitespace-nowrap">Сбросить draft</span>
          </button>

          <button
            type="button"
            onClick={onSaveDraft}
            disabled={isSaving}
            title="Сохранить черновик сценария на сервере"
            className="flex min-h-9 min-w-0 items-center justify-center gap-1.5 rounded-xl bg-indigo-600 px-2.5 py-1.5 text-xs font-semibold text-white shadow-lg shadow-indigo-600/20 transition hover:bg-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
          >
            <Save size={13} />
            <span className="whitespace-nowrap">{isSaving ? 'Сохранение…' : 'Сохранить draft'}</span>
          </button>
        </div>
      </div>

      {/* Validation Issues Drawer if any */}
      {hasErrors && (
        <div className="absolute bottom-3 left-2 right-2 sm:right-auto sm:left-4 z-10 max-w-md bg-rose-950/95 border border-rose-800 rounded-xl p-3 shadow-2xl backdrop-blur-md text-xs text-rose-200 space-y-1">
          <div className="font-semibold text-rose-100 flex items-center gap-1.5">
            <AlertCircle size={14} />
            <span>Требуется исправить перед запуском:</span>
          </div>
          <ul className="list-disc list-inside space-y-0.5 text-[11px] text-rose-300">
            {validationIssues.map((iss, i) => (
              <li key={i}>{iss.message}</li>
            ))}
          </ul>
        </div>
      )}

      {/* ReactFlow Canvas */}
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeDragStop={onNodeDragStop}
        onNodeDragStart={handleNodeDragStart}
        onNodeClick={onNodeClick}
        onPaneClick={onPaneClick}
        onBeforeDelete={onBeforeDelete}
        onInit={handleInit}
        onMoveStart={handleMoveStart}
        deleteKeyCode={['Backspace', 'Delete']}
        fitView
        className="bg-slate-950"
      >
        <Background variant={BackgroundVariant.Dots} gap={16} size={1} color="#334155" />
        <Controls className="!bg-slate-900 !border-slate-800 !text-slate-300" />
        <MiniMap
          nodeColor="#4f46e5"
          maskColor="rgba(15, 23, 42, 0.7)"
          className="!bg-slate-900 !border-slate-800 !rounded-xl"
        />
      </ReactFlow>
    </div>
  );
};
