import React from 'react';
import {
  Database,
  File,
  FileCode2,
  FileCog,
  FileJson,
  FileText,
} from 'lucide-react';
import type { Severity } from '../../types';
import { SEVERITY_DOT, SEVERITY_LABEL, fileExtension, fileToneClass } from './fileMeta';

interface FileKindIconProps {
  path: string;
  size?: number;
  className?: string;
}

export const FileKindIcon: React.FC<FileKindIconProps> = ({ path, size = 14, className = '' }) => {
  const classes = `shrink-0 ${fileToneClass(path)} ${className}`;
  switch (fileExtension(path)) {
    case '.kt':
    case '.kts':
    case '.java':
    case '.ts':
    case '.tsx':
    case '.js':
    case '.jsx':
    case '.css':
      return <FileCode2 size={size} className={classes} aria-hidden="true" />;
    case '.json':
      return <FileJson size={size} className={classes} aria-hidden="true" />;
    case '.yaml':
    case '.yml':
    case '.toml':
    case '.properties':
    case '.gradle':
    case '.env':
    case '.xml':
      return <FileCog size={size} className={classes} aria-hidden="true" />;
    case '.sql':
      return <Database size={size} className={classes} aria-hidden="true" />;
    case '.md':
    case '.txt':
      return <FileText size={size} className={classes} aria-hidden="true" />;
    default:
      return <File size={size} className={classes} aria-hidden="true" />;
  }
};

export const SeverityDot: React.FC<{ severity: Severity | null; className?: string }> = ({
  severity,
  className = '',
}) => {
  if (!severity) return null;
  return (
    <span
      className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${SEVERITY_DOT[severity]} ${className}`}
      title={SEVERITY_LABEL[severity]}
      aria-label={SEVERITY_LABEL[severity]}
    />
  );
};