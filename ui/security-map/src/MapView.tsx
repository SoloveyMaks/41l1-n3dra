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
import { NODE_WIDTH, computeMapLayout, estimateNodeHeight, initialFocusNodeIds } from "./layout";
import { NodeIcon, nodeMeta, toneColor } from "./NodeIcon";
import { SecurityNode, type SecurityNodeData } from "./SecurityNode";
import { SecurityEdge } from "./SecurityEdge";
import { buildEdgeRoutes } from "./edgeRouting";
import { buildExplorationTree, type TreeRow } from "./tree";
import "@xyflow/react/dist/style.css";
import "./style.css";

export type MapViewProps = {
  model: MapViewModel;
  loadState: LoadState;
  logoUrl?: string;
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

const sourceErrorReason = (code: string): string => {
  if (code === "source_unavailable")
    return "Сервис не смог отдать сохранённый фрагмент.";
  if (code === "evidence_not_available")
    return "Фрагмент не найден в сохранённом снимке.";
  if (code === "source_scope_mismatch")
    return "Фрагмент относится к другому запуску или снимку.";
  return "Сервис не смог загрузить фрагмент.";
};

const findingSeverityOrder: Record<string, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
  info: 4,
};

const findingStatusOrder: Record<string, number> = {
  confirmed: 0,
  open: 1,
  inconclusive: 2,
  dismissed: 3,
};

const findingConfidenceOrder: Record<string, number> = {
  high: 0,
  medium: 1,
  low: 2,
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
  const [mobileView, setMobileView] = useState<"map" | "details">("map");
  const [viewMode, setViewMode] = useState<"overview" | "tree">("overview");
  const [expandedTree, setExpandedTree] = useState<Set<string>>(() => new Set<string>());
  const [searchQuery, setSearchQuery] = useState("");
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
  const primaryFinding = useMemo(() => {
    const withRefs = model.findings.filter((finding) => finding.nodeIds.length > 0);
    const pool = withRefs.length ? withRefs : model.findings;
    return [...pool].sort((a, b) =>
      (findingSeverityOrder[a.severity] ?? 9) - (findingSeverityOrder[b.severity] ?? 9) ||
      (findingStatusOrder[a.status] ?? 9) - (findingStatusOrder[b.status] ?? 9) ||
      (findingConfidenceOrder[a.confidence] ?? 9) - (findingConfidenceOrder[b.confidence] ?? 9) ||
      a.label.localeCompare(b.label),
    )[0] ?? null;
  }, [model.findings]);
  const focusIds = useMemo(() => {
    if (!primaryFinding || !primaryFinding.nodeIds.length) return [];
    return initialFocusNodeIds(model.nodes, model.edges, model.findings, primaryFinding.id);
  }, [primaryFinding, model.nodes, model.edges, model.findings]);
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
  const explorationRoots = useMemo(
    () => buildExplorationTree(model.nodes, model.edges, new Set()).roots,
    [model.nodes, model.edges],
  );
  const treeModel = useMemo(
    () => buildExplorationTree(model.nodes, model.edges, expandedTree),
    [model.nodes, model.edges, expandedTree],
  );
  useEffect(() => {
    setExpandedTree(new Set(explorationRoots));
  }, [model.revision, explorationRoots.join("|")]);
  const searchAll = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    if (!query) return [];
    return model.nodes.filter((node) =>
      `${node.label} ${node.kind} ${node.symbolKey}`.toLowerCase().includes(query),
    );
  }, [searchQuery, model.nodes]);
  const searchResults = searchAll.slice(0, 8);
  const searchMatchIds = useMemo(
    () => new Set(searchAll.map((node) => node.id)),
    [searchAll],
  );
  const nodesMeasured =
    mapNodes.length > 0 &&
    mapNodes.every((node) => Boolean(node.measured?.width && node.measured?.height));
  const nodeTypes = useMemo(() => ({ securityNode: SecurityNode }), []);
  const edgeTypes = useMemo(() => ({ securityEdge: SecurityEdge }), []);
  const canvasKey = `${Math.round(canvasSize.width / 50)}x${Math.round(canvasSize.height / 50)}`;
  const [localSelection, setLocalSelection] = useState<Selection | null>(null);
  const [openRequest, setOpenRequest] = useState<EvidenceRequest | null>(null);
  const autoSelectedKey = useRef<string | null>(null);
  const skipDetailsScroll = useRef(false);
  const lastFocusedKey = useRef<string | null>(null);

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

  const autoSelectionKey = `${model.scope.workspaceId}:${model.scope.runId}:${model.scope.snapshotId}:${model.revision}`;
  useEffect(() => {
    if (props.selection !== undefined) return;
    if (autoSelectedKey.current === autoSelectionKey) return;
    autoSelectedKey.current = autoSelectionKey;
    setOpenRequest(null);
    if (primaryFinding) {
      skipDetailsScroll.current = true;
      setLocalSelection({ kind: "finding", id: primaryFinding.id });
    } else {
      setLocalSelection(null);
    }
  }, [props.selection, autoSelectionKey, primaryFinding]);

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
    if (skipDetailsScroll.current) {
      skipDetailsScroll.current = false;
      return;
    }
    if (
      selection &&
      mobileView === "details" &&
      window.matchMedia("(max-width: 1100px)").matches
    ) {
      detailsRef.current?.scrollTo({ top: 0 });
    }
  }, [selection?.kind, selection?.id, mobileView]);

  useEffect(() => {
    if (openRequest && window.matchMedia("(max-width: 1100px)").matches) {
      sourceRef.current?.scrollIntoView({ block: "start" });
    }
  }, [openRequest]);

  const choose = (value: Selection) => {
    setOpenRequest(null);
    setLocalSelection(value);
    if (viewMode === "tree" && value.kind === "node") {
      setExpandedTree((previous: Set<string>) => new Set(previous).add(value.id));
    }
    props.onSelect?.(value);
    if (typeof window !== "undefined" && window.matchMedia("(max-width: 1100px)").matches) {
      setRailOpen(false);
      setMobileView("details");
    }
  };

  const toggleTree = (id: string) => {
    setExpandedTree((previous: Set<string>) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectSearchResult = (id: string) => {
    choose({ kind: "node", id });
    setSearchQuery("");
    setExpandedTree((previous: Set<string>) => new Set(previous).add(id));
    focusViewport([id], 0.7, 1.2);
  };

  useEffect(() => {
    if (viewMode === "tree") setRailOpen(true);
  }, [viewMode]);

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

  const sourceError =
    activeSource &&
    (activeSource.status === "error" || activeSource.status === "unavailable")
      ? activeSource
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
    if (typeof window !== "undefined" && window.matchMedia("(max-width: 1100px)").matches) {
      setMobileView("details");
    }
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
  if (finding) focusIds.forEach((id) => adjacentIds.add(id));
  for (const item of model.edges) {
    if (selectedNodeIds.has(item.fromId)) adjacentIds.add(item.toId);
    if (selectedNodeIds.has(item.toId)) adjacentIds.add(item.fromId);
  }
  const hasSelection = Boolean(selection);
  const unknownEdges = model.edges.filter((item) => item.resolution === "unknown");
  const partialEdges = model.edges.filter((item) => item.resolution === "partial");
  const unresolvedEdges = [...unknownEdges, ...partialEdges];
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
  if (finding) {
    for (const item of model.edges) {
      if (focusIds.includes(item.fromId) && focusIds.includes(item.toId)) {
        connectedEdges.add(item.id);
      }
    }
  }

  const treeVisibleKey = [...treeModel.visibleIds].sort().join("|");
  const searchMatchKey = [...searchMatchIds].sort().join("|");
  useEffect(() => {
    setMapNodes((current) =>
      current.map((stored) => {
        const item = model.nodes.find((candidate) => candidate.id === stored.id);
        if (!item) return stored;
        const data = stored.data as unknown as SecurityNodeData;
        const treeHidden = viewMode === "tree" && !treeModel.visibleIds.has(item.id);
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
            dimmed: (hasSelection && !adjacentIds.has(item.id)) || treeHidden,
            searchMatch: searchMatchIds.has(item.id),
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
    focusIds.join("|"),
    layout?.viewId,
    JSON.stringify(layout?.nodePositions),
    viewMode,
    treeVisibleKey,
    searchMatchKey,
  ]);

  const edgeRoutes = useMemo(
    () => buildEdgeRoutes(mapNodes, model.edges),
    [mapNodes, model.edges],
  );

  const flowEdges: Edge[] = model.edges.map((item) => {
    const selected = edge?.id === item.id;
    const highlighted = selected || (!hasSelection ? false : connectedEdges.has(item.id));
    const inTree =
      treeModel.visibleIds.has(item.fromId) && treeModel.visibleIds.has(item.toId);
    const treeDimmed = viewMode === "tree" && !inTree;
    const crosslink =
      viewMode === "tree" && inTree && !treeModel.treeEdgeIds.has(item.id);
    const dimmed = (hasSelection && !highlighted) || treeDimmed;
    const colour =
      item.resolution === "resolved"
        ? selected
          ? "#c4b5fd"
          : crosslink
            ? "#fbbf24"
            : "#64748b"
        : item.resolution === "partial"
          ? "#fbbf24"
          : "#f59e0b";
    const route = edgeRoutes.get(item.id);
    return {
      id: item.id,
      type: "securityEdge",
      source: item.fromId,
      target: item.toId,
      label: edgeText(item),
      ariaLabel: `Связь ${edgeText(item)}`,
      selected,
      markerEnd: { type: MarkerType.ArrowClosed, color: colour },
      data: route
        ? { points: route.points, labelPoint: route.labelPoint }
        : undefined,
      style: {
        stroke: colour,
        strokeWidth: selected ? 3.2 : 1.7,
        strokeDasharray:
          crosslink || item.resolution !== "resolved" ? "7 6" : undefined,
        opacity: dimmed ? 0.32 : 1,
      },
      interactionWidth: 36,
      className: `sm-edge sm-edge--${item.resolution}${selected ? " is-selected" : ""}${
        crosslink ? " is-crosslink" : ""
      }`,
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

  const modelFocusKey = `${model.revision}:${focusIds.join("|")}`;
  const initialReadableIds = () => {
    const narrow = canvasSize.width > 0 && canvasSize.width < 600;
    return narrow && narrowFocusIds.length
      ? narrowFocusIds
      : readableFocusIds.length
        ? readableFocusIds
        : focusIds;
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
      return;
    }
    const idsToFocus = initialReadableIds();
    if (idsToFocus.length) focusViewport(idsToFocus);
    else fitAll();
  };

  useEffect(() => {
    if (!flow || !nodesMeasured || !canvasSize.width) return;
    if (lastFocusedKey.current === modelFocusKey) return;
    const timer = window.setTimeout(() => {
      lastFocusedKey.current = modelFocusKey;
      if (!focusIds.length) {
        void flow.fitView({ padding: 0.1, minZoom: 0.06, maxZoom: 1.2, duration: 0 });
        return;
      }
      focusViewport(initialReadableIds());
    }, 60);
    return () => window.clearTimeout(timer);
  }, [flow, nodesMeasured, modelFocusKey, canvasSize.width]);

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
      lastFocusedKey.current = modelFocusKey;
      const ids = initialReadableIds();
      if (ids.length) focusViewport(ids);
      else fitAll();
    });
  };

  const findingCountByNode = (id: string) =>
    model.findings.filter((item) => item.nodeIds.includes(id)).length;

  return (
    <section className="security-map" aria-label="Карта безопасности">
      <header className="sm-topbar">
        <div className="sm-brand">
          <span className="sm-brand-icon">
            {props.logoUrl ? (
              <img src={props.logoUrl} alt="NEDRA" className="sm-brand-logo" />
            ) : (
              <NodeIcon kind="route" size={19} />
            )}
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
        <div className="sm-search">
          <input
            type="search"
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") setSearchQuery("");
            }}
            placeholder="Поиск узла…"
            aria-label="Поиск узлов карты"
          />
          {searchQuery.trim() ? (
            <div className="sm-search-results" role="listbox" aria-label="Результаты поиска">
              {searchResults.length ? (
                searchResults.map((item) => {
                  const meta = nodeMeta(item.kind);
                  return (
                    <button
                      key={item.id}
                      type="button"
                      role="option"
                      aria-selected="false"
                      onClick={() => selectSearchResult(item.id)}
                    >
                      <NodeIcon kind={item.kind} size={14} />
                      <span>
                        <strong>{item.label}</strong>
                        <small>{meta.label}</small>
                      </span>
                    </button>
                  );
                })
              ) : (
                <p>Узлы не найдены</p>
              )}
            </div>
          ) : null}
        </div>
        <div className="sm-actions">
          <div className="sm-mode-toggle" role="group" aria-label="Режим карты">
            <button
              type="button"
              aria-pressed={viewMode === "overview"}
              onClick={() => setViewMode("overview")}
            >
              Обзор
            </button>
            <button
              type="button"
              aria-pressed={viewMode === "tree"}
              onClick={() => setViewMode("tree")}
            >
              Дерево
            </button>
          </div>
          <span
            className={`sm-health ${unresolvedEdges.length ? "is-warn" : model.coverage.status === "complete" ? "is-ok" : "is-partial"}`}
          >
            {unknownEdges.length
              ? `${unknownEdges.length} ${plural(unknownEdges.length, "связь не установлена", "связи не установлены", "связей не установлено")}`
              : partialEdges.length
                ? `${partialEdges.length} ${plural(partialEdges.length, "связь подтверждена частично", "связи подтверждены частично", "связей с частичным подтверждением")}`
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
        <span className="sm-layer-legend" aria-label="Порядок групп карты">
          <b>Кто</b><i>→</i><b>Маршрут</b><i>→</i><b>Доступ</b><i>→</i><b>Обработчик</b><i>→</i><b>Данные / операция</b>
        </span>
      </div>

      {unresolvedEdges.length > 0 && (
        <button
          className="sm-unknown-banner"
          onClick={() => choose({ kind: "edge", id: unresolvedEdges[0].id })}
        >
          <span>
            {unknownEdges.length && partialEdges.length
              ? `Не установлено: ${unknownEdges.length} · частично: ${partialEdges.length}`
              : unknownEdges.length
                ? `${unknownEdges.length} ${plural(unknownEdges.length, "связь не установлена", "связи не установлены", "связей не установлено")}`
                : `${partialEdges.length} ${plural(partialEdges.length, "связь подтверждена частично", "связи подтверждены частично", "связей с частичным подтверждением")}`}
          </span>
          <small>
            {unknownEdges.length
              ? "Жёлтый пунктир — это пробел анализа, а не безопасное состояние. Открыть первую связь →"
              : "Связи подтверждены частично — проверьте основание. Открыть первую связь →"}
          </small>
        </button>
      )}

      {model.provenance === "manual_source_review" && (
        <div className="sm-provenance" role="note">
          Ручной разбор исходника — не результат анализатора
        </div>
      )}

      <div className="sm-mobile-switch" role="group" aria-label="Карта или детали">
        <button
          type="button"
          aria-pressed={mobileView === "map"}
          onClick={() => setMobileView("map")}
        >
          Карта
        </button>
        <button
          type="button"
          aria-pressed={mobileView === "details"}
          onClick={() => setMobileView("details")}
        >
          Детали
        </button>
      </div>

      <div className={`sm-workspace ${railOpen ? "has-rail" : ""} ${mobileView === "details" ? "show-details" : ""}`}>
        <aside className={`sm-rail ${railOpen ? "is-open" : ""}`} aria-label="Путь и проблемы">
          <div className="sm-rail-head">
            <span>{viewMode === "tree" ? "Дерево исследования" : "Путь и проблемы"}</span>
            <button onClick={() => setRailOpen(false)} aria-label="Закрыть список">×</button>
          </div>
          {viewMode === "tree" && (
            <section className="sm-rail-section sm-tree-section">
              <h2>
                Соседи <span>{treeModel.rows.length}</span>
              </h2>
              <p className="sm-rail-empty sm-tree-meta">
                Корни: {treeModel.roots.length} · сироты: {treeModel.orphans.length} · перекрёстные связи:{" "}
                {treeModel.crosslinkEdgeIds.size}
              </p>
              <div className="sm-tree">
                {treeModel.rows.map((row: TreeRow, index) => {
                  const meta = nodeMeta(row.node.kind);
                  const expanded = expandedTree.has(row.node.id);
                  return (
                    <div
                      key={`${row.rootId}:${row.node.id}:${row.depth}:${index}`}
                      className={`sm-tree-row${row.cycle ? " is-cycle" : ""}${
                        row.crosslink ? " is-crosslink" : ""
                      }`}
                      style={{ paddingLeft: `${8 + row.depth * 12}px` }}
                    >
                      <button
                        type="button"
                        className="sm-tree-toggle"
                        onClick={() => toggleTree(row.node.id)}
                        disabled={!row.hasChildren}
                        aria-label={
                          row.hasChildren
                            ? expanded
                              ? `Свернуть соседей ${row.node.label}`
                              : `Раскрыть соседей ${row.node.label}`
                            : "Нет соседей"
                        }
                      >
                        {row.hasChildren ? (expanded ? "−" : "+") : "·"}
                      </button>
                      <button
                        type="button"
                        className="sm-tree-item"
                        onClick={() => {
                          choose({ kind: "node", id: row.node.id });
                          focusViewport([row.node.id], 0.7, 1.2);
                        }}
                      >
                        <span className="sm-tree-icon">
                          <NodeIcon kind={row.node.kind} size={13} />
                        </span>
                        <span className="sm-tree-copy">
                          <strong>{row.node.label}</strong>
                          <small>
                            {meta.label}
                            {row.depth === 0 ? " · корень" : ` · шаг ${row.depth}`}
                          </small>
                        </span>
                        {row.cycle ? <em className="sm-tree-badge">цикл</em> : null}
                        {row.crosslink ? <em className="sm-tree-badge">crosslink</em> : null}
                      </button>
                    </div>
                  );
                })}
              </div>
              {treeModel.orphans.length > 0 && (
                <p className="sm-tree-meta">
                  Сироты: {treeModel.orphans.map((id) => model.nodes.find((node) => node.id === id)?.label).filter(Boolean).join(", ")}
                </p>
              )}
            </section>
          )}
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
                edgeTypes={edgeTypes}
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
              {!finding.nodeIds.length && (
                <p className="sm-muted">
                  У этой проблемы нет привязки к узлам карты. Путь не подсвечен;
                  ниже — факты и доказательства, но это не значит, что маршрут безопасен.
                </p>
              )}
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
                    Фрагмент кода недоступен. {sourceErrorReason(activeSource.code)}
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
                  {sourceError && (
                    <>
                      <dt>Код ошибки</dt>
                      <dd className="sm-mono">{sourceError.code}</dd>
                    </>
                  )}
                </dl>
              </details>
            </section>
          )}
          <details className="sm-tech">
            <summary>Технический контекст и подпись</summary>
            <dl>
              <dt>Рабочая область</dt><dd className="sm-mono">{model.scope.workspaceId}</dd>
              <dt>Запуск</dt><dd className="sm-mono">{model.scope.runId}</dd>
              <dt>Снимок</dt><dd className="sm-mono">{model.scope.snapshotId}</dd>
              <dt>Ревизия</dt><dd className="sm-mono">{model.revision}</dd>
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