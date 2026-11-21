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
import { WorkflowDraft, WorkflowEdge } from '../types';
import type { RunTelemetry } from '../execution/runTelemetry';
import { AlertCircle, CheckCircle2, Plus, RotateCcw, Save, Sparkles } from 'lucide-react';
import { RepositorySourceView } from './RepositorySourceView';
import { artifactRepository } from '../repository';

interface WorkflowCanvasProps {
  draft: WorkflowDraft;
  onDraftChange: (updated: WorkflowDraft) => void;
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string | null) => void;
  telemetry: RunTelemetry;
  executionNotice?: string | null;
  validationIssues: Array<{ level: string; message: string }>;
  onSaveDraft: () => void;
  onResetToTemplate: () => void;
  isSaving: boolean;
  workflows: any[];
  workflowId: string;
  onSelectWorkflow: (workflowId: string) => void;
  onCreateWorkflow?: () => void;
  onGenerateWorkflow?: () => void;
  artifacts: import('../types').ArtifactManifest[];
  currentArtifactId: string;
  onSelectArtifact: (artifactId: string) => void;
}

export const WorkflowCanvas: React.FC<WorkflowCanvasProps> = ({
  draft,
  onDraftChange,
  selectedNodeId,
  onSelectNode,
  telemetry,
  executionNotice,
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
}) => {
  const nodeTypes = useMemo(() => ({ customNode: CustomNode }), []);
  const edgeTypes = useMemo(() => ({ executionEdge: ExecutionEdge }), []);
  const selectedArtifact = artifacts.find((artifact) => artifact.id === currentArtifactId);
  const selectedRepository = artifactRepository(selectedArtifact);

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
        </div>

        <div
          className={`pointer-events-auto flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl border text-[11px] shadow-lg backdrop-blur-md ${
            hasErrors
              ? 'bg-rose-950/90 border-rose-800 text-rose-300'
              : 'bg-emerald-950/90 border-emerald-800 text-emerald-300'
          }`}
        >
          {hasErrors ? <AlertCircle size={13} /> : <CheckCircle2 size={13} />}
          <span>{hasErrors ? `Ошибок: ${validationIssues.filter((issue) => issue.level === 'error').length}` : 'Граф валиден'}</span>
        </div>

        {executionNotice && (
          <div
            className="pointer-events-auto flex max-w-md items-start gap-1.5 rounded-xl border border-amber-800 bg-amber-950/90 px-2.5 py-1.5 text-[11px] text-amber-200 shadow-lg backdrop-blur-md"
            data-execution-binding="mismatch"
          >
            <AlertCircle size={13} className="mt-0.5 shrink-0" />
            <span>{executionNotice}</span>
          </div>
        )}

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
