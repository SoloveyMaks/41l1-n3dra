import type { Severity } from '../../types';
import type { RepositoryFileFact } from './types';

export interface FileTreeFolder {
  kind: 'folder';
  id: string;
  name: string;
  path: string;
  depth: number;
  children: FileTreeNode[];
  fileCount: number;
  findingCount: number;
  worstSeverity: Severity | null;
}

export interface FileTreeFile {
  kind: 'file';
  id: string;
  name: string;
  path: string;
  depth: number;
  file: RepositoryFileFact;
}

export type FileTreeNode = FileTreeFolder | FileTreeFile;

const SEVERITY_RANK: Record<Severity, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
  info: 4,
};

const byName = (left: FileTreeNode, right: FileTreeNode): number => {
  if (left.kind !== right.kind) return left.kind === 'folder' ? -1 : 1;
  return left.name.localeCompare(right.name, 'ru', { numeric: true, sensitivity: 'base' });
};

const worst = (left: Severity | null, right: Severity | null): Severity | null => {
  if (!left) return right;
  if (!right) return left;
  return SEVERITY_RANK[right] < SEVERITY_RANK[left] ? right : left;
};

const dirname = (path: string): string => {
  const index = path.lastIndexOf('/');
  return index === -1 ? '' : path.slice(0, index);
};

const basename = (path: string): string => {
  const index = path.lastIndexOf('/');
  return index === -1 ? path : path.slice(index + 1);
};

/**
 * Строит детерминированное дерево папок и файлов.
 * Папки — только навигация: они не создают рёбер и не утверждают вызовы.
 */
export function buildFileTree(source: readonly RepositoryFileFact[]): FileTreeNode[] {
  type DraftFolder = Omit<FileTreeFolder, 'children' | 'fileCount' | 'findingCount' | 'worstSeverity'> & {
    children: Array<DraftFolder | FileTreeFile>;
    fileCount: number;
    findingCount: number;
    worstSeverity: Severity | null;
  };

  const root: DraftFolder = {
    kind: 'folder',
    id: '',
    name: '',
    path: '',
    depth: -1,
    children: [],
    fileCount: 0,
    findingCount: 0,
    worstSeverity: null,
  };
  const folders = new Map<string, DraftFolder>([['', root]]);

  const ensureFolder = (path: string): DraftFolder => {
    const existing = folders.get(path);
    if (existing) return existing;
    const parentPath = dirname(path);
    const parent = parentPath ? ensureFolder(parentPath) : root;
    const folder: DraftFolder = {
      kind: 'folder',
      id: `folder:${path}`,
      name: basename(path),
      path,
      depth: parent === root ? 0 : parent.depth + 1,
      children: [],
      fileCount: 0,
      findingCount: 0,
      worstSeverity: null,
    };
    parent.children.push(folder);
    folders.set(path, folder);
    return folder;
  };

  for (const file of [...source].sort((a, b) => a.path.localeCompare(b.path))) {
    const folderPath = dirname(file.path);
    const folder = folderPath ? ensureFolder(folderPath) : root;
    folder.children.push({
      kind: 'file',
      id: `file:${file.path}`,
      name: basename(file.path),
      path: file.path,
      depth: folder === root ? 0 : folder.depth + 1,
      file,
    });
  }

  const finalize = (draft: DraftFolder): FileTreeFolder => {
    let fileCount = 0;
    let findingCount = 0;
    let worstSeverity: Severity | null = null;
    const children = draft.children
      .map((child) => {
        if (child.kind === 'file') {
          fileCount += 1;
          findingCount += child.file.findings.length;
          worstSeverity = worst(worstSeverity, child.file.worstSeverity);
          return child;
        }
        const folder = finalize(child);
        fileCount += folder.fileCount;
        findingCount += folder.findingCount;
        worstSeverity = worst(worstSeverity, folder.worstSeverity);
        return folder;
      })
      .sort(byName);
    return {
      kind: 'folder',
      id: draft.id,
      name: draft.name,
      path: draft.path,
      depth: draft.depth,
      children,
      fileCount,
      findingCount,
      worstSeverity,
    };
  };

  return finalize(root).children;
}

export function collectFolderIds(nodes: readonly FileTreeNode[]): string[] {
  const ids: string[] = [];
  const walk = (items: readonly FileTreeNode[]) => {
    for (const item of items) {
      if (item.kind !== 'folder') continue;
      ids.push(item.id);
      walk(item.children);
    }
  };
  walk(nodes);
  return ids;
}

/** Папки, которые нужно раскрыть, чтобы показать файл в дереве. */
export function folderIdsForPath(path: string): string[] {
  const parts = path.split('/');
  const ids: string[] = [];
  for (let index = 1; index < parts.length; index += 1) {
    ids.push(`folder:${parts.slice(0, index).join('/')}`);
  }
  return ids;
}

/**
 * Один матчер для дерева и графа: путь файла или правило его находки.
 * Пустая строка совпадает со всем.
 */
export function fileMatchesQuery(file: RepositoryFileFact, query: string): boolean {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return true;
  return (
    file.path.toLocaleLowerCase().includes(needle) ||
    file.findings.some((finding) => finding.ruleId.toLocaleLowerCase().includes(needle))
  );
}

/** Оставляет только совпавшие файлы и папки-предки. Исходный depth не меняется. */
export function filterFileTree(nodes: readonly FileTreeNode[], query: string): FileTreeNode[] {
  const needle = query.trim();
  if (!needle) return [...nodes];
  const walk = (items: readonly FileTreeNode[]): FileTreeNode[] => {
    const result: FileTreeNode[] = [];
    for (const item of items) {
      if (item.kind === 'file') {
        if (fileMatchesQuery(item.file, needle)) result.push(item);
        continue;
      }
      const children = walk(item.children);
      if (!children.length) continue;
      result.push({ ...item, children });
    }
    return result;
  };
  return walk(nodes);
}

export function countFiles(nodes: readonly FileTreeNode[]): number {
  let total = 0;
  for (const item of nodes) {
    total += item.kind === 'file' ? 1 : countFiles(item.children);
  }
  return total;
}