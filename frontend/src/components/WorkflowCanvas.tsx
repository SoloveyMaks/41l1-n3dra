import React, { useEffect, useMemo, useCallback } from 'react';
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
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import { CustomNode } from './CustomNode';
import { ExecutionEdge, ExecutionEdgeData } from './ExecutionEdge';
import type { ArtifactManifest, WorkflowDraft, WorkflowEdge } from '../types';
import type { RunTelemetry } from '../execution/runTelemetry';
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

  // Overlay edge flow. Pulse is true only for edge_data received in this live
  // subscription, so replaying history restores highlights without motion.
  useEffect(() => {
    setEdges((current) =>
      current.map((edge) => {
        const live = telemetry.edges[edge.id];
        return {
          ...edge,
          data: {
            ...edge.data,
            flow: live?.state || 'idle',
            pulse: Boolean(live?.pulse),
          },
        };
      }),
    );
  }, [telemetry, baseEdges, setEdges]);

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
    <div className="relative min-h-[420px] w-full min-w-0 bg-slate-950 xl:h-full xl:min-h-0 xl:flex-1">
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
        onNodeClick={onNodeClick}
        onPaneClick={onPaneClick}
        onBeforeDelete={onBeforeDelete}
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
