import React, { useEffect, useMemo, useCallback } from 'react';
import {
  ReactFlow,
  BaseEdge,
  Controls,
  Background,
  MiniMap,
  MarkerType,
  useNodesState,
  useEdgesState,
  Connection,
  Edge,
  EdgeProps,
  Node,
  getBezierPath,
  BackgroundVariant,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import { CustomNode } from './CustomNode';
import { WorkflowDraft, WorkflowEdge, NodeRunStatus, ArtifactManifest } from '../types';
import { AlertCircle, CheckCircle2, Plus, RotateCcw, Save, Sparkles } from 'lucide-react';
import { RepositorySourceView } from './RepositorySourceView';
import { artifactRepository } from '../repository';

export type EdgeFlow = { seq: number; source?: string; target?: string };
type LiveEdgeData = { flowed?: boolean; inactive?: boolean; animate?: boolean };

/**
 * Edge that moves only when the backend emitted `edge_data` for it. A moving
 * dot therefore means "data actually crossed this edge", not "this is the next
 * step in a guessed linear order".
 */
function LiveEdge({
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style,
  markerEnd,
  data,
}: EdgeProps) {
  const [path] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });
  const state = data as LiveEdgeData | undefined;
  return (
    <>
      <BaseEdge path={path} markerEnd={markerEnd} style={style} />
      {state?.animate ? (
        <>
          <circle r={4.6} fill="#a5f3fc" className="wf-flow-dot">
            <animateMotion dur="1.35s" repeatCount="indefinite" path={path} />
          </circle>
          <circle r={2.1} fill="#22d3ee" className="wf-flow-dot">
            <animateMotion
              dur="1.35s"
              begin="0.22s"
              repeatCount="indefinite"
              path={path}
            />
          </circle>
        </>
      ) : null}
    </>
  );
}

interface WorkflowCanvasProps {
  draft: WorkflowDraft;
  onDraftChange: (updated: WorkflowDraft) => void;
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string | null) => void;
  nodeStatuses?: Record<string, NodeRunStatus>;
  edgeFlows?: Record<string, EdgeFlow>;
  executionActive?: boolean;
  validationIssues: Array<{ level: string; message: string }>;
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
  onOpenMap?: () => void;
}

export const WorkflowCanvas: React.FC<WorkflowCanvasProps> = ({
  draft,
  onDraftChange,
  selectedNodeId,
  onSelectNode,
  nodeStatuses = {},
  edgeFlows = {},
  executionActive = false,
  validationIssues,
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
  onOpenMap,
}) => {
  const nodeTypes = useMemo(() => ({ customNode: CustomNode }), []);
  const edgeTypes = useMemo(() => ({ liveEdge: LiveEdge }), []);
  const selectedArtifact = artifacts.find((artifact) => artifact.id === currentArtifactId);
  const selectedRepository = artifactRepository(selectedArtifact);
  const flowedEdgeIds = useMemo(() => new Set(Object.keys(edgeFlows)), [edgeFlows]);
  const flowedSourceIds = useMemo(() => {
    const sources = new Set<string>();
    for (const edge of draft.edges) {
      if (flowedEdgeIds.has(edge.id)) sources.add(edge.source);
    }
    return sources;
  }, [draft.edges, flowedEdgeIds]);
  const conditionSources = useMemo(
    () => new Set(draft.nodes.filter((node) => node.kind === 'condition').map((node) => node.id)),
    [draft.nodes],
  );
  const runningNodeIds = useMemo(
    () =>
      Object.entries(nodeStatuses)
        .filter(([, status]) => status === 'running')
        .map(([id]) => id),
    [nodeStatuses],
  );

  // Map our domain WorkflowNode to ReactFlow Node
  const initialRfNodes: Node[] = useMemo(() => {
    return draft.nodes.map((n) => ({
      id: n.id,
      type: 'customNode',
      position: n.position || { x: 100, y: 100 },
      selected: n.id === selectedNodeId,
      data: {
        kind: n.kind,
        title: n.title || n.kind,
        config: n.config,
        status: nodeStatuses[n.id] || 'pending',
        current: nodeStatuses[n.id] === 'running',
      },
    }));
  }, [draft.nodes, selectedNodeId, nodeStatuses]);

  // Map our domain WorkflowEdge to ReactFlow Edge. Dots are shown only for
  // edges the backend confirmed with `edge_data`; untaken condition branches are
  // dimmed only when the other branch of the same condition actually carried data.
  const initialRfEdges: Edge[] = useMemo(() => {
    return draft.edges.map((e) => {
      const edgeColor =
        e.sourcePort === 'true_branch'
          ? '#34d399'
          : e.sourcePort === 'false_branch'
          ? '#f87171'
          : '#818cf8';
      const flowed = flowedEdgeIds.has(e.id);
      const inactive =
        conditionSources.has(e.source) && !flowed && flowedSourceIds.has(e.source);

      return {
        id: e.id,
        source: e.source,
        sourceHandle: e.sourcePort,
        target: e.target,
        targetHandle: e.targetPort,
        type: 'liveEdge',
        animated: false,
        markerEnd: { type: MarkerType.ArrowClosed, color: edgeColor },
        data: { flowed, inactive, animate: flowed && executionActive },
        style: {
          stroke: edgeColor,
          strokeWidth: flowed ? 2.6 : 1.7,
          opacity: inactive ? 0.22 : flowed ? 1 : 0.72,
          strokeDasharray: inactive ? '5 4' : undefined,
        },
        className: flowed ? 'wf-edge--flowed' : inactive ? 'wf-edge--inactive' : undefined,
      };
    });
  }, [draft.edges, flowedEdgeIds, flowedSourceIds, conditionSources, executionActive]);

  const [nodes, setNodes, onNodesChange] = useNodesState(initialRfNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialRfEdges);

  // Sync when draft changes from outside
  useEffect(() => {
    setNodes(initialRfNodes);
  }, [initialRfNodes, setNodes]);

  useEffect(() => {
    setEdges(initialRfEdges);
  }, [initialRfEdges, setEdges]);

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

  const hasErrors = validationIssues.some((i) => i.level === 'error');

  return (
    <div className="flex-1 h-full relative bg-slate-950">
      {/* Top Bar on Canvas */}
      <div className="absolute top-2 left-2 right-2 z-10 flex flex-wrap items-start gap-2 pointer-events-none">
        <div className="pointer-events-auto flex flex-wrap items-center gap-2 min-w-0 rounded-xl border border-slate-800 bg-slate-900/95 px-2.5 py-1.5 shadow-lg backdrop-blur-md text-xs">
          <label className="flex items-center gap-1.5 min-w-0">
            <span className="text-[10px] uppercase tracking-wider text-slate-500">Сценарий</span>
            <select
              value={workflowId}
              onChange={(event) => onSelectWorkflow(event.target.value)}
              className="max-w-[170px] sm:max-w-[230px] bg-slate-950 border border-slate-700 rounded px-1.5 py-0.5 text-[11px] text-slate-200"
              title="Выбрать сохранённый сценарий"
            >
              {workflows.map((workflow) => (
                <option key={workflow.id} value={workflow.id}>
                  {workflow.name || workflow.id}
                </option>
              ))}
            </select>
          </label>
          {onCreateWorkflow && (
            <button
              onClick={onCreateWorkflow}
              className="flex items-center gap-1 rounded border border-indigo-800 bg-indigo-950/60 px-1.5 py-0.5 text-[11px] text-indigo-300 hover:bg-indigo-900"
              title="Создать новый сценарий на основе текущих блоков"
            >
              <Plus size={12} /> Новый
            </button>
          )}
          {onGenerateWorkflow && (
            <button
              onClick={onGenerateWorkflow}
              className="flex items-center gap-1 rounded border border-fuchsia-800 bg-fuchsia-950/60 px-1.5 py-0.5 text-[11px] text-fuchsia-200 hover:bg-fuchsia-900"
              title="Собрать черновик сценария из текста или голоса"
            >
              <Sparkles size={12} /> По описанию
            </button>
          )}
          <label className="flex items-center gap-1.5 min-w-0">
            <span className="text-[10px] uppercase tracking-wider text-slate-500">Проект</span>
            <select
              value={currentArtifactId}
              onChange={(event) => onSelectArtifact(event.target.value)}
              className="max-w-[170px] sm:max-w-[240px] bg-slate-950 border border-slate-700 rounded px-1.5 py-0.5 text-[11px] text-slate-200"
              title="Артефакт, который попадёт в запуск"
            >
              <option value="">{artifacts.length ? 'Авто-выбор' : 'Нет артефактов'}</option>
              {artifacts.map((artifact) => (
                <option key={artifact.id} value={artifact.id}>
                  {artifact.filename} · {artifact.kotlin_file_count} kt · {artifact.id.slice(0, 6)}
                </option>
              ))}
            </select>
          </label>
          {selectedRepository && (
            <div className="pointer-events-auto min-w-0 rounded-lg border border-indigo-900/70 bg-indigo-950/40 px-2 py-1">
              <RepositorySourceView repository={selectedRepository} showProvider={false} />
            </div>
          )}
          <span className="hidden sm:inline text-slate-600">•</span>
          <span className="hidden sm:inline text-slate-400 font-mono">{draft.nodes.length} блоков</span>
          <span className="hidden sm:inline text-slate-600">•</span>
          <span className="hidden sm:inline text-slate-400 font-mono">{draft.edges.length} связей</span>
          <span className="hidden md:inline text-[10px] text-slate-500" title="Этот граф показывает, как обрабатываются файлы. Карта доступа показывает, кто и через какую проверку доходит до данных.">
            обработка файлов
          </span>
          {onOpenMap && (
            <button
              onClick={onOpenMap}
              className="rounded border border-indigo-800 bg-indigo-950/60 px-1.5 py-0.5 text-[11px] text-indigo-200 hover:bg-indigo-900"
              title="Кто обращается → проверка доступа → маршрут → обработчик → данные. Это другой граф, не структура файлов."
            >
              Карта доступа →
            </button>
          )}
        </div>

        <div
          className={`pointer-events-auto flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl border text-[11px] shadow-lg backdrop-blur-md ${
            hasErrors
              ? 'bg-rose-950/90 border-rose-800 text-rose-300'
              : runningNodeIds.length
                ? 'bg-sky-950/90 border-sky-700 text-sky-200'
                : 'bg-emerald-950/90 border-emerald-800 text-emerald-300'
          }`}
          title={
            runningNodeIds.length
              ? `Выполняется сейчас: ${runningNodeIds.length}. Параллельные ветки не упорядочиваются.`
              : undefined
          }
        >
          {hasErrors ? (
            <AlertCircle size={13} />
          ) : runningNodeIds.length ? (
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-sky-400 opacity-70" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-sky-300" />
            </span>
          ) : (
            <CheckCircle2 size={13} />
          )}
          <span>
            {hasErrors
              ? `Ошибок: ${validationIssues.filter((issue) => issue.level === 'error').length}`
              : runningNodeIds.length
                ? `Выполняется: ${runningNodeIds.length}`
                : 'Граф валиден'}
          </span>
          {flowedEdgeIds.size > 0 && (
            <span className="text-[10px] text-sky-300/80">
              · данные прошли по {flowedEdgeIds.size}{' '}
              {flowedEdgeIds.size === 1 ? 'связи' : 'связям'}
            </span>
          )}
        </div>

        <div className="pointer-events-auto ml-auto flex items-center gap-2">
        <button
          onClick={onResetToTemplate}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl bg-slate-900/90 hover:bg-slate-800 border border-slate-800 text-slate-300 text-xs font-medium shadow-lg backdrop-blur-md transition cursor-pointer"
          title="Вернуть сохранённый draft с сервера"
        >
          <RotateCcw size={13} />
          <span className="hidden sm:inline">Сбросить draft</span>
        </button>

        <button
          onClick={onSaveDraft}
          disabled={isSaving}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-lg shadow-indigo-600/20 transition cursor-pointer"
        >
          <Save size={13} />
          <span>{isSaving ? 'Сохранение...' : 'Сохранить draft'}</span>
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
        deleteKeyCode={null}
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
