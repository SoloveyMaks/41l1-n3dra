import { useEffect, useRef, useState } from "react";
import {
  Background,
  Controls,
  MarkerType,
  ReactFlow,
  applyNodeChanges,
  type Node,
  type Edge,
  type NodeChange,
  type ReactFlowInstance,
} from "@xyflow/react";
import type {
  EvidenceRequest,
  LoadState,
  MapViewModel,
  ScopedSelection,
  SecurityMapLayout,
  Selection,
  SourceState,
} from "./types";
import { sameScope } from "./model";
import "@xyflow/react/dist/style.css";
import "./style.css";

export type MapViewProps = {
  model: MapViewModel;
  loadState: LoadState;
  layout?: SecurityMapLayout;
  selection?: ScopedSelection | null;
  sourceState?: SourceState;
  onSelect?: (selection: Selection) => void;
  onOpenEvidence?: (request: EvidenceRequest) => void;
  onLayoutChange?: (layout: SecurityMapLayout) => void;
  onRetry: () => void;
  onRetrySource?: (request: EvidenceRequest) => void;
};
const resolutionText = {
  resolved: "Разрешена",
  partial: "Частично установлена",
  unknown: "Неизвестна",
};
const defaultPosition = (i: number) => ({
  x: (i % 2) * 320,
  y: Math.floor(i / 2) * 180,
});
const sameRequest = (a: EvidenceRequest, b: EvidenceRequest) =>
  sameScope(a, b) && a.evidenceId === b.evidenceId;

export function Failure({
  code,
  onRetry,
}: {
  code: string;
  onRetry: () => void;
}) {
  return (
    <div className="sm-state" role="alert">
      <h2>Карта недоступна</h2>
      <p>
        {code === "MAP_LOAD_FAILED"
          ? "Не удалось загрузить карту. Повторите запрос."
          : "Данные не прошли проверку. Чужие или несогласованные фрагменты скрыты."}
      </p>
      <code>{code}</code>
      <button onClick={onRetry}>Повторить загрузку</button>
    </div>
  );
}

export function MapView(props: MapViewProps) {
  const { model, loadState, layout, sourceState, onRetry } = props;
  const canvasRef = useRef<HTMLDivElement>(null);
  const [flow, setFlow] = useState<ReactFlowInstance | null>(null);
  useEffect(() => {
    if (!flow || !canvasRef.current) return;
    let initial = true;
    const observer = new ResizeObserver(() => {
      if (initial) {
        initial = false;
        return;
      }
      void flow.fitView({ padding: 0.2 });
    });
    observer.observe(canvasRef.current);
    return () => observer.disconnect();
  }, [flow]);
  const detailsRef = useRef<HTMLElement>(null);
  const sourceRef = useRef<HTMLElement>(null);
  const unresolvedEdges = model.edges.filter(
    (edge) => edge.resolution !== "resolved",
  );
  const manual = model.provenance === "manual_source_review";
  const initialNodes = (): Node[] =>
    model.nodes.map((n, i) => ({
      id: n.id,
      position: layout?.nodePositions[n.id] ?? defaultPosition(i),
      ariaLabel: n.label,
      data: {
        label: (
          <>
            <span className="sm-node-kind">{n.kind}</span>
            <strong>{n.label}</strong>
          </>
        ),
      },
      style: { width: 245 },
      sourcePosition: undefined,
    }));
  const [nodes, setNodes] = useState<Node[]>(initialNodes);
  useEffect(() => {
    if (layout) setNodes(initialNodes());
  }, [layout?.viewId, JSON.stringify(layout?.nodePositions)]);
  const [localSelection, setLocalSelection] = useState<Selection | null>(null);
  const [openRequest, setOpenRequest] = useState<EvidenceRequest | null>(null);
  const selection =
    props.selection === undefined
      ? localSelection
      : (props.selection?.value ?? null);
  const evidence =
    openRequest && model.evidence.find((e) => e.id === openRequest.evidenceId);
  useEffect(() => {
    setOpenRequest(null);
  }, [selection?.kind, selection?.id]);
  useEffect(() => {
    if (selection && window.matchMedia("(max-width: 850px)").matches)
      detailsRef.current?.scrollIntoView({ block: "start" });
  }, [selection?.kind, selection?.id]);
  useEffect(() => {
    if (openRequest && window.matchMedia("(max-width: 850px)").matches)
      sourceRef.current?.scrollIntoView({ block: "start" });
  }, [openRequest]);
  const choose = (value: Selection) => {
    setOpenRequest(null);
    setLocalSelection(value);
    props.onSelect?.(value);
  };
  const finding =
    selection?.kind === "finding"
      ? model.findings.find((f) => f.id === selection.id)
      : undefined;
  const node =
    selection?.kind === "node"
      ? model.nodes.find((n) => n.id === selection.id)
      : undefined;
  const edge =
    selection?.kind === "edge"
      ? model.edges.find((e) => e.id === selection.id)
      : undefined;
  const selectedPolicy = edge?.policyRef ?? finding?.policyRef;
  const linkedFindings = node
    ? model.findings.filter((f) => f.nodeIds.includes(node.id))
    : [];
  const selectedEvidence =
    finding?.evidenceRefs ?? node?.evidenceRefs ?? edge?.evidenceRefs ?? [];
  const edges: Edge[] = model.edges.map((e) => ({
    id: e.id,
    source: e.fromId,
    target: e.toId,
    label: e.resolution === "resolved" ? e.kind : `${e.kind} · ${e.resolution}`,
    ariaLabel: `Связь ${e.kind}: ${e.resolution}`,
    selected: edge?.id === e.id,
    markerEnd: { type: MarkerType.ArrowClosed },
    style: {
      strokeDasharray: e.resolution !== "resolved" ? "6 5" : undefined,
      strokeWidth: edge?.id === e.id ? 3 : 2,
    },
    interactionWidth: 28,
  }));
  const open = (id: string) => {
    if (!model.evidence.some((e) => e.id === id)) return;
    const request = {
      workspaceId: model.scope.workspaceId,
      runId: model.scope.runId,
      snapshotId: model.scope.snapshotId,
      evidenceId: id,
    };
    setOpenRequest(request);
    props.onOpenEvidence?.(request);
  };
  const evidenceButtons = (ids: string[]) => (
    <div className="sm-evidence-list">
      {ids.map((id) => {
        const e = model.evidence.find((item) => item.id === id);
        return e ? (
          <button
            className="sm-evidence-button"
            key={id}
            onClick={() => open(id)}
            aria-pressed={openRequest?.evidenceId === id}
          >
            <span>{id}</span>
            <small>
              {e.location.path}:{e.location.line}–{e.location.endLine}
            </small>
          </button>
        ) : null;
      })}
    </div>
  );
  const commitLayout = (current: Node[], viewport = layout?.viewport) =>
    props.onLayoutChange?.({
      scope: model.scope,
      viewId: layout?.viewId ?? "security-map",
      nodePositions: Object.fromEntries(current.map((n) => [n.id, n.position])),
      ...(viewport ? { viewport } : {}),
    });
  const changes = (items: NodeChange<Node>[]) => {
    const next = applyNodeChanges(items, nodes);
    setNodes(next);
    if (items.some((item) => item.type === "position" && !item.dragging))
      commitLayout(next);
  };
  const activeSource =
    sourceState &&
    sourceState.status !== "idle" &&
    openRequest &&
    sameRequest(sourceState.request, openRequest)
      ? sourceState
      : null;
  return (
    <section className="security-map" aria-label="Карта безопасности">
      {manual && (
        <div className="sm-provenance" role="note">
          Ручной разбор исходника — не результат анализатора
        </div>
      )}
      <header className="sm-header">
        <div>
          <span className="sm-eyebrow">
            SECURITY MAP / {manual ? "SOURCE REVIEW" : "ANALYSIS"}
          </span>
          <h1>Путь доступа и данных</h1>
          <p>Кто получает доступ, на каком основании и к каким данным.</p>
        </div>
        <span className="sm-badge">
          {loadState === "partial" ||
          model.coverage.status !== "complete" ||
          unresolvedEdges.length > 0
            ? "Частичная картина"
            : "Данные загружены"}
        </span>
      </header>
      {unresolvedEdges.length > 0 && (
        <p className="sm-resolution-warning" role="status">
          Неизвестные или частичные связи: {unresolvedEdges.length}. Даже полная
          сводка покрытия не закрывает эти пробелы.
        </p>
      )}
      <div className="sm-scope">
        <span>workspace: {model.scope.workspaceId}</span>
        <span>run: {model.scope.runId}</span>
        <span>snapshot: {model.scope.snapshotId}</span>
      </div>
      <div className="sm-grid">
        <div className="sm-main">
          <div ref={canvasRef} className="sm-canvas" aria-label="Граф доступа">
            {loadState === "empty" || !nodes.length ? (
              <div className="sm-state">
                <h2>Для этого запуска нет поддержанных связей</h2>
                <p>
                  Пустая карта не означает, что проект безопасен. Смотрите
                  покрытие и диагностику.
                </p>
                <button onClick={onRetry}>Повторить загрузку</button>
              </div>
            ) : (
              <ReactFlow
                onInit={setFlow}
                nodes={nodes.map((n) => ({
                  ...n,
                  selected: node?.id === n.id,
                }))}
                edges={edges}
                onNodesChange={changes}
                onNodeClick={(_, n) => choose({ kind: "node", id: n.id })}
                onEdgeClick={(_, e) => choose({ kind: "edge", id: e.id })}
                onNodeDragStop={(_, __, dragged) => {
                  const next = nodes.map(
                    (node) => dragged.find((d) => d.id === node.id) ?? node,
                  );
                  setNodes(next);
                  commitLayout(next);
                }}
                onMoveEnd={(_, viewport) => commitLayout(nodes, viewport)}
                nodesConnectable={false}
                edgesReconnectable={false}
                deleteKeyCode={null}
                fitView={!layout?.viewport}
                defaultViewport={layout?.viewport}
                fitViewOptions={{ padding: 0.2 }}
                minZoom={0.25}
                maxZoom={1.5}
                proOptions={{ hideAttribution: false }}
              >
                <Background gap={22} color="#d3dce8" />
                <Controls showInteractive={false} />
              </ReactFlow>
            )}
          </div>
          <nav className="sm-path" aria-label="Связи пути">
            <h2>Проследить путь</h2>
            {model.edges.map((e, i) => (
              <button
                key={e.id}
                className={edge?.id === e.id ? "is-selected" : ""}
                onClick={() => choose({ kind: "edge", id: e.id })}
              >
                <b>{String(i + 1).padStart(2, "0")}</b>
                <span>
                  {e.kind}
                  <small>
                    {model.nodes.find((n) => n.id === e.fromId)?.label} →{" "}
                    {model.nodes.find((n) => n.id === e.toId)?.label}
                  </small>
                </span>
                <em>{resolutionText[e.resolution]}</em>
              </button>
            ))}
          </nav>
          <section className="sm-findings">
            <h2>
              Находки <span>{model.findings.length}</span>
            </h2>
            <p>
              Находка связана с узлами через links и не является вызовом кода.
            </p>
            {model.findings.map((f) => (
              <button
                key={f.id}
                onClick={() => choose({ kind: "finding", id: f.id })}
                className={finding?.id === f.id ? "is-selected" : ""}
              >
                <span className="sm-dot" />
                <span>
                  <strong>{f.label}</strong>
                  <small>
                    {f.status} · {f.severity} · уверенность {f.confidence}
                  </small>
                </span>
                <span>↗</span>
              </button>
            ))}
            {!model.findings.length && (
              <p>Связанных находок нет; это не оценка безопасности проекта.</p>
            )}
          </section>
          {model.diagnostics
            .filter((d) => d.nodeId || d.code.includes("UNKNOWN"))
            .map((d) => (
              <section className="sm-unknown" key={d.id}>
                <h2>
                  {d.code === "UNKNOWN_RUNTIME_GUARDS"
                    ? "Неизвестно: дополнительные guards"
                    : `Неизвестно: ${d.code}`}
                </h2>
                <p>{d.message}</p>
                {d.nodeId && (
                  <p>
                    У обработчика:{" "}
                    {model.nodes.find((n) => n.id === d.nodeId)?.label}
                  </p>
                )}
                <p>
                  Это пробел анализа. Связь с выдуманным обработчиком не рисуем.
                </p>
                {evidenceButtons(d.evidenceRefs)}
              </section>
            ))}
        </div>
        <aside
          ref={detailsRef}
          className="sm-details"
          aria-label="Детали выбранного элемента"
        >
          <span className="sm-eyebrow">DETAIL / EVIDENCE</span>
          <h2>
            {edge
              ? `Связь: ${edge.kind}`
              : (node?.label ?? finding?.label ?? "Выберите стрелку или узел")}
          </h2>
          {!selection && (
            <p>
              Нажмите на стрелку графа или выберите шаг пути ниже. Здесь
              появятся основание, producer и доказательства.
            </p>
          )}
          {edge && (
            <>
              <dl>
                <dt>Resolution</dt>
                <dd>
                  {resolutionText[edge.resolution]} ({edge.resolution})
                </dd>
                <dt>Основание</dt>
                <dd>
                  {edge.basis === "source"
                    ? "Исходный код (source)"
                    : "Предположение о политике (policy assumption)"}
                </dd>
                <dt>Producer</dt>
                <dd>{edge.producerVersion}</dd>
                <dt>Policy</dt>
                <dd>
                  {edge.policyRef ??
                    "Не используется этой связью. Предположения для Finding — ниже."}
                </dd>
              </dl>
              <p className={edge.resolution !== "resolved" ? "sm-unknown" : ""}>
                {edge.resolutionReason}
              </p>
            </>
          )}
          {node && (
            <>
              <p>{node.kind}</p>
              <code>{node.symbolKey}</code>
              <p>Producer: {node.producerVersion}</p>
              {linkedFindings.map((f) => (
                <button
                  key={f.id}
                  onClick={() => choose({ kind: "finding", id: f.id })}
                >
                  Связанная находка: {f.label}
                </button>
              ))}
            </>
          )}
          {finding && (
            <>
              <p>
                {finding.status} · {finding.severity} · {finding.confidence}
              </p>
              {finding.reasons.map((reason, i) => (
                <p key={i}>{reason}</p>
              ))}
              <h3>Связанные узлы</h3>
              <div className="sm-chips">
                {finding.nodeIds.map((id) => (
                  <button key={id} onClick={() => choose({ kind: "node", id })}>
                    {model.nodes.find((n) => n.id === id)?.label}
                  </button>
                ))}
              </div>
            </>
          )}
          {!!selectedEvidence.length && (
            <>
              <h3>Доказательства</h3>
              {evidenceButtons(selectedEvidence)}
            </>
          )}
          {evidence && (
            <section
              ref={sourceRef}
              className="sm-source"
              aria-label="Фрагмент доказательства"
            >
              <h3>
                {manual ? "Сохранённый фрагмент" : "Маскированный исходник"}
              </h3>
              <p>
                {evidence.location.path}:{evidence.location.line}:
                {evidence.location.column}–{evidence.location.endLine}:
                {evidence.location.endColumn}
              </p>
              <p>
                Bytes: {evidence.location.byteStart}–{evidence.location.byteEnd}
              </p>
              <p>
                SHA-256: <code>{evidence.sourceHash}</code>
              </p>
              <p>Producer: {evidence.producerVersion}</p>
              {manual ? (
                <>
                  <p>
                    Reviewed range · без добавленного контекста. Это сохранённый
                    фрагмент, не ответ API A.
                  </p>
                  <pre>{evidence.redactedSnippet}</pre>
                </>
              ) : activeSource?.status === "ready" ? (
                <>
                  <p>Redaction: {activeSource.redactionVersion}</p>
                  <pre>{activeSource.maskedFragment}</pre>
                </>
              ) : activeSource?.status === "error" ||
                activeSource?.status === "unavailable" ? (
                <>
                  <p role="alert">
                    Исходник недоступен (
                    {activeSource.code === "source_changed"
                      ? "source_changed"
                      : activeSource.code === "source_unavailable"
                        ? "source_unavailable"
                        : activeSource.code === "source_redaction_incomplete"
                          ? "source_redaction_incomplete"
                          : "evidence_not_available"}
                    ).
                  </p>
                  <button
                    onClick={() =>
                      openRequest &&
                      (props.onRetrySource ?? props.onOpenEvidence)?.(
                        openRequest,
                      )
                    }
                  >
                    Повторить запрос фрагмента
                  </button>
                </>
              ) : (
                <p role="status">
                  {activeSource?.status === "loading"
                    ? "Загрузка маскированного фрагмента…"
                    : "Ожидаем scoped masked-source от A…"}
                </p>
              )}
            </section>
          )}
          <section className="sm-policy">
            <h3>Политика и допущения</h3>
            {!model.policies.length && (
              <p>Политика предметной области не предоставлена.</p>
            )}
            {model.policies
              .filter((p) => !selectedPolicy || p.id === selectedPolicy)
              .map((p) => (
                <div key={p.id}>
                  <strong>
                    {p.status === "assumption"
                      ? "Предположение — не утверждено"
                      : "Утверждённая политика"}
                  </strong>
                  <p>{p.description}</p>
                  <p>
                    Версия: {p.version} · source: {p.source}
                  </p>
                  <code>{p.hash}</code>
                  {p.limitations.map((l, i) => (
                    <p key={i}>{l}</p>
                  ))}
                  {evidenceButtons(p.evidenceRefs)}
                </div>
              ))}
          </section>
        </aside>
      </div>
      <details className="sm-diagnostics">
        <summary>
          Покрытие и диагностика · {model.coverage.status} ·{" "}
          {model.diagnostics.length} сообщений
        </summary>
        <p>{model.coverage.summary}</p>
        {model.coverage.warnings.map((w, i) => (
          <p key={i}>{w}</p>
        ))}
        {model.diagnostics.map((d) => (
          <article key={d.id}>
            <h3>{d.code}</h3>
            <p>{d.message}</p>
            {evidenceButtons(d.evidenceRefs)}
          </article>
        ))}
      </details>
      <footer className="sm-footer">
        Layout меняет только расположение. Статический путь не доказывает
        эксплуатацию.
      </footer>
    </section>
  );
}
