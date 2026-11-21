import React from 'react';
import { ChevronRight, Folder, FolderOpen } from 'lucide-react';
import type { FileTreeFolder, FileTreeNode } from './tree';
import { FileKindIcon, SeverityDot } from './FileIcons';

interface FileTreeViewProps {
  nodes: readonly FileTreeNode[];
  expanded: ReadonlySet<string>;
  selectedPath: string | null;
  onToggleFolder: (id: string) => void;
  onSelectFile: (path: string) => void;
}

const Guides: React.FC<{ depth: number }> = ({ depth }) => (
  <>
    {Array.from({ length: depth }, (_, index) => (
      <span
        key={index}
        aria-hidden="true"
        className="rg-tree-guide"
        style={{ left: `${16 + index * 14}px` }}
      />
    ))}
  </>
);

const FileRow: React.FC<{
  node: Extract<FileTreeNode, { kind: 'file' }>;
  selected: boolean;
  onSelect: (path: string) => void;
}> = ({ node, selected, onSelect }) => (
  <li role="treeitem" aria-selected={selected}>
    <button
      type="button"
      onClick={() => onSelect(node.path)}
      aria-current={selected ? 'true' : undefined}
      title={node.path}
      className={`relative flex w-full items-center gap-1.5 rounded-md py-[5px] pr-2 text-left text-[12px] transition-colors ${
        selected
          ? 'bg-indigo-500/15 text-indigo-100 ring-1 ring-inset ring-indigo-500/30'
          : 'text-slate-300 hover:bg-slate-800/70 hover:text-slate-100'
      }`}
      style={{ paddingLeft: `${10 + node.depth * 14}px` }}
    >
      <Guides depth={node.depth} />
      <span className="w-[13px] shrink-0" aria-hidden="true" />
      <FileKindIcon path={node.path} size={14} />
      <span className="min-w-0 flex-1 truncate">{node.name}</span>
      <SeverityDot severity={node.file.worstSeverity} />
      {node.file.findings.length > 0 ? (
        <span
          className="shrink-0 rounded-full border border-slate-700 bg-slate-900 px-1.5 text-[10px] leading-4 text-slate-300"
          title={`Находок: ${node.file.findings.length}`}
        >
          {node.file.findings.length}
        </span>
      ) : null}
      {node.file.evidence.length > 0 ? (
        <span
          className="hidden shrink-0 text-[10px] text-slate-600 sm:inline"
          title={`masked-фрагментов: ${node.file.evidence.length}`}
        >
          {node.file.evidence.length}
        </span>
      ) : null}
    </button>
  </li>
);

const FolderRow: React.FC<{
  node: FileTreeFolder;
  expanded: ReadonlySet<string>;
  selectedPath: string | null;
  onToggle: (id: string) => void;
  onSelectFile: (path: string) => void;
}> = ({ node, expanded, selectedPath, onToggle, onSelectFile }) => {
  const open = expanded.has(node.id);
  return (
    <li role="treeitem" aria-expanded={open}>
      <button
        type="button"
        onClick={() => onToggle(node.id)}
        aria-expanded={open}
        title={node.path}
        className="relative flex w-full items-center gap-1.5 rounded-md py-[5px] pr-2 text-left text-[12px] text-slate-300 transition-colors hover:bg-slate-800/70 hover:text-slate-100"
        style={{ paddingLeft: `${10 + node.depth * 14}px` }}
      >
        <Guides depth={node.depth} />
        <ChevronRight
          size={13}
          className={`shrink-0 text-slate-500 transition-transform ${open ? 'rotate-90' : ''}`}
          aria-hidden="true"
        />
        {open ? (
          <FolderOpen size={14} className="shrink-0 text-indigo-300" aria-hidden="true" />
        ) : (
          <Folder size={14} className="shrink-0 text-indigo-300/80" aria-hidden="true" />
        )}
        <span className="min-w-0 flex-1 truncate">{node.name}</span>
        <SeverityDot severity={node.worstSeverity} />
        <span className="shrink-0 text-[10px] text-slate-500" title={`Файлов: ${node.fileCount}`}>
          {node.fileCount}
        </span>
      </button>
      {open ? (
        <ul role="group" className="m-0 list-none p-0">
          {node.children.map((child) =>
            child.kind === 'folder' ? (
              <FolderRow
                key={child.id}
                node={child}
                expanded={expanded}
                selectedPath={selectedPath}
                onToggle={onToggle}
                onSelectFile={onSelectFile}
              />
            ) : (
              <FileRow
                key={child.id}
                node={child}
                selected={selectedPath === child.path}
                onSelect={onSelectFile}
              />
            ),
          )}
        </ul>
      ) : null}
    </li>
  );
};

export const FileTreeView: React.FC<FileTreeViewProps> = ({
  nodes,
  expanded,
  selectedPath,
  onToggleFolder,
  onSelectFile,
}) => (
  <ul className="m-0 list-none space-y-px p-0" role="tree" aria-label="Файлы проверки">
    {nodes.map((node) =>
      node.kind === 'folder' ? (
        <FolderRow
          key={node.id}
          node={node}
          expanded={expanded}
          selectedPath={selectedPath}
          onToggle={onToggleFolder}
          onSelectFile={onSelectFile}
        />
      ) : (
        <FileRow
          key={node.id}
          node={node}
          selected={selectedPath === node.path}
          onSelect={onSelectFile}
        />
      ),
    )}
  </ul>
);