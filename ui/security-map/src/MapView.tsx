import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  applyNodeChanges,
  type Edge,
  type Node,
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
import { NODE_WIDTH, computeMapLayout } from "./layout";
import { NodeIcon, nodeMeta, toneColor } from "./NodeIcon";
import { SecurityNode, type SecurityNodeData } from "./SecurityNode";
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
  resolved: "разрешена",
  partial: "частично",
  unknown: "неизвестно",
} as const;

const edgeKindText: Record<string, string> = {
  routes_to: "маршрут",
  matches: "совпадает",
  permits: "разрешает",
  requires: "требует",
  calls: "вызывает",
  flows_to: "передаёт",
  guards: "защищает",
  reads: "читает",
  writes: "пишет",
};

const edgeText = (edge: MapViewModel["edges"][number]) =>
  `${edgeKindText[edge.kind] ?? edge.kind}${
    edge.resolution === "resolved" ? "" : ` · ${resolutionText[edge.resolution]}`
  }`;

const sameRequest = (a: EvidenceRequest, b: EvidenceRequest) =>
  sameScope(a, b) && a.evidenceId === b.evidenceId;

const plural = (n: number, one: string, few: string, many: string) => {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
};

const DEFAULT_VIEWPORT = { x: 32, y: 88, zoom: 0.7 };

const positionFromLayout = (
  layout: SecurityMapLayout | undefined,
  computed: Record<string, { x: number; y: number }>,
  id: string,
) => layout?.nodePositions[id] ?? computed[id] ?? { x: 0, y: 0 };

const nodeFor = (
  item: MapViewModel["nodes"][number],
  position: { x: number; y: number },
): Node => {
  const meta = nodeMeta(item.kind);
  const data: SecurityNodeData = {
    kind: item.kind,
    label: item.label,
    symbolKey: item.symbolKey,
    evidenceCount: item.evidenceRefs.length,
    findingCount: 0,
    tone: meta.tone,
    unknown: false,
    neighbour: false,
    dimmed: false,
  };
  return {
    id: item.id,
    type: "securityNode",
    position,
    data,
    ariaLabel: `${meta.label}: ${item.label}`,
    style: { width: NODE_WIDTH },
  };
};

export function Failure({
  code,
  onRetry,
}: {
  code: string;
  onRetry: () => void;
}) {
  return (
    <div className="sm-state" role="alert">
      <div className="sm-state-icon">!</div>
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
  const detailsRef = useRef<HTMLElement>(null);
  const sourceRef = useRef<HTMLElement>(null);
  const [flow, setFlow] = useState<ReactFlowInstance | null>(null);
  const [railOpen, setRailOpen] = useState(false);
  const computed = useMemo(
    () => computeMapLayout(model.nodes, model.edges),
    [model.nodes, model.edges],
  );
  const [mapNodes, setMapNodes] = useState<Node[]>(() =>
    model.nodes.map((item) =>
      nodeFor(item, positionFromLayout(layout, computed, item.id)),
    ),
  );
  const [canvasSize, setCanvasSize] = useState({ width: 0, height: 0 });
  const [viewport, setViewport] = useState(layout?.viewport ?? DEFAULT_VIEWPORT);
  const nodeTypes = useMemo(() => ({ securityNode: SecurityNode }), []);
  const canvasKey = `${Math.round(canvasSize.width / 50)}x${Math.round(canvasSize.height / 50)}`;
  const [localSelection, setLocalSelection] = useState<Selection | null>(null);
  const [openRequest, setOpenRequest] = useState<EvidenceRequest | null>(null);

  useEffect(() => {
    setMapNodes(
      model.nodes.map((item) =>
        nodeFor(item, positionFromLayout(layout, computed, item.id)),
      ),
    );
  }, [layout?.viewId, JSON.stringify(layout?.nodePositions), model.revision, model.nodes.length, model.edges.length]);

  useEffect(() => {
    const element = canvasRef.current;
    if (!element) return;
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (!rect) return;
      setCanvasSize((current) =>
        Math.abs(current.width - rect.width) < 2 &&
        Math.abs(current.height - rect.height) < 2
          ? current
          : { width: rect.width, height: rect.height },
      );
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    setLocalSelection(null);
    setOpenRequest(null);
  }, [model.scope.workspaceId, model.scope.runId, model.scope.snapshotId]);

  const selection =
    props.selection === undefined
      ? localSelection
      : (props.selection?.value ?? null);
  const evidence =
    openRequest && model.evidence.find((item) => item.id === openRequest.evidenceId);
  const edge =
    selection?.kind === "edge"
      ? model.edges.find((item) => item.id === selection.id)
      : undefined;
  const node =
    selection?.kind === "node"
      ? model.nodes.find((item) => item.id === selection.id)
      : undefined;
  const finding =
    selection?.kind === "finding"
      ? model.findings.find((item) => item.id === selection.id)
      : undefined;

  useEffect(() => {
    setOpenRequest(null);
  }, [selection?.kind, selection?.id]);

  useEffect(() => {
    if (selection && window.matchMedia("(max-width: 1100px)").matches) {
      detailsRef.current?.scrollIntoView({ block: "start" });
    }
  }, [selection?.kind, selection?.id]);

  useEffect(() => {
    if (openRequest && window.matchMedia("(max-width: 1100px)").matches) {
      sourceRef.current?.scrollIntoView({ block: "start" });
    }
  }, [openRequest]);

  const choose = (value: Selection) => {
    setOpenRequest(null);
    setLocalSelection(value);
    props.onSelect?.(value);
    if (typeof window !== "undefined" && window.innerWidth < 1100) {
      setRailOpen(false);
    }
  };

  const clearSelection = () => {
    setLocalSelection(null);
    setOpenRequest(null);
  };

  const activeSource =
    sourceState &&
    sourceState.status !== "idle" &&
    openRequest &&
    sameRequest(sourceState.request, openRequest)
      ? sourceState
      : null;

  const open = (id: string) => {
    if (!model.evidence.some((item) => item.id === id)) return;
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
        const item = model.evidence.find((candidate) => candidate.id === id);
        return item ? (
          <button
            className="sm-evidence-button"
            key={id}
            onClick={() => open(id)}
            aria-pressed={openRequest?.evidenceId === id}
            title={item.id}
          >
            <span className="sm-evidence-path">
              {item.location.path}:{item.location.line}–{item.location.endLine}
            </span>
            <small>
              evidence {item.id.slice(0, 8)} · {item.producerVersion}
            </small>
          </button>
        ) : null;
      })}
    </div>
  );

  const selectedNodeIds = new Set<string>();
  if (node) selectedNodeIds.add(node.id);
  if (edge) {
    selectedNodeIds.add(edge.fromId);
    selectedNodeIds.add(edge.toId);
  }
  if (finding) finding.nodeIds.forEach((id) => selectedNodeIds.add(id));
  const adjacentIds = new Set(selectedNodeIds);
  for (const item of model.edges) {
    if (selectedNodeIds.has(item.fromId)) adjacentIds.add(item.toId);
    if (selectedNodeIds.has(item.toId)) adjacentIds.add(item.fromId);
  }
  const hasSelection = Boolean(selection);
  const unknownEdges = model.edges.filter((item) => item.resolution !== "resolved");
  const unknownNodeIds = new Set(
    unknownEdges.flatMap((item) => [item.fromId, item.toId]),
  );
  const connectedEdges = new Set(
    model.edges
      .filter(
        (item) =>
          selectedNodeIds.has(item.fromId) || selectedNodeIds.has(item.toId),
      )
      .map((item) => item.id),
  );
  if (edge) connectedEdges.add(edge.id);

  useEffect(() => {
    setMapNodes((current) =>
      current.map((stored) => {
        const item = model.nodes.find((candidate) => candidate.id === stored.id);
        if (!item) return stored;
        const data = stored.data as unknown as SecurityNodeData;
        return {
          ...stored,
          selected: node?.id === item.id,
          data: {
            ...data,
            findingCount: model.findings.filter((candidate) =>
              candidate.nodeIds.includes(item.id),
            ).length,
            unknown: unknownNodeIds.has(item.id),
            neighbour: adjacentIds.has(item.id) && node?.id !== item.id,
            dimmed: hasSelection && !adjacentIds.has(item.id),
          },
        };
      }),
    );
  }, [
    selection?.kind,
    selection?.id,
    model.revision,
    model.nodes.length,
    model.edges.length,
    model.findings.length,
    layout?.viewId,
    JSON.stringify(layout?.nodePositions),
  ]);

  const flowEdges: Edge[] = model.edges.map((item) => {
    const selected = edge?.id === item.id;
    const highlighted = selected || (!hasSelection ? false : connectedEdges.has(item.id));
    const dimmed = hasSelection && !highlighted;
    const colour =
      item.resolution === "resolved"
        ? selected
          ? "#c4b5fd"
          : "#64748b"
        : item.resolution === "partial"
          ? "#fbbf24"
          : "#f59e0b";
    return {
      id: item.id,
      source: item.fromId,
      target: item.toId,
      label: edgeText(item),
      ariaLabel: `Связь ${edgeText(item)}`,
      selected,
      markerEnd: { type: MarkerType.ArrowClosed, color: colour },
      style: {
        stroke: colour,
        strokeWidth: selected ? 3.2 : 1.7,
        strokeDasharray: item.resolution === "resolved" ? undefined : "7 6",
        opacity: dimmed ? 0.18 : 1,
      },
      labelStyle: {
        fill: item.resolution === "resolved" ? "#cbd5e1" : "#fcd34d",
        fontSize: 10,
        fontWeight: selected ? 700 : 550,
        textDecoration: selected ? "underline" : "none",
      },
      labelBgStyle: {
        fill: "#0b1220",
        fillOpacity: 0.94,
        stroke: item.resolution === "resolved" ? "#334155" : "#92400e",
      },
      labelBgPadding: [6, 4],
      labelBgBorderRadius: 4,
      interactionWidth: 32,
      className: `sm-edge sm-edge--${item.resolution}${selected ? " is-selected" : ""}`,
    };
  });

  const commitLayout = (
    currentNodes: Node[],
    viewport: { x: number; y: number; zoom: number } | undefined =
      layout?.viewport,
  ) => {
    const nodePositions = Object.fromEntries(
      currentNodes.map((item) => [item.id, item.position]),
    );
    props.onLayoutChange?.({
      scope: model.scope,
      viewId: layout?.viewId ?? "security-map",
      nodePositions,
      ...(viewport ? { viewport } : {}),
    });
  };

  const onNodesChange = (changes: NodeChange<Node>[]) => {
    const next = applyNodeChanges(changes, mapNodes);
    setMapNodes(next);
    if (changes.some((item) => item.type === "position" && !item.dragging)) {
      commitLayout(next);
    }
  };

  const fit = () => {
    void flow?.fitView({ padding: 0.16, minZoom: 0.12 });
  };

  const reset = () => {
    const nextPositions = computeMapLayout(model.nodes, model.edges);
    const next = model.nodes.map((item) =>
      nodeFor(item, nextPositions[item.id] ?? { x: 0, y: 0 }),
    );
    setMapNodes(next);
    props.onLayoutChange?.({
      scope: model.scope,
      viewId: layout?.viewId ?? "security-map",
      nodePositions: nextPositions,
    });
    requestAnimationFrame(() => {
      void flow?.setViewport(DEFAULT_VIEWPORT);
    });
  };

  const findingCountByNode = (id: string) =>
    model.findings.filter((item) => item.nodeIds.includes(id)).length;

  return (
    <section className="security-map" aria-label="Карта безопасности">
      <header className="sm-topbar">
        <div className="sm-brand">
          <span className="sm-brand-icon">
            <NodeIcon kind="route" size={19} />
          </span>
          <div>
            <span className="sm-eyebrow">SECURITY MAP / SOURCE PATTERN</span>
            <h1>Карта доступа и данных</h1>
          </div>
        </div>
        <div className="sm-counts" aria-label="Счётчики карты">
          <span><b>{model.nodes.length}</b> узлов</span>
          <span><b>{model.edges.length}</b> связей</span>
          <span><b>{model.findings.length}</b> находок</span>
          <span><b>{model.evidence.length}</b> evidence</span>
        </div>
        <div className="sm-actions">
          <span
            className={`sm-health ${unknownEdges.length ? "is-warn" : model.coverage.status === "complete" ? "is-ok" : "is-partial"}`}
          >
            {unknownEdges.length
              ? `${unknownEdges.length} ${plural(unknownEdges.length, "неизвестная связь", "неизвестные связи", "неизвестных связей")}`
              : model.coverage.status === "complete"
                ? "полная картина"
                : "частичная картина"}
          </span>
          <button
            className={`sm-tool ${railOpen ? "is-active" : ""}`}
            onClick={() => setRailOpen((value) => !value)}
            title="Путь и находки"
          >
            ◫ <span>Путь</span>
          </button>
          <button className="sm-tool" onClick={fit} title="Вписать карту">
            ⛶
          </button>
          <button className="sm-tool" onClick={reset} title="Сбросить layout">
            ↺
          </button>
          <button className="sm-tool" onClick={onRetry} title="Обновить карту">
            ⟳
          </button>
        </div>
      </header>

      {unknownEdges.length > 0 && (
        <button
          className="sm-unknown-banner"
          onClick={() => choose({ kind: "edge", id: unknownEdges[0].id })}
        >
          <span>Неизвестные или частичные связи: {unknownEdges.length}</span>
          <small>Пунктир и amber — это пробел анализа, а не безопасное состояние. Открыть первую связь →</small>
        </button>
      )}

      {model.provenance === "manual_source_review" && (
        <div className="sm-provenance" role="note">
          Ручной разбор исходника — не результат анализатора
        </div>
      )}

      <div className={`sm-workspace ${railOpen ? "has-rail" : ""}`}>
        <aside className={`sm-rail ${railOpen ? "is-open" : ""}`} aria-label="Путь и находки">
          <div className="sm-rail-head">
            <span>Путь и факты</span>
            <button onClick={() => setRailOpen(false)} aria-label="Закрыть список">×</button>
          </div>
          <section className="sm-rail-section">
            <h2>Маршрут <span>{model.edges.length}</span></h2>
            {model.edges.map((item, index) => (
              <button
                key={item.id}
                className={`sm-rail-row ${edge?.id === item.id ? "is-selected" : ""}`}
                onClick={() => choose({ kind: "edge", id: item.id })}
              >
                <b>{String(index + 1).padStart(2, "0")}</b>
                <span>
                  <strong>{edgeText(item)}</strong>
                  <small>
                    {model.nodes.find((candidate) => candidate.id === item.fromId)?.label} →{" "}
                    {model.nodes.find((candidate) => candidate.id === item.toId)?.label}
                  </small>
                </span>
                <em className={`sm-resolution sm-resolution--${item.resolution}`}>
                  {resolutionText[item.resolution]}
                </em>
              </button>
            ))}
          </section>
          <section className="sm-rail-section">
            <h2>Находки <span>{model.findings.length}</span></h2>
            {model.findings.map((item) => (
              <button
                key={item.id}
                className={`sm-rail-row sm-finding-row ${finding?.id === item.id ? "is-selected" : ""}`}
                onClick={() => choose({ kind: "finding", id: item.id })}
              >
                <span className={`sm-severity sm-severity--${item.severity}`} />
                <span>
                  <strong>{item.label}</strong>
                  <small>
                    {item.status} · {item.severity} · {item.confidence}
                  </small>
                </span>
              </button>
            ))}
            {!model.findings.length && (
              <p className="sm-rail-empty">Связанных находок нет; это не оценка безопасности проекта.</p>
            )}
          </section>
          <section className="sm-rail-section">
            <h2>Пробелы <span>{model.diagnostics.length}</span></h2>
            <p className="sm-rail-empty">{model.coverage.summary}</p>
            {model.coverage.warnings.slice(0, 5).map((warning, index) => (
              <p className="sm-rail-warning" key={index}>{warning}</p>
            ))}
          </section>
        </aside>

        <div className="sm-canvas-wrap">
          <div ref={canvasRef} className="sm-canvas" aria-label="Граф доступа">
            {loadState === "empty" || !model.nodes.length ? (
              <div className="sm-empty">
                <h2>Для этого запуска нет поддержанных связей</h2>
                <p>Пустая карта не означает, что проект безопасен. Смотрите покрытие и диагностику.</p>
                <button onClick={onRetry}>Повторить загрузку</button>
              </div>
            ) : (
              <ReactFlow
                onInit={setFlow}
                nodes={mapNodes}
                edges={flowEdges}
                nodeTypes={nodeTypes}
                onNodesChange={onNodesChange}
                onNodeClick={(_, item) => choose({ kind: "node", id: item.id })}
                onEdgeClick={(_, item) => choose({ kind: "edge", id: item.id })}
                onPaneClick={clearSelection}
                onNodeDragStop={(_, item) => {
                  const next = mapNodes.map((candidate) =>
                    candidate.id === item.id
                      ? { ...candidate, position: item.position }
                      : candidate,
                  );
                  setMapNodes(next);
                  commitLayout(next);
                }}
                onMoveEnd={(_, nextViewport) => {
                  setViewport(nextViewport);
                  commitLayout(mapNodes, nextViewport);
                }}
                nodesConnectable={false}
                edgesReconnectable={false}
                deleteKeyCode={null}
                key={canvasKey}
                fitView={false}
                defaultViewport={layout?.viewport ?? viewport}
                minZoom={0.18}
                maxZoom={1.6}
                panOnScroll
                selectionOnDrag
                proOptions={{ hideAttribution: false }}
              >
                <Background
                  variant={BackgroundVariant.Dots}
                  gap={20}
                  size={1.2}
                  color="#334155"
                />
                <Controls className="sm-controls" showInteractive={false} />
                <MiniMap
                  className="sm-minimap"
                  nodeColor={(item) => {
                    const data = item.data as unknown as SecurityNodeData;
                    return toneColor[data.tone] ?? "#64748b";
                  }}
                  maskColor="rgba(2, 6, 23, 0.82)"
                  pannable
                  zoomable
                />
              </ReactFlow>
            )}
          </div>
        </div>

        <aside ref={detailsRef} className="sm-details" aria-label="Детали выбранного элемента">
          <div className="sm-details-head">
            <span className="sm-eyebrow">DETAIL / EVIDENCE</span>
            <span className="sm-details-count">
              {model.evidence.length} evidence · {model.findings.length} findings
            </span>
          </div>
          <h2>
            {edge
              ? `Связь: ${edgeText(edge)}`
              : (node
                  ? `${nodeMeta(node.kind).label}: ${node.label}`
                  : (finding
                      ? finding.label
                      : "Выберите узел, стрелку или находку"))}
          </h2>
          {!selection && (
            <p className="sm-muted">
              Слева — фактический маршрут. Нажмите узел или стрелку: здесь появятся
              основание, producer и evidence. Layout меняет только расположение.
            </p>
          )}
          {edge && (
            <>
              <div className="sm-detail-pills">
                <span className={`sm-status sm-status--${edge.resolution}`}>{resolutionText[edge.resolution]}</span>
                <span className="sm-pill">{edge.kind}</span>
                <span className="sm-pill">{edge.basis === "source" ? "source" : "policy assumption"}</span>
              </div>
              <p className={edge.resolution === "resolved" ? "" : "sm-amber-text"}>{edge.resolutionReason}</p>
              <h3>Доказательства связи</h3>
              {evidenceButtons(edge.evidenceRefs)}
              <details className="sm-tech">
                <summary>Технические данные</summary>
                <dl>
                  <dt>Producer</dt><dd>{edge.producerVersion}</dd>
                  <dt>Resolution</dt><dd>{edge.resolution}</dd>
                  <dt>Policy</dt><dd>{edge.policyRef ?? "не используется"}</dd>
                  <dt>Edge ID</dt><dd className="sm-mono">{edge.id}</dd>
                </dl>
              </details>
            </>
          )}
          {node && (
            <>
              <div className="sm-detail-pills">
                <span className={`sm-status sm-status--${nodeMeta(node.kind).tone}`}>{nodeMeta(node.kind).label}</span>
                <span className="sm-pill">{node.evidenceRefs.length} evidence</span>
                <span className="sm-pill">{findingCountByNode(node.id)} findings</span>
              </div>
              <p className="sm-node-symbol-detail">{node.symbolKey}</p>
              <h3>Связанные находки</h3>
              {model.findings.filter((item) => item.nodeIds.includes(node.id)).map((item) => (
                <button className="sm-link-button" key={item.id} onClick={() => choose({ kind: "finding", id: item.id })}>
                  {item.label}
                </button>
              ))}
              <h3>Доказательства узла</h3>
              {evidenceButtons(node.evidenceRefs)}
              <details className="sm-tech">
                <summary>Технические данные</summary>
                <dl>
                  <dt>Kind</dt><dd>{node.kind}</dd>
                  <dt>Producer</dt><dd>{node.producerVersion}</dd>
                  <dt>Node ID</dt><dd className="sm-mono">{node.id}</dd>
                </dl>
              </details>
            </>
          )}
          {finding && (
            <>
              <div className="sm-detail-pills">
                <span className={`sm-status sm-status--${finding.status}`}>{finding.status}</span>
                <span className={`sm-status sm-status--${finding.severity}`}>{finding.severity}</span>
                <span className="sm-pill">confidence {finding.confidence}</span>
              </div>
              {finding.reasons.map((reason, index) => <p key={index}>{reason}</p>)}
              <h3>Связанные узлы</h3>
              <div className="sm-chips">
                {finding.nodeIds.map((id) => (
                  <button key={id} onClick={() => choose({ kind: "node", id })}>
                    {model.nodes.find((item) => item.id === id)?.label ?? id.slice(0, 8)}
                  </button>
                ))}
              </div>
              <h3>Доказательства находки</h3>
              {evidenceButtons(finding.evidenceRefs)}
            </>
          )}
          {evidence && (
            <section ref={sourceRef} className="sm-source" aria-label="Фрагмент доказательства">
              <div className="sm-source-head">
                <h3>{model.provenance === "manual_source_review" ? "Сохранённый фрагмент" : "Маскированный исходник"}</h3>
                <span className="sm-source-state">
                  {model.provenance === "manual_source_review"
                    ? "reviewed"
                    : activeSource?.status ?? "ожидает"}
                </span>
              </div>
              <p className="sm-source-path">
                {evidence.location.path}:{evidence.location.line}:{evidence.location.column}–
                {evidence.location.endLine}:{evidence.location.endColumn}
              </p>
              {model.provenance === "manual_source_review" ? (
                <>
                  <p>Reviewed range · без добавленного контекста.</p>
                  <pre>{evidence.redactedSnippet}</pre>
                </>
              ) : activeSource?.status === "ready" ? (
                <>
                  <p>Redaction: {activeSource.redactionVersion}</p>
                  <pre>{activeSource.maskedFragment}</pre>
                </>
              ) : activeSource?.status === "error" || activeSource?.status === "unavailable" ? (
                <>
                  <p role="alert" className="sm-amber-text">
                    Исходник недоступен ({activeSource.code}).
                  </p>
                  <button
                    onClick={() =>
                      openRequest &&
                      (props.onRetrySource ?? props.onOpenEvidence)?.(openRequest)
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
              <details className="sm-tech">
                <summary>Технические данные</summary>
                <dl>
                  <dt>Source hash</dt><dd className="sm-mono">{evidence.sourceHash}</dd>
                  <dt>Bytes</dt><dd className="sm-mono">{evidence.location.byteStart}–{evidence.location.byteEnd}</dd>
                  <dt>Producer</dt><dd>{evidence.producerVersion}</dd>
                  <dt>Evidence ID</dt><dd className="sm-mono">{evidence.id}</dd>
                </dl>
              </details>
            </section>
          )}
          <details className="sm-tech">
            <summary>Scope и подпись</summary>
            <dl>
              <dt>Workspace</dt><dd className="sm-mono">{model.scope.workspaceId}</dd>
              <dt>Run</dt><dd className="sm-mono">{model.scope.runId}</dd>
              <dt>Snapshot</dt><dd className="sm-mono">{model.scope.snapshotId}</dd>
              <dt>Revision</dt><dd className="sm-mono">{model.revision}</dd>
            </dl>
          </details>
          <section className="sm-coverage">
            <div className="sm-coverage-head">
              <h3>Покрытие</h3>
              <span className={`sm-status sm-status--${model.coverage.status === "complete" ? "checked" : "partial"}`}>
                {model.coverage.status}
              </span>
            </div>
            <p>{model.coverage.summary}</p>
            {model.coverage.warnings.map((warning, index) => (
              <p className="sm-warning-text" key={index}>{warning}</p>
            ))}
            {model.diagnostics.length > 0 && (
              <details className="sm-tech">
                <summary>Диагностика · {model.diagnostics.length}</summary>
                {model.diagnostics.map((item) => (
                  <article key={item.id}>
                    <strong>{item.code}</strong>
                    <p>{item.message}</p>
                    {evidenceButtons(item.evidenceRefs)}
                  </article>
                ))}
              </details>
            )}
          </section>
          <section className="sm-policy">
            <h3>Политика и допущения</h3>
            {!model.policies.length && <p className="sm-muted">Политика предметной области не предоставлена.</p>}
            {model.policies.map((policy) => (
              <details key={policy.id} className="sm-tech">
                <summary>
                  {policy.status === "assumption" ? "Предположение — не утверждено" : "Утверждённая политика"}
                </summary>
                <p>{policy.description}</p>
                <p>Версия: {policy.version} · source: {policy.source}</p>
                <p className="sm-mono">{policy.hash}</p>
                {policy.limitations.map((limitation, index) => <p key={index}>{limitation}</p>)}
              </details>
            ))}
          </section>
        </aside>
      </div>
    </section>
  );
}