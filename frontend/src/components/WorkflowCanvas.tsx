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
import { WorkflowDraft, WorkflowNode, WorkflowEdge, NodeRunStatus } from '../types';
import { AlertCircle, CheckCircle2, RotateCcw, Save } from 'lucide-react';

interface WorkflowCanvasProps {
  draft: WorkflowDraft;
  onDraftChange: (updated: WorkflowDraft) => void;
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string | null) => void;
  nodeStatuses?: Record<string, NodeRunStatus>;
  validationIssues: Array<{ level: string; message: string }>;
  onSaveDraft: () => void;
  onResetToTemplate: () => void;
  isSaving: boolean;
}

export const WorkflowCanvas: React.FC<WorkflowCanvasProps> = ({
  draft,
  onDraftChange,
  selectedNodeId,
  onSelectNode,
  nodeStatuses = {},
  validationIssues,
  onSaveDraft,
  onResetToTemplate,
  isSaving,
}) => {
  const nodeTypes = useMemo(() => ({ customNode: CustomNode }), []);

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
      },
    }));
  }, [draft.nodes, selectedNodeId, nodeStatuses]);

  // Map our domain WorkflowEdge to ReactFlow Edge
  const initialRfEdges: Edge[] = useMemo(() => {
    return draft.edges.map((e) => {
      const isCondition = e.sourcePort === 'true_branch' || e.sourcePort === 'false_branch';
      const edgeColor =
        e.sourcePort === 'true_branch'
          ? '#34d399'
          : e.sourcePort === 'false_branch'
          ? '#f87171'
          : '#818cf8';

      return {
        id: e.id,
        source: e.source,
        sourceHandle: e.sourcePort,
        target: e.target,
        targetHandle: e.targetPort,
        animated: isCondition,
        style: { stroke: edgeColor, strokeWidth: 2 },
      };
    });
  }, [draft.edges]);

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
      <div className="absolute top-3 left-4 z-10 flex items-center gap-2">
        <div className="bg-slate-900/90 border border-slate-800 rounded-xl px-3 py-1.5 shadow-lg backdrop-blur-md flex items-center gap-2 text-xs">
          <span className="font-semibold text-slate-200">{draft.name}</span>
          <span className="text-slate-500">•</span>
          <span className="text-slate-400 font-mono">{draft.nodes.length} блоков</span>
          <span className="text-slate-500">•</span>
          <span className="text-slate-400 font-mono">{draft.edges.length} связей</span>
        </div>

        {/* Validation indicator */}
        <div
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-xl border text-xs shadow-lg backdrop-blur-md ${
            hasErrors
              ? 'bg-rose-950/80 border-rose-800 text-rose-300'
              : 'bg-emerald-950/80 border-emerald-800 text-emerald-300'
          }`}
        >
          {hasErrors ? <AlertCircle size={14} /> : <CheckCircle2 size={14} />}
          <span>{hasErrors ? `Ошибок в графе: ${validationIssues.length}` : 'Граф валиден (DAG OK)'}</span>
        </div>
      </div>

      <div className="absolute top-3 right-4 z-10 flex items-center gap-2">
        <button
          onClick={onResetToTemplate}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-slate-900/90 hover:bg-slate-800 border border-slate-800 text-slate-300 text-xs font-medium shadow-lg backdrop-blur-md transition cursor-pointer"
        >
          <RotateCcw size={13} />
          <span>Шаблон PR Gate</span>
        </button>

        <button
          onClick={onSaveDraft}
          disabled={isSaving}
          className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-lg shadow-indigo-600/20 transition cursor-pointer"
        >
          <Save size={13} />
          <span>{isSaving ? 'Сохранение...' : 'Сохранить draft'}</span>
        </button>
      </div>

      {/* Validation Issues Drawer if any */}
      {hasErrors && (
        <div className="absolute bottom-4 left-4 z-10 max-w-md bg-rose-950/95 border border-rose-800 rounded-xl p-3 shadow-2xl backdrop-blur-md text-xs text-rose-200 space-y-1">
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
