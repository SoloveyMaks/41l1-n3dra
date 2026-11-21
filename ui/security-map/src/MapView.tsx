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
import { NODE_WIDTH, computeMapLayout, initialFocusNodeIds } from "./layout";
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
  resolved: "подтверждена",
  partial: "частично подтверждена",
  unknown: "связь не установлена",
} as const;

const severityText: Record<string, string> = {
  critical: "критично",
  high: "высокий риск",
  medium: "средний риск",
  low: "низкий риск",
  info: "информация",
};

const statusText: Record<string, string> = {
  confirmed: "подтверждено",
  inconclusive: "не подтверждено",
  open: "открыто",
  dismissed: "отклонено",
};

const confidenceText: Record<string, string> = {
  high: "высокая уверенность",
  medium: "средняя уверенность",
  low: "низкая уверенность",
};

const coverageText: Record<string, string> = {
  complete: "Проверенный объём завершён",
  partial: "Проверено неполностью",
  unknown: "Покрытие неизвестно",
};

const basisText: Record<string, string> = {
  source: "по исходнику",
  policy_assumption: "Предположение, нужно подтверждение",
};

const sourceStatusText: Record<string, string> = {
  idle: "ожидает",
  loading: "загружаем",
  ready: "загружен",
  error: "недоступен",
  unavailable: "недоступен",
};

const edgeKindText: Record<string, string> = {
  routes_to: "Обращается",
  matches: "Подходит под правило",
  permits: "Разрешает",
  requires: "Требует",
  calls: "Вызывает",
  flows_to: "Передаёт данные",
  guards: "Проверяет",
  reads: "Читает",
  writes: "Изменяет",
};

const humanCoverageWarning = (warning: string): string => {
  if (warning === "runtime-configuration")
    return "Не проверена конфигурация при запуске: переменные окружения и внешние фильтры.";
  if (warning === "runtime-guards")
    return "Не проверены проверки доступа, которые включаются во время работы.";
  if (warning === "binaries-and-archives")
    return "Не проверялись бинарные файлы и архивы.";
  return warning;
};

const diagnosticText = (code: string, message: string): string => {
  if (code === "CF-STATIC-ONLY")
    return "Проверялся только исходный код: программа не запускалась, настройки среды и внешние фильтры не проверялись.";
  return message;
};

const edgeText = (edge: MapViewModel["edges"][number]) =>
  `${edgeKindText[edge.kind] ?? "Связь"}${
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
  const [miniMapOpen, setMiniMapOpen] = useState(
    () => typeof window !== "undefined" && window.innerWidth >= 900,
  );
  const miniMapTouched = useRef(false);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 900px)");
    const sync = () => {
      if (!miniMapTouched.current) setMiniMapOpen(!media.matches);
    };
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, []);
  const toggleMiniMap = () => {
    miniMapTouched.current = true;
    setMiniMapOpen((value) => !value);
  };
  const focusIds = useMemo(
    () => initialFocusNodeIds(model.nodes, model.edges, model.findings),
    [model.nodes, model.edges, model.findings],
  );
  const readableFocusIds = useMemo(() => {
    const byId = new Map(model.nodes.map((node) => [node.id, node]));
    const routePath = focusIds.filter((id) =>
      ["route", "guard", "function", "sink"].includes(byId.get(id)?.kind ?? ""),
    );
    if (routePath.length >= 2) return routePath;
    const dataPath = focusIds.filter((id) =>
      ["function", "sink", "data"].includes(byId.get(id)?.kind ?? ""),
    );
    return dataPath.length >= 2 ? dataPath : focusIds.filter((id) => byId.get(id)?.kind !== "actor");
  }, [focusIds, model.nodes]);
  const narrowFocusIds = useMemo(() => {
    const byId = new Map(model.nodes.map((node) => [node.id, node]));
    const route = focusIds.find((id) => byId.get(id)?.kind === "route");
    return route ? [route] : readableFocusIds.slice(0, 1);
  }, [focusIds, readableFocusIds, model.nodes]);
  const nodesMeasured =
    mapNodes.length > 0 &&
    mapNodes.every((node) => Boolean(node.measured?.width && node.measured?.height));
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
          >
            <span className="sm-evidence-path">
              {item.location.path}:{item.location.line}–{item.location.endLine}
            </span>
            <small>Фрагмент кода</small>
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

  const focusViewport = (
    ids: string[],
    minZoom = 0.34,
    maxZoom = 0.9,
  ) => {
    if (!flow || !ids.length) return;
    const positions = layout?.nodePositions ?? computeMapLayout(model.nodes, model.edges);
    const selected = ids.flatMap((id) => {
      const position = positions[id];
      if (!position) return [];
      const stored = mapNodes.find((node) => node.id === id);
      return [{ position, measured: stored?.measured }];
    });
    if (!selected.length) return;
    const pad = 24;
    const minX = Math.min(...selected.map((node) => node.position.x));
    const minY = Math.min(...selected.map((node) => node.position.y));
    const maxX = Math.max(
      ...selected.map((node) => node.position.x + (node.measured?.width ?? NODE_WIDTH)),
    );
    const maxY = Math.max(
      ...selected.map((node) => node.position.y + (node.measured?.height ?? 232)),
    );
    const width = Math.max(1, maxX - minX);
    const height = Math.max(1, maxY - minY);
    const canvasWidth = canvasSize.width || canvasRef.current?.clientWidth || 0;
    const canvasHeight = canvasSize.height || canvasRef.current?.clientHeight || 0;
    if (!canvasWidth || !canvasHeight) return;
    const zoom = Math.max(
      minZoom,
      Math.min(
        maxZoom,
        Math.min((canvasWidth - 2 * pad) / width, (canvasHeight - 2 * pad) / height),
      ),
    );
    void flow.setViewport({
      x: pad - minX * zoom,
      y: pad - minY * zoom,
      zoom,
    });
  };

  const fitAll = () => {
    void flow?.fitView({ padding: 0.1, minZoom: 0.06, maxZoom: 1.2, duration: 260 });
  };

  const focusSelection = () => {
    const ids = new Set(selectedNodeIds);
    if (edge) {
      ids.add(edge.fromId);
      ids.add(edge.toId);
    }
    if (finding) finding.nodeIds.forEach((id) => ids.add(id));
    if (ids.size) {
      focusViewport([...ids], 0.55, 1.15);
    } else {
      const narrow = canvasSize.width > 0 && canvasSize.width < 600;
      focusViewport(narrow && narrowFocusIds.length ? narrowFocusIds : readableFocusIds.length ? readableFocusIds : focusIds);
    }
  };

  useEffect(() => {
    if (!flow || !nodesMeasured || !focusIds.length) return;
    const narrow = canvasSize.width > 0 && canvasSize.width < 600;
    const ids = narrow && narrowFocusIds.length
      ? narrowFocusIds
      : readableFocusIds.length
        ? readableFocusIds
        : focusIds;
    const timer = window.setTimeout(() => focusViewport(ids), 60);
    return () => window.clearTimeout(timer);
  }, [flow, nodesMeasured, model.revision, focusIds.join("|"), readableFocusIds.join("|"), narrowFocusIds.join("|"), canvasKey, canvasSize.width, canvasSize.height]);

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
      const narrow = canvasSize.width > 0 && canvasSize.width < 600;
      focusViewport(narrow && narrowFocusIds.length ? narrowFocusIds : readableFocusIds.length ? readableFocusIds : focusIds);
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
            <span className="sm-eyebrow">КАРТА ДОСТУПА / СТАТИЧЕСКИЙ АНАЛИЗ</span>
            <h1>Карта доступа и данных</h1>
          </div>
        </div>
        <div className="sm-counts" aria-label="Счётчики карты">
          <span><b>{model.nodes.length}</b> узлов</span>
          <span><b>{model.edges.length}</b> связей</span>
          <span>
            <b>{model.findings.length}</b>{" "}
            {plural(model.findings.length, "проблема", "проблемы", "проблем")}
          </span>
          <span>
            <b>{model.evidence.length}</b>{" "}
            {plural(model.evidence.length, "доказательство", "доказательства", "доказательств")}
          </span>
        </div>
        <div className="sm-actions">
          <span
            className={`sm-health ${unknownEdges.length ? "is-warn" : model.coverage.status === "complete" ? "is-ok" : "is-partial"}`}
          >
            {unknownEdges.length
              ? `${unknownEdges.length} ${plural(unknownEdges.length, "связь не установлена", "связи не установлены", "связей не установлено")}`
              : coverageText[model.coverage.status] ?? "Проверено неполностью"}
          </span>
          <button
            className={`sm-tool ${railOpen ? "is-active" : ""}`}
            onClick={() => setRailOpen((value) => !value)}
            title="Путь и проблемы"
          >
            ◫ <span>Путь</span>
          </button>
          <button className="sm-tool" onClick={fitAll} title="Вся карта">
            ⛶ <span>Вся карта</span>
          </button>
          <button className="sm-tool" onClick={focusSelection} title="Показать путь">
            ◎ <span>Показать путь</span>
          </button>
          <button
            className={`sm-tool ${miniMapOpen ? "is-active" : ""}`}
            onClick={toggleMiniMap}
            title="Мини-карта"
          >
            ▣ <span>Мини</span>
          </button>
          <button className="sm-tool" onClick={reset} title="Сбросить расположение">
            ↺
          </button>
          <button className="sm-tool" onClick={onRetry} title="Обновить карту">
            ⟳
          </button>
        </div>
      </header>

      <div className="sm-intro">
        <span>Откуда приходит запрос, как проверяется доступ и какие данные затрагиваются.</span>
        <span>Нажмите на стрелку, чтобы увидеть основание.</span>
      </div>

      {unknownEdges.length > 0 && (
        <button
          className="sm-unknown-banner"
          onClick={() => choose({ kind: "edge", id: unknownEdges[0].id })}
        >
          <span>
            {unknownEdges.length}{" "}
            {plural(unknownEdges.length, "связь не установлена", "связи не установлены", "связей не установлено")}
          </span>
          <small>Жёлтый пунктир — это пробел анализа, а не безопасное состояние. Открыть первую связь →</small>
        </button>
      )}

      {model.provenance === "manual_source_review" && (
        <div className="sm-provenance" role="note">
          Ручной разбор исходника — не результат анализатора
        </div>
      )}

      <div className={`sm-workspace ${railOpen ? "has-rail" : ""}`}>
        <aside className={`sm-rail ${railOpen ? "is-open" : ""}`} aria-label="Путь и проблемы">
          <div className="sm-rail-head">
            <span>Путь и проблемы</span>
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
            <h2>Проблемы <span>{model.findings.length}</span></h2>
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
                    {statusText[item.status] ?? item.status} ·{" "}
                    {severityText[item.severity] ?? item.severity} ·{" "}
                    {confidenceText[item.confidence] ?? item.confidence}
                  </small>
                </span>
              </button>
            ))}
            {!model.findings.length && (
              <p className="sm-rail-empty">Проблем не найдено; это не оценка безопасности проекта.</p>
            )}
          </section>
          <section className="sm-rail-section">
            <h2>Пробелы <span>{model.diagnostics.length}</span></h2>
            <p className="sm-rail-empty">{model.coverage.summary}</p>
            {model.coverage.warnings.slice(0, 5).map((warning, index) => (
              <p className="sm-rail-warning" key={index}>{humanCoverageWarning(warning)}</p>
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
                {miniMapOpen && (
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
                )}
              </ReactFlow>
            )}
          </div>
        </div>

        <aside ref={detailsRef} className="sm-details" aria-label="Детали выбранного элемента">
          <div className="sm-details-head">
            <span className="sm-eyebrow">ДЕТАЛИ / ДОКАЗАТЕЛЬСТВА</span>
            <span className="sm-details-count">
              {model.evidence.length}{" "}
              {plural(model.evidence.length, "доказательство", "доказательства", "доказательств")} ·{" "}
              {model.findings.length}{" "}
              {plural(model.findings.length, "проблема", "проблемы", "проблем")}
            </span>
          </div>
          <h2>
            {edge
              ? `Связь: ${edgeText(edge)}`
              : (node
                  ? `${nodeMeta(node.kind).label}: ${node.label}`
                  : (finding
                      ? finding.label
                      : "Выберите узел, стрелку или проблему"))}
          </h2>
          {!selection && (
            <p className="sm-muted">
              Слева — фактический маршрут. Нажмите узел или стрелку: здесь появятся
              основание и фрагменты кода. Расположение блоков можно менять.
            </p>
          )}
          {edge && (
            <>
              <div className="sm-detail-pills">
                <span className={`sm-status sm-status--${edge.resolution}`}>{resolutionText[edge.resolution]}</span>
                <span className="sm-pill">{edgeKindText[edge.kind] ?? "Связь"}</span>
                <span className="sm-pill">{basisText[edge.basis] ?? edge.basis}</span>
              </div>
              <p className={edge.resolution === "resolved" ? "" : "sm-amber-text"}>{edge.resolutionReason}</p>
              <h3>Доказательства связи</h3>
              {evidenceButtons(edge.evidenceRefs)}
              <details className="sm-tech">
                <summary>Технические данные</summary>
                <dl>
                  <dt>Источник анализа</dt><dd>{edge.producerVersion}</dd>
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
                <span className="sm-pill">
                  {node.evidenceRefs.length}{" "}
                  {plural(node.evidenceRefs.length, "доказательство", "доказательства", "доказательств")}
                </span>
                <span className="sm-pill">
                  {findingCountByNode(node.id)}{" "}
                  {plural(findingCountByNode(node.id), "проблема", "проблемы", "проблем")}
                </span>
              </div>
              <p className="sm-node-hint">{nodeMeta(node.kind).hint}</p>
              <h3>Связанные проблемы</h3>
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
                  <dt>Тип</dt><dd>{node.kind}</dd>
                  <dt>Символ</dt><dd className="sm-mono">{node.symbolKey}</dd>
                  <dt>Источник анализа</dt><dd>{node.producerVersion}</dd>
                  <dt>ID элемента</dt><dd className="sm-mono">{node.id}</dd>
                </dl>
              </details>
            </>
          )}
          {finding && (
            <>
              <div className="sm-detail-pills">
                <span className={`sm-status sm-status--${finding.status}`}>{statusText[finding.status] ?? finding.status}</span>
                <span className={`sm-status sm-status--${finding.severity}`}>{severityText[finding.severity] ?? finding.severity}</span>
                <span className="sm-pill">{confidenceText[finding.confidence] ?? finding.confidence}</span>
              </div>
              <h3>Почему так</h3>
              {finding.reasons.map((reason, index) => <p key={index}>{reason}</p>)}
              <h3>Связанные узлы</h3>
              <div className="sm-chips">
                {finding.nodeIds.map((id) => (
                  <button key={id} onClick={() => choose({ kind: "node", id })}>
                    {model.nodes.find((item) => item.id === id)?.label ?? "Элемент карты"}
                  </button>
                ))}
              </div>
              <h3>Доказательства проблемы</h3>
              {evidenceButtons(finding.evidenceRefs)}
            </>
          )}
          {evidence && (
            <section ref={sourceRef} className="sm-source" aria-label="Фрагмент доказательства">
              <div className="sm-source-head">
                <h3>{model.provenance === "manual_source_review" ? "Сохранённый фрагмент" : "Фрагмент кода"}</h3>
                <span className="sm-source-state">
                  {model.provenance === "manual_source_review"
                    ? "проверен"
                    : sourceStatusText[activeSource?.status ?? "idle"] ?? "ожидает"}
                </span>
              </div>
              <p className="sm-source-path">
                {evidence.location.path}:{evidence.location.line}:{evidence.location.column}–
                {evidence.location.endLine}:{evidence.location.endColumn}
              </p>
              {model.provenance === "manual_source_review" ? (
                <>
                  <p>Проверенный фрагмент без добавленного контекста.</p>
                  <pre>{evidence.redactedSnippet}</pre>
                </>
              ) : activeSource?.status === "ready" ? (
                <>
                  <p>Версия маскирования: {activeSource.redactionVersion}</p>
                  <pre>{activeSource.maskedFragment}</pre>
                </>
              ) : activeSource?.status === "error" || activeSource?.status === "unavailable" ? (
                <>
                  <p role="alert" className="sm-amber-text">
                    Фрагмент кода недоступен.
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
                    ? "Загружаем фрагмент…"
                    : "Загружаем фрагмент исходника…"}
                </p>
              )}
              <details className="sm-tech">
                <summary>Технические данные</summary>
                <dl>
                  <dt>Хеш фрагмента</dt><dd className="sm-mono">{evidence.sourceHash}</dd>
                  <dt>Байты</dt><dd className="sm-mono">{evidence.location.byteStart}–{evidence.location.byteEnd}</dd>
                  <dt>Источник анализа</dt><dd>{evidence.producerVersion}</dd>
                  <dt>ID доказательства</dt><dd className="sm-mono">{evidence.id}</dd>
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
                {coverageText[model.coverage.status] ?? "Проверено неполностью"}
              </span>
            </div>
            <p>{model.coverage.summary}</p>
            {model.coverage.warnings.map((warning, index) => (
              <p className="sm-warning-text" key={index}>{humanCoverageWarning(warning)}</p>
            ))}
            {model.coverage.warnings.length > 0 && (
              <details className="sm-tech">
                <summary>Технические подробности покрытия</summary>
                <ul>
                  {model.coverage.warnings.map((warning, index) => (
                    <li key={index} className="sm-mono">{warning}</li>
                  ))}
                </ul>
              </details>
            )}
            {model.diagnostics.length > 0 && (
              <details className="sm-tech">
                <summary>Технические пробелы · {model.diagnostics.length}</summary>
                {model.diagnostics.map((item) => (
                  <article key={item.id}>
                    <strong>{diagnosticText(item.code, item.message)}</strong>
                    <details>
                      <summary>Технический код</summary>
                      <code>{item.code}</code>
                    </details>
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
                  {policy.status === "assumption" ? "Предположение, нужно подтверждение" : "Утверждённая политика"}
                </summary>
                <pre>{policy.description}</pre>
                <p>Версия: {policy.version} · источник: {policy.source}</p>
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