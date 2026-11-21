import type { Severity } from '../../types';

export const fileExtension = (path: string): string => {
  const name = path.slice(path.lastIndexOf('/') + 1);
  if (name.startsWith('.env')) return '.env';
  const index = name.lastIndexOf('.');
  return index === -1 ? '' : name.slice(index).toLowerCase();
};

export const fileToneClass = (path: string): string => {
  switch (fileExtension(path)) {
    case '.kt':
    case '.kts':
      return 'text-violet-300';
    case '.java':
      return 'text-orange-300';
    case '.ts':
    case '.tsx':
    case '.js':
    case '.jsx':
      return 'text-sky-300';
    case '.json':
      return 'text-amber-300';
    case '.yaml':
    case '.yml':
    case '.toml':
    case '.properties':
    case '.gradle':
    case '.env':
      return 'text-cyan-300';
    case '.sql':
      return 'text-emerald-300';
    case '.md':
    case '.txt':
      return 'text-slate-300';
    case '.css':
      return 'text-fuchsia-300';
    default:
      return 'text-slate-400';
  }
};

export const languageLabel = (path: string): string => {
  switch (fileExtension(path)) {
    case '.kt':
    case '.kts':
      return 'Kotlin';
    case '.java':
      return 'Java';
    case '.ts':
    case '.tsx':
      return 'TypeScript';
    case '.js':
    case '.jsx':
      return 'JavaScript';
    case '.json':
      return 'JSON';
    case '.sql':
      return 'SQL';
    case '.yaml':
    case '.yml':
      return 'YAML';
    case '.md':
      return 'Markdown';
    case '.css':
      return 'CSS';
    case '.gradle':
      return 'Gradle';
    case '.toml':
      return 'TOML';
    default:
      return fileExtension(path).replace('.', '').toUpperCase() || 'Файл';
  }
};

export const SEVERITY_LABEL: Record<Severity, string> = {
  critical: 'Критично',
  high: 'Высокий',
  medium: 'Средний',
  low: 'Низкий',
  info: 'Инфо',
};

export const SEVERITY_DOT: Record<Severity, string> = {
  critical: 'bg-rose-400',
  high: 'bg-orange-400',
  medium: 'bg-amber-400',
  low: 'bg-sky-400',
  info: 'bg-slate-400',
};

export const SEVERITY_CHIP: Record<Severity, string> = {
  critical: 'border-rose-900 bg-rose-950/50 text-rose-200',
  high: 'border-orange-900 bg-orange-950/50 text-orange-200',
  medium: 'border-amber-900 bg-amber-950/50 text-amber-200',
  low: 'border-sky-900 bg-sky-950/50 text-sky-200',
  info: 'border-slate-700 bg-slate-900 text-slate-300',
};

export const formatBytes = (bytes?: number | null): string => {
  if (!bytes || bytes <= 0) return '';
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} КБ`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
};

export const formatHash = (hash?: string | null): string =>
  hash && hash.length > 12 ? `${hash.slice(0, 10)}…` : hash || '';

export const baseName = (path: string): string => {
  const index = path.lastIndexOf('/');
  return index === -1 ? path : path.slice(index + 1);
};

export const folderName = (path: string): string => {
  const folder = path.slice(0, Math.max(0, path.lastIndexOf('/')));
  return folder || 'корень';
};