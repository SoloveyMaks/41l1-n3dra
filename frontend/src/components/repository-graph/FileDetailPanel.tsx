import React, { useState } from 'react';
import { ArrowLeft, ArrowRight, Check, CircleAlert, Copy, Loader2 } from 'lucide-react';
import type {
  FileEvidenceFact,
  MaskedFragmentEntry,
  MaskedReadRequest,
  RepositoryFileFact,
  RepositoryFileLink,
} from './types';
import { EVIDENCE_PREVIEW_LIMIT } from './types';
import {
  SEVERITY_CHIP,
  SEVERITY_LABEL,
  formatBytes,
  formatHash,
  folderName,
  languageLabel,
} from './fileMeta';
import { FileKindIcon, SeverityDot } from './FileIcons';

interface FileDetailPanelProps {
  file: RepositoryFileFact;
  links: readonly RepositoryFileLink[];
  masked: ReadonlyMap<string, MaskedFragmentEntry>;
  onRequestMasked: (request: MaskedReadRequest) => void;
  onSelectFile: (path: string) => void;
}

const MAX_FINDINGS = 6;

const MaskedSnippet: React.FC<{
  path: string;
  evidence: FileEvidenceFact;
  entry: MaskedFragmentEntry | undefined;
  onRequest: (request: MaskedReadRequest) => void;
}> = ({ path, evidence, entry, onRequest }) => {
  if (!entry) {
    return (
      <div className="rounded-lg border border-slate-800 bg-slate-950/70 p-2.5">
        <div className="flex items-center justify-between gap-2 text-[10px] text-slate-500">
          <span>Строки {evidence.line}–{evidence.endLine}</span>
          <button
            type="button"
            onClick={() =>
              onRequest({
                evidenceId: evidence.id,
                path,
                line: evidence.line,
                endLine: evidence.endLine,
              })
            }
            className="rounded border border-slate-700 px-1.5 py-0.5 text-[10px] text-slate-300 hover:bg-slate-800"
          >
            Загрузить masked-фрагмент
          </button>
        </div>
      </div>
    );
  }
  if (entry.loading) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-slate-800 bg-slate-950/70 p-2.5 text-[11px] text-slate-400">
        <Loader2 size={13} className="animate-spin text-indigo-400" />
        Читаем masked-фрагмент, строки {evidence.line}–{evidence.endLine}…
      </div>
    );
  }
  if (!entry.result || entry.result.status === 'unavailable') {
    return (
      <div className="rounded-lg border border-amber-900/70 bg-amber-950/25 p-2.5 text-[11px] text-amber-200">
        <div className="flex items-start gap-2">
          <CircleAlert size={13} className="mt-0.5 shrink-0" />
          <span className="min-w-0 flex-1">
            Фрагмент строк {evidence.line}–{evidence.endLine} недоступен.
            {entry.result?.status === 'unavailable' ? ` ${entry.result.reason}` : ''}
          </span>
          <button
            type="button"
            onClick={() =>
              onRequest({
                evidenceId: evidence.id,
                path,
                line: evidence.line,
                endLine: evidence.endLine,
              })
            }
            className="shrink-0 rounded border border-amber-900 px-1.5 py-0.5 text-[10px] text-amber-200 hover:bg-amber-900/40"
          >
            Повторить
          </button>
        </div>
      </div>
    );
  }
  return (
    <div className="overflow-hidden rounded-lg border border-slate-800 bg-slate-950/70">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-slate-800/80 px-2.5 py-1.5 text-[10px] text-slate-500">
        <span className="font-mono text-slate-400">
          {evidence.line}–{evidence.endLine}
        </span>
        <span className="rounded border border-emerald-900/70 bg-emerald-950/40 px-1.5 text-[9px] uppercase tracking-wide text-emerald-300">
          masked
        </span>
        {entry.result.redactionVersion ? <span>{entry.result.redactionVersion}</span> : null}
        {entry.result.remasked ? <span className="text-amber-300">перемаскировано сервером</span> : null}
      </div>
      <pre className="rg-scroll m-0 max-h-64 overflow-auto whitespace-pre-wrap break-words p-2.5 font-mono text-[11px] leading-5 text-slate-300">
        {entry.result.text}
      </pre>
    </div>
  );
};

const LinkRow: React.FC<{
  link: RepositoryFileLink;
  outgoing: boolean;
  onSelectFile: (path: string) => void;
}> = ({ link, outgoing, onSelectFile }) => {
  const other = outgoing ? link.toPath : link.fromPath;
  return (
    <button
      type="button"
      onClick={() => onSelectFile(other)}
      title={`${link.fromPath} → ${link.toPath}`}
      className="flex w-full items-center gap-2 rounded-md border border-slate-800 bg-slate-950/50 px-2 py-1.5 text-left text-[11px] text-slate-300 transition-colors hover:border-slate-700 hover:bg-slate-900"
    >
      {outgoing ? (
        <ArrowRight size={12} className="shrink-0 text-indigo-300" />
      ) : (
        <ArrowLeft size={12} className="shrink-0 text-cyan-300" />
      )}
      <span className="min-w-0 flex-1 truncate font-mono text-[10px]">{other}</span>
      <span className="shrink-0 text-[10px] text-slate-500">{link.label}</span>
      <span
        className={`shrink-0 rounded border px-1 text-[9px] ${
          link.resolution === 'resolved'
            ? 'border-emerald-900/70 bg-emerald-950/40 text-emerald-300'
            : 'border-amber-900/70 bg-amber-950/40 text-amber-300'
        }`}
        title={`Доказательств: ${link.evidenceCount}`}
      >
        {link.evidenceCount}
      </span>
    </button>
  );
};

export const FileDetailPanel: React.FC<FileDetailPanelProps> = ({
  file,
  links,
  masked,
  onRequestMasked,
  onSelectFile,
}) => {
  const [copied, setCopied] = useState(false);
  const snippets = file.evidence.slice(0, EVIDENCE_PREVIEW_LIMIT);
  const hiddenSnippets = Math.max(0, file.evidence.length - snippets.length);
  const findings = file.findings.slice(0, MAX_FINDINGS);
  const hiddenFindings = Math.max(0, file.findings.length - findings.length);
  const outgoing = links.filter((link) => link.fromPath === file.path);
  const incoming = links.filter((link) => link.toPath === file.path);

  const copyPath = async () => {
    try {
      await navigator.clipboard.writeText(file.path);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="flex h-full min-w-0 flex-col overflow-hidden bg-slate-950/40">
      <header className="border-b border-slate-800 px-4 py-3">
        <div className="flex items-start gap-2.5">
          <div className="mt-0.5 rounded-lg border border-slate-800 bg-slate-900 p-1.5">
            <FileKindIcon path={file.path} size={18} />
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="truncate text-sm font-semibold text-slate-100" title={file.path}>
              {file.path.slice(file.path.lastIndexOf('/') + 1)}
            </h3>
            <div className="mt-0.5 flex min-w-0 items-center gap-1.5">
              <span className="truncate font-mono text-[10px] text-slate-500" title={file.path}>
                {folderName(file.path)}
              </span>
              <button
                type="button"
                onClick={() => void copyPath()}
                className="shrink-0 rounded border border-slate-800 p-0.5 text-slate-500 hover:bg-slate-800 hover:text-slate-300"
                title="Скопировать путь"
              >
                {copied ? <Check size={11} className="text-emerald-400" /> : <Copy size={11} />}
              </button>
            </div>
          </div>
        </div>
        <div className="mt-2.5 flex flex-wrap items-center gap-1.5 text-[10px]">
          <span className="rounded border border-slate-800 bg-slate-900 px-1.5 py-0.5 text-slate-400">
            {languageLabel(file.path)}
          </span>
          {file.worstSeverity ? (
            <span className={`rounded border px-1.5 py-0.5 ${SEVERITY_CHIP[file.worstSeverity]}`}>
              {SEVERITY_LABEL[file.worstSeverity]}
            </span>
          ) : null}
          <span className="rounded border border-slate-800 bg-slate-900 px-1.5 py-0.5 text-slate-400">
            находок: {file.findings.length}
          </span>
          <span className="rounded border border-slate-800 bg-slate-900 px-1.5 py-0.5 text-slate-400">
            узлов: {file.nodeCount}
          </span>
          <span className="rounded border border-slate-800 bg-slate-900 px-1.5 py-0.5 text-slate-400">
            фрагментов: {file.evidence.length}
          </span>
          {formatBytes(file.bytes) ? (
            <span className="rounded border border-slate-800 bg-slate-900 px-1.5 py-0.5 text-slate-400">
              {formatBytes(file.bytes)}
            </span>
          ) : null}
          {formatHash(file.sourceHash) ? (
            <span
              className="rounded border border-slate-800 bg-slate-900 px-1.5 py-0.5 font-mono text-slate-500"
              title={file.sourceHash ?? ''}
            >
              sha {formatHash(file.sourceHash)}
            </span>
          ) : null}
        </div>
      </header>

      <div className="rg-scroll min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
        <section>
          <div className="mb-1.5 flex items-center justify-between">
            <h4 className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
              Masked-код
            </h4>
            <span className="text-[10px] text-slate-600">
              только сохранённые фрагменты, не весь файл
            </span>
          </div>
          {snippets.length ? (
            <div className="space-y-2">
              {snippets.map((evidence) => (
                <MaskedSnippet
                  key={evidence.id}
                  path={file.path}
                  evidence={evidence}
                  entry={masked.get(evidence.id)}
                  onRequest={onRequestMasked}
                />
              ))}
              {hiddenSnippets > 0 ? (
                <p className="text-[10px] text-slate-600">
                  Ещё {hiddenSnippets} фрагмент(ов) не показаны, чтобы не перегружать панель.
                </p>
              ) : null}
            </div>
          ) : (
            <p className="rounded-lg border border-dashed border-slate-800 bg-slate-950/40 p-3 text-[11px] text-slate-500">
              Для этого файла нет сохранённых masked-фрагментов. Путь известен из покрытия,
              но исходник не читался.
            </p>
          )}
        </section>

        <section>
          <h4 className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
            Находки в файле
          </h4>
          {findings.length ? (
            <ul className="space-y-1.5">
              {findings.map((finding) => (
                <li
                  key={finding.id}
                  className="flex items-center gap-2 rounded-md border border-slate-800 bg-slate-950/50 px-2 py-1.5 text-[11px]"
                >
                  <SeverityDot severity={finding.severity} />
                  <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-slate-300" title={finding.ruleId}>
                    {finding.ruleId}
                  </span>
                  <span className="shrink-0 text-[10px] text-slate-500">
                    стр. {finding.line}
                  </span>
                  <span className="shrink-0 text-[10px] text-slate-500">{finding.status}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[11px] text-slate-500">Находок в этом файле нет.</p>
          )}
          {hiddenFindings > 0 ? (
            <p className="mt-1 text-[10px] text-slate-600">Ещё {hiddenFindings} находок скрыто.</p>
          ) : null}
        </section>

        <section>
          <h4 className="mb-1.5 flex items-center justify-between text-[10px] font-semibold uppercase tracking-wider text-slate-400">
            <span>Связи из фактов</span>
            <span className="font-normal normal-case tracking-normal text-slate-600">
              {outgoing.length + incoming.length}
            </span>
          </h4>
          {outgoing.length + incoming.length ? (
            <div className="space-y-1">
              {outgoing.map((link) => (
                <LinkRow key={`out:${link.id}`} link={link} outgoing onSelectFile={onSelectFile} />
              ))}
              {incoming.map((link) => (
                <LinkRow key={`in:${link.id}`} link={link} outgoing={false} onSelectFile={onSelectFile} />
              ))}
            </div>
          ) : (
            <p className="text-[11px] text-slate-500">
              Межфайловых связей с доказательствами нет. Папки и пути не выдаются за граф вызовов.
            </p>
          )}
        </section>
      </div>
    </div>
  );
};