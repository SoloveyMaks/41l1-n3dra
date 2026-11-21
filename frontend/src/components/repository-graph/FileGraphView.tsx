import React, { memo, useMemo } from 'react';
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import './repository-graph.css';
import type { Severity } from '../../types';
import type { RepositoryFileGraphData } from './types';
import { SEVERITY_DOT, baseName, folderName } from './fileMeta';
import { FileKindIcon, SeverityDot } from './FileIcons';
import { fileMatchesQuery } from './tree';

const FILE_NODE_WIDTH = 212;
const FILE_NODE_HEIGHT = 64;
const GAP_X = 54;
const GAP_Y = 30;

interface FileNodeData extends Record<string, unknown> {
  path: string;
  name: string;
  folder: string;
  severity: Severity | null;
  findingCount: number;
  evidenceCount: number;
  selected: boolean;
  dimmed: boolean;
  searchMatch: boolean;
  searchDimmed: boolean;
}

type FileFlowNode = Node<FileNodeData, 'file'>;

const FileNodeCard = memo(({ data }: NodeProps) => {
  const node = data as unknown as FileNodeData;
  return (
    <div
      className={`rg-file-node${node.selected ? ' is-selected' : ''}${
        node.dimmed ? ' is-dimmed' : ''
      }${node.searchMatch ? ' is-search-match' : ''}${
        node.searchDimmed ? ' is-search-dimmed' : ''
      }`}
      title={node.path}
    >
      <Handle type="target" position={Position.Left} id="in" className="rg-handle" isConnectable={false} />
      <div className="flex items-center gap-2">
        <FileKindIcon path={node.path} size={16} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] font-semibold leading-4 text-slate-100">
            {node.name}
          </div>
          <div className="truncate text-[10.5px] leading-4 text-slate-500">{node.folder}</div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <SeverityDot severity={node.severity} />
          {node.findingCount > 0 ? (
            <span
              className="rounded-full border border-slate-700 bg-slate-900 px-1 text-[9px] leading-4 text-slate-300"
              title={`Находок: ${node.findingCount}`}
            >
              {node.findingCount}
            </span>
          ) : node.evidenceCount > 0 ? (
            <span className="text-[9px] leading-4 text-slate-600" title={`Masked-фрагментов: ${node.evidenceCount}`}>
              {node.evidenceCount}
            </span>
          ) : null}
        </div>
      </div>
      <Handle type="source" position={Position.Right} id="out" className="rg-handle" isConnectable={false} />
    </div>
  );
});

FileNodeCard.displayName = 'FileNodeCard';

const severityColor = (severity: Severity | null): string => {
  if (!severity) return '#475569';
  if (severity === 'critical') return '#fb7185';
  if (severity === 'high') return '#fb923c';
  if (severity === 'medium') return '#fbbf24';
  if (severity === 'low') return '#38bdf8';
  return '#94a3b8';
};

interface FileGraphViewProps {
  data: RepositoryFileGraphData;
  selectedPath: string | null;
  query: string;
  onSelectFile: (path: string) => void;
}

/**
 * Граф файлов, а не граф вызовов: связи берутся только из links, которые
 * deriveFileGraph собрал из доказанных рёбер фактов. Папки здесь — подпись,
 * а не узел и не связь.
 */
export const FileGraphView: React.FC<FileGraphViewProps> = ({
  data,
  selectedPath,
  query,
  onSelectFile,
}) => {
  const columns = data.files.length <= 4 ? 2 : data.files.length <= 10 ? 3 : 4;
  const searchActive = Boolean(query.trim());
  const neighbours = useMemo(() => {
    const ids = new Set<string>();
    if (!selectedPath) return ids;
    ids.add(selectedPath);
    for (const link of data.links) {
      if (link.fromPath === selectedPath) ids.add(link.toPath);
      if (link.toPath === selectedPath) ids.add(link.fromPath);
    }
    return ids;
  }, [data.links, selectedPath]);

  const nodes = useMemo<FileFlowNode[]>(
    () =>
      data.files.map((file, index) => {
        const searchMatch = fileMatchesQuery(file, query);
        return {
          id: `file:${file.path}`,
          type: 'file',
          position: {
            x: (index % columns) * (FILE_NODE_WIDTH + GAP_X),
            y: Math.floor(index / columns) * (FILE_NODE_HEIGHT + GAP_Y),
          },
          draggable: false,
          selectable: false,
          data: {
            path: file.path,
            name: baseName(file.path),
            folder: folderName(file.path),
            severity: file.worstSeverity,
            findingCount: file.findings.length,
            evidenceCount: file.evidence.length,
            selected: file.path === selectedPath,
            dimmed:
              Boolean(selectedPath) && !neighbours.has(file.path) && file.path !== selectedPath,
            searchMatch,
            searchDimmed: searchActive && !searchMatch,
          },
        };
      }),
    [columns, data.files, neighbours, query, searchActive, selectedPath],
  );

  const edges = useMemo<Edge[]>(
    () =>
      data.links.map((link) => {
        const active =
          Boolean(selectedPath) && (link.fromPath === selectedPath || link.toPath === selectedPath);
        const dimmed = Boolean(selectedPath) && !active;
        return {
          id: link.id,
          source: `file:${link.fromPath}`,
          target: `file:${link.toPath}`,
          animated: false,
          selectable: false,
          label: active ? link.label : undefined,
          labelShowBg: true,
          labelBgStyle: { fill: '#020617', fillOpacity: 0.92 },
          labelBgPadding: [4, 2],
          labelBgBorderRadius: 4,
          labelStyle: { fill: '#cbd5e1', fontSize: 10 },
          markerEnd: {
            type: MarkerType.ArrowClosed,
            color: active ? '#a5b4fc' : '#6366f1',
            width: 16,
            height: 16,
          },
          style: {
            stroke: active ? '#a5b4fc' : '#6366f1',
            strokeWidth: active ? 2 : 0.9,
            opacity: dimmed ? 0.08 : active ? 1 : 0.26,
          },
        };
      }),
    [data.links, selectedPath],
  );

  const nodeTypes = useMemo(() => ({ file: FileNodeCard }), []);

  return (
    <div className="rg-graph relative h-full min-h-0 w-full overflow-hidden rounded-xl border border-slate-800 bg-slate-950">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={(_, node) => onSelectFile((node.data as unknown as FileNodeData).path)}
        nodesDraggable={false}
        nodesConnectable={false}
        edgesReconnectable={false}
        deleteKeyCode={null}
        fitView
        fitViewOptions={{ padding: 0.16, minZoom: 0.82, maxZoom: 1.05 }}
        minZoom={0.22}
        maxZoom={1.8}
        panOnScroll
        proOptions={{ hideAttribution: false }}
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1.1} color="#334155" />
        <Controls showInteractive={false} />
        <MiniMap
          pannable
          zoomable
          nodeColor={(node) => severityColor((node.data as unknown as FileNodeData).severity)}
          nodeStrokeWidth={0}
          maskColor="rgba(2, 6, 23, 0.62)"
          style={{ width: 132, height: 86 }}
        />
      </ReactFlow>

      <div className="pointer-events-none absolute left-3 top-3 z-10 max-w-[min(420px,calc(100%-24px))] rounded-lg border border-slate-800 bg-slate-950/90 px-2.5 py-2 text-[10px] leading-4 text-slate-400 shadow-lg backdrop-blur">
        {data.links.length > 0 ? (
          <>
            <span className="text-slate-300">Связи из фактов: {data.links.length}.</span>{' '}
            Показаны только рёбра с доказательствами; папки и пути не считаются вызовами.
            {data.linksOmitted > 0 ? ` Скрыто без доказательств: ${data.linksOmitted}.` : ''}
            {data.linksInsideFiles > 0 ? ` Внутри файлов: ${data.linksInsideFiles}.` : ''}
          </>
        ) : (
          <>
            <span className="text-slate-300">Межфайловых связей нет.</span> Показаны только
            файлы: иерархия папок не выдаётся за доказанный граф вызовов.
            {data.linksInsideFiles > 0 ? ` Связи внутри файлов: ${data.linksInsideFiles}.` : ''}
          </>
        )}
      </div>

      <div className="pointer-events-none absolute bottom-3 left-1/2 z-10 flex -translate-x-1/2 flex-wrap items-center justify-center gap-2 rounded-lg border border-slate-800 bg-slate-950/90 px-2.5 py-1.5 text-[9px] text-slate-500 shadow-lg backdrop-blur">
        {(['critical', 'high', 'medium', 'low'] as Severity[]).map((severity) => (
          <span key={severity} className="flex items-center gap-1">
            <span className={`h-1.5 w-1.5 rounded-full ${SEVERITY_DOT[severity]}`} />
            {severity === 'critical'
              ? 'критично'
              : severity === 'high'
                ? 'высокий'
                : severity === 'medium'
                  ? 'средний'
                  : 'низкий'}
          </span>
        ))}
      </div>
    </div>
  );
};