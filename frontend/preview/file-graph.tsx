// @ts-nocheck
/// <reference types="vite/client" />
import React from 'react';
import { createRoot } from 'react-dom/client';
import '../src/index.css';
import { RepositoryFileGraphPanel } from '../src/components/repository-graph';
import { RepositorySourceView } from '../src/components/RepositorySourceView';
import { deriveFileGraph } from '../src/components/repository-graph';
import type {
  MaskedReadRequest,
  MaskedReadResult,
  RepositoryFileFact,
  RepositoryFileGraphData,
  RepositoryFileLink,
} from '../src/components/repository-graph';
import published from '../../examples/demo/sql-before-after/current.published.json';

type DemoEvidence = { id: string; line: number; endLine: number };
type DemoFinding = {
  id: string;
  ruleId: string;
  severity: 'critical' | 'high' | 'medium' | 'low' | 'info';
  status: string;
  confidence: string;
  line: number;
};

const modules = import.meta.glob('../src/**/*.{ts,tsx,css,json}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

const dirname = (path: string): string => {
  const index = path.lastIndexOf('/');
  return index === -1 ? '' : path.slice(0, index);
};

const resolveImport = (from: string, spec: string, paths: Set<string>): string | null => {
  if (!spec.startsWith('.')) return null;
  const stack: string[] = [];
  for (const part of `${dirname(from)}/${spec}`.split('/')) {
    if (!part || part === '.') continue;
    if (part === '..') stack.pop();
    else stack.push(part);
  }
  const joined = stack.join('/');
  const candidates = [
    joined,
    `${joined}.ts`,
    `${joined}.tsx`,
    `${joined}.js`,
    `${joined}.jsx`,
    `${joined}/index.ts`,
    `${joined}/index.tsx`,
  ];
  return candidates.find((candidate) => paths.has(candidate)) ?? null;
};

const demoSnippets = new Map<string, string>();

const delay = (ms: number): Promise<void> => {
  const { promise, resolve } = Promise.withResolvers<void>();
  window.setTimeout(resolve, ms);
  return promise;
};

const buildDemoGraph = (): RepositoryFileGraphData => {
  const paths = new Set(Object.keys(modules).map((key) => key.replace(/^\.\.\//, '')));
  const files: RepositoryFileFact[] = [];
  const links = new Map<string, RepositoryFileLink>();

  for (const [key, raw] of Object.entries(modules)) {
    const path = key.replace(/^\.\.\//, '');
    const text = String(raw);
    const lines = text.split('\n');
    const evidence: DemoEvidence[] = [
      {
        id: `demo:${path}`,
        line: 1,
        endLine: Math.min(26, lines.length),
      },
    ];
    demoSnippets.set(evidence[0].id, lines.slice(0, 26).join('\n'));

    const findings: DemoFinding[] = [];
    const secretLine = lines.findIndex((line) => /(secret|token|password)/i.test(line));
    if (secretLine >= 0) {
      findings.push({
        id: `demo-finding:${path}`,
        ruleId: 'DEMO-SECRET-MENTION',
        severity: 'medium',
        status: 'potential',
        confidence: 'low',
        line: secretLine + 1,
      });
    }

    files.push({
      path,
      bytes: new TextEncoder().encode(text).length,
      evidence,
      findings,
      nodeCount: 0,
      worstSeverity: findings.length ? 'medium' : null,
    });

    const importRe = /(?:from\s*|import\s*|import\(\s*)['"]([^'"]+)['"]/g;
    let match: RegExpExecArray | null;
    while ((match = importRe.exec(text))) {
      const target = resolveImport(path, match[1], paths);
      if (!target || target === path) continue;
      const id = `${path}|${target}|imports`;
      if (!links.has(id)) {
        links.set(id, {
          id,
          fromPath: path,
          toPath: target,
          kind: 'imports',
          label: 'Импортирует',
          resolution: 'resolved',
          evidenceCount: 1,
        });
      }
    }
  }

  files.sort((a, b) => a.path.localeCompare(b.path));
  return {
    files,
    links: [...links.values()].sort(
      (a, b) =>
        a.fromPath.localeCompare(b.fromPath) ||
        a.toPath.localeCompare(b.toPath) ||
        a.kind.localeCompare(b.kind),
    ),
    coverage: {
      status: 'complete',
      filesTotal: files.length,
      filesVisited: files.length,
      skippedScopes: [],
    },
    linksOmitted: 0,
    linksInsideFiles: 0,
  };
};

const demoData = buildDemoGraph();
const demoReader = async (request: MaskedReadRequest): Promise<MaskedReadResult> => {
  const text = demoSnippets.get(request.evidenceId);
  if (!text) return { status: 'unavailable', reason: 'Демо-фрагмент не найден.' };
  await delay(90);
  return { status: 'ready', text, redactionVersion: 'demo-frontend/1' };
};

const realBundle = published as unknown as {
  bundle: {
    codeFacts: unknown;
    scanResults: Array<{ findings: unknown[] }>;
  };
};
// The published demo artifact is a historical CodeFactsBundle, not a full
// SecurityMapDto. Complete it with the canonical content hash of its own map
// document (sha256 of sorted compact JSON, as the producer computes it) so the
// production gate validates the demo instead of rejecting a missing field.
const realMap = {
  ...(realBundle.bundle.codeFacts as Record<string, unknown>),
  bundleHash: '3e2844ecf0e3cfee2fa22494d2d0cbd3fc1088fceb8359915204c200407d7ece',
};
const realFindings = realBundle.bundle.scanResults.flatMap((result) => result.findings);
type RealMapFixture = { evidence?: Array<{ id: string; redactedSnippet: string }> };
const realMapFixture = realMap as unknown as RealMapFixture;
const realData = deriveFileGraph(realMap as never, realFindings as never);
const realSnippets = new Map(
  (realMapFixture.evidence ?? []).map((item) => [item.id, item.redactedSnippet]),
);
const realReader = async (request: MaskedReadRequest): Promise<MaskedReadResult> => {
  const text = realSnippets.get(request.evidenceId);
  if (!text) return { status: 'unavailable', reason: 'Нет сохранённого фрагмента.' };
  await delay(90);
  return { status: 'ready', text, redactionVersion: 'fixture' };
};

const fixture = new URLSearchParams(window.location.search).get('fixture');
const useReal = fixture === 'real';
const useModal = fixture === 'modal';
const data = useReal ? realData : demoData;

const modalRepository = {
  url: 'https://github.com/TBagirov/training',
  provider: 'github' as const,
  branch: 'main',
  commit_sha: 'fe1fcbe212669783480666b9b60147c8a8c6eef4',
};
const modalRunId = (realMap as { identity: { runId: string } }).identity.runId;

if (useModal) {
  const rawIdentity = (realMap as { identity?: Record<string, string> }).identity ?? {};
  const runId = rawIdentity.runId;
  const evidenceList =
    (realMap as { evidence?: Array<{ id: string; location: unknown; sourceHash: string; redactedSnippet: string }> })
      .evidence ?? [];
  const evidenceById = new Map(evidenceList.map((item) => [item.id, item]));
  const json = (body: unknown) =>
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  const originalFetch = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url =
      typeof input === 'string' ? input : input instanceof Request ? input.url : String(input);
    if (url.includes('/security-map')) {
      return json({
        identity: {
          workspaceId: rawIdentity.workspaceId,
          runId,
          snapshotId: rawIdentity.snapshotId,
        },
        map: realMap,
        findings: realFindings,
      });
    }
    if (url.includes('/evidence/')) {
      const evidenceId = decodeURIComponent(url.split('/evidence/')[1].split('?')[0]);
      const evidence = evidenceById.get(evidenceId);
      const request = {
        workspaceId: rawIdentity.workspaceId,
        runId,
        snapshotId: rawIdentity.snapshotId,
        evidenceId,
      };
      if (!evidence) {
        return new Response(JSON.stringify({ status: 'unavailable', request, code: 'evidence_not_available' }), {
          status: 404,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return json({
        status: 'ready',
        request,
        sourceHash: evidence.sourceHash,
        location: evidence.location,
        maskedFragment: evidence.redactedSnippet,
        redactionVersion: 'fixture',
      });
    }
    return originalFetch(input, init);
  };
}

const App: React.FC = () => {
  const demoLink = '?fixture=demo';
  const realLink = '?fixture=real';
  if (useModal) {
    return (
      <div className="flex h-screen w-screen flex-col bg-slate-950 p-2 text-slate-100">
        <div className="mb-2 flex flex-wrap items-center gap-2 px-1 text-[11px] text-slate-400">
          <span className="font-semibold text-slate-300">Демо-стенд: модалка из RepositorySourceView</span>
          <span className="text-slate-600">·</span>
          <span>fetch к /api перехвачен фикстурой published bundle</span>
          <a href={demoLink} className="ml-auto rounded border border-slate-700 px-2 py-1 text-slate-300 hover:bg-slate-800">
            Дерево исходников
          </a>
          <a href={realLink} className="rounded border border-slate-700 px-2 py-1 text-slate-300 hover:bg-slate-800">
            Published bundle
          </a>
        </div>
        <div className="flex flex-1 items-center justify-center p-4">
          <div className="w-full max-w-2xl rounded-xl border border-slate-800 bg-slate-900/60 p-4">
            <p className="mb-3 text-[11px] leading-5 text-slate-400">
              Кнопка «Файлы» открывает настоящее модальное окно: оно запрашивает security-map,
              строит файлы и читает masked-фрагменты через тот же код, что и в приложении.
            </p>
            <RepositorySourceView repository={modalRepository} runId={modalRunId} />
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="flex h-screen w-screen flex-col bg-slate-950 p-2 text-slate-100">
      <div className="mb-2 flex flex-wrap items-center gap-2 px-1 text-[11px] text-slate-400">
        <span className="font-semibold text-slate-300">Демо-стенд файлового графа</span>
        <span className="text-slate-600">·</span>
        <span>
          {useReal
            ? 'реальный published bundle: 2 файла Kotlin + настоящие masked-фрагменты'
            : 'рабочее дерево frontend: реальные пути и импорты, синтетические находки только для визуальной проверки'}
        </span>
        <a
          href={demoLink}
          className={`ml-auto rounded border px-2 py-1 ${
            !useReal
              ? 'border-indigo-700 bg-indigo-950/60 text-indigo-200'
              : 'border-slate-700 text-slate-300 hover:bg-slate-800'
          }`}
        >
          Дерево исходников
        </a>
        <a
          href={realLink}
          className={`rounded border px-2 py-1 ${
            useReal
              ? 'border-indigo-700 bg-indigo-950/60 text-indigo-200'
              : 'border-slate-700 text-slate-300 hover:bg-slate-800'
          }`}
        >
          Published bundle
        </a>
        <a
          href="?fixture=modal"
          className="rounded border border-slate-700 px-2 py-1 text-slate-300 hover:bg-slate-800"
        >
          Модалка
        </a>
      </div>
      <div className="min-h-0 flex-1 overflow-hidden rounded-xl border border-slate-800">
        <RepositoryFileGraphPanel
          data={data}
          readMasked={useReal ? realReader : demoReader}
          className="h-full"
        />
      </div>
    </div>
  );
};

createRoot(document.getElementById('root')!).render(<App />);