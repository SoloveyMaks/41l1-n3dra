import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  ReactFlow,
  ViewportPortal,
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
import { sameScope, scopeKey as makeScopeKey } from "./model";
import {
  MAP_LANES,
  NODE_WIDTH,
  PROBLEM_LANE,
  computeMapLayout,
  computeTreeLayout,
  estimateNodeHeight,
  initialFocusNodeIds,
  laneIndexForKind,
  laneX,
} from "./layout";
import { NodeIcon, nodeMeta, toneColor } from "./NodeIcon";
import { SecurityNode, type SecurityNodeData } from "./SecurityNode";
import { FindingNode, type FindingNodeData } from "./FindingNode";
import { SecurityEdge } from "./SecurityEdge";
import { buildEdgeRoutes } from "./edgeRouting";
import { buildExplorationTree, type TreeRow } from "./tree";
import { enumerateAccessPaths, type AccessPath } from "./paths";
import {
  basisText,
  confidenceText,
  coverageText,
  edgeKindText,
  plural,
  resolutionText,
  severityText,
  statusText,
} from "./labels";
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

const severityColor: Record<string, string> = {
  critical: "#fb7185",
  high: "#fb923c",
  medium: "#fbbf24",
  low: "#38bdf8",
  info: "#94a3b8",
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

const DEFAULT_VIEWPORT = { x: 32, y: 88, zoom: 0.7 };
const FINDING_NODE_PREFIX = "finding:";
const FINDING_LINK_PREFIX = "finding-link:";

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

const TREE_LAYOUT_PREFIX = "nedra.map.tree.";

function readTreePositions(key: string): Record<string, { x: number; y: number }> {
  if (typeof window === "undefined") return {};
  try {
    const parsed = JSON.parse(window.localStorage.getItem(key) ?? "null") as
      | { nodePositions?: Record<string, { x: number; y: number }> }
      | null;
    return parsed?.nodePositions ?? {};
  } catch {
    return {};
  }
}

function writeTreePositions(
  key: string,
  scope: MapViewModel["scope"],
  nodePositions: Record<string, { x: number; y: number }>,
  viewport?: { x: number; y: number; zoom: number },
): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, JSON.stringify({
      scope,
      viewId: "security-map",
      nodePositions,
      viewport,
    }));
  } catch {
    // Storage may be full or disabled; the in-memory layout still works.
  }
}

export function MapView(props: MapViewProps) {
  const { model, loadState, layout, sourceState, onRetry } = props;
  const canvasRef = useRef<HTMLDivElement>(null);
  const detailsRef = useRef<HTMLElement>(null);
  const sourceRef = useRef<HTMLElement>(null);
  const [flow, setFlow] = useState<ReactFlowInstance | null>(null);
  const [railOpen, setRailOpen] = useState(false);
  const [mobileView, setMobileView] = useState<"map" | "details">("map");
  const [viewMode] = useState<"overview" | "tree">("tree");
  const [expandedTree, setExpandedTree] = useState<Set<string>>(() => new Set<string>());
  const [searchQuery, setSearchQuery] = useState("");
  const computed = useMemo(
    () => computeMapLayout(model.nodes, model.edges),
    [model.nodes, model.edges],
  );
  const overviewPositions = useRef<Record<string, { x: number; y: number }>>({
    ...computed,
    ...(layout?.nodePositions ?? {}),
  });
  const [mapNodes, setMapNodes] = useState<Node[]>(() =>
    model.nodes.map((item) =>
      nodeFor(item, positionFromLayout(layout, computed, item.id)),
    ),
  );
  const [findingNodes, setFindingNodes] = useState<Node[]>([]);
  const [canvasSize, setCanvasSize] = useState({ width: 0, height: 0 });
  const [viewport, setViewport] = useState(layout?.viewport ?? DEFAULT_VIEWPORT);
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
  const allExpanded = useMemo(
    () => new Set(model.nodes.map((node) => node.id)),
    [model.nodes],
  );
  const treeModel = useMemo(
    () => buildExplorationTree(model.nodes, model.edges, expandedTree),
    [model.nodes, model.edges, expandedTree],
  );
  const treeLayoutKey = `${TREE_LAYOUT_PREFIX}${makeScopeKey(model.scope)}`;
  const [treeSavedPositions, setTreeSavedPositions] = useState<
    Record<string, { x: number; y: number }>
  >(() => readTreePositions(treeLayoutKey));
  useEffect(() => {
    setTreeSavedPositions(readTreePositions(treeLayoutKey));
  }, [treeLayoutKey]);
  const treePositions = useMemo(
    () => ({
      ...computeTreeLayout(treeModel, model.nodes).positions,
      ...(layout?.nodePositions ?? {}),
      ...treeSavedPositions,
    }),
    [treeModel, model.nodes, layout?.nodePositions, treeSavedPositions],
  );
  const pathSummary = useMemo(() => enumerateAccessPaths(model), [model]);
  const treeVisibleKey = [...treeModel.visibleIds].sort().join("|");
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
  const searchMatchKey = [...searchMatchIds].sort().join("|");
  const nodesMeasured =
    mapNodes.length > 0 &&
    mapNodes.every((node) => Boolean(node.measured?.width && node.measured?.height));
  const nodeTypes = useMemo(
    () => ({ securityNode: SecurityNode, findingNode: FindingNode }),
    [],
  );
  const edgeTypes = useMemo(() => ({ securityEdge: SecurityEdge }), []);
  const canvasKey = `${Math.round(canvasSize.width / 50)}x${Math.round(canvasSize.height / 50)}`;
  const [localSelection, setLocalSelection] = useState<Selection | null>(null);
  const [openRequest, setOpenRequest] = useState<EvidenceRequest | null>(null);
  const autoSelectedKey = useRef<string | null>(null);
  const skipDetailsScroll = useRef(false);
  const lastFocusedKey = useRef<string | null>(null);

  useEffect(() => {
    if (viewMode === "tree") setExpandedTree(allExpanded);
  }, [viewMode, model.revision, allExpanded]);

  useEffect(() => {
    const positions = viewMode === "tree" ? treePositions : overviewPositions.current;
    setMapNodes((previous) => {
      const previousById = new Map(previous.map((node) => [node.id, node]));
      return model.nodes.map((item) => {
        const stored = previousById.get(item.id);
        const position =
          positions[item.id] ??
          computed[item.id] ??
          stored?.position ?? { x: 0, y: 0 };
        return stored ? { ...stored, position } : nodeFor(item, position);
      });
    });
  }, [model.revision, model.nodes, viewMode, treePositions, computed]);

  useEffect(() => {
    if (!layout?.nodePositions) return;
    overviewPositions.current = { ...computed, ...layout.nodePositions };
    if (viewMode !== "overview") return;
    setMapNodes((previous) =>
      previous.map((node) => ({
        ...node,
        position: overviewPositions.current[node.id] ?? node.position,
      })),
    );
  }, [layout?.viewId, JSON.stringify(layout?.nodePositions), computed, viewMode]);

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

  const scopeKey = `${model.scope.workspaceId}:${model.scope.runId}:${model.scope.snapshotId}`;
  const lastScopeKey = useRef(scopeKey);
  useEffect(() => {
    if (lastScopeKey.current === scopeKey) return;
    lastScopeKey.current = scopeKey;
    setLocalSelection(null);
    setOpenRequest(null);
    // A new scope may bring new findings; let auto-focus choose again.
    autoSelectedKey.current = null;
  }, [scopeKey]);

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

  const autoSelectionKey = `${model.scope.workspaceId}:${model.scope.runId}:${model.scope.snapshotId}:${model.revision}`;
  useEffect(() => {
    if (props.selection !== undefined) return;
    if (autoSelectedKey.current === autoSelectionKey) return;
    if (!primaryFinding) {
      // Do not consume the key: findings may arrive after the map payload.
      setLocalSelection(null);
      return;
    }
    autoSelectedKey.current = autoSelectionKey;
    setOpenRequest(null);
    skipDetailsScroll.current = true;
    setLocalSelection({ kind: "finding", id: primaryFinding.id });
  }, [props.selection, autoSelectionKey, primaryFinding]);

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

  const selectPath = (path: AccessPath) => {
    const terminal = path.nodeIds[path.nodeIds.length - 1];
    if (terminal) choose({ kind: "node", id: terminal });
    setExpandedTree((previous) => new Set([...previous, ...path.nodeIds]));
    focusViewport(path.nodeIds, 0.4, 1.0);
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

  const findingByNode = useMemo(() => {
    const summary = new Map<string, { count: number; severity?: string }>();
    for (const item of model.findings) {
      for (const id of item.nodeIds) {
        const current = summary.get(id) ?? { count: 0 };
        const worse =
          current.severity === undefined ||
          (findingSeverityOrder[item.severity] ?? 9) <
            (findingSeverityOrder[current.severity] ?? 9);
        summary.set(id, {
          count: current.count + 1,
          severity: worse ? item.severity : current.severity,
        });
      }
    }
    return summary;
  }, [model.findings]);

  const selectedNodeIds = new Set<string>();
  if (node) selectedNodeIds.add(node.id);
  if (edge) {
    selectedNodeIds.add(edge.fromId);
    selectedNodeIds.add(edge.toId);
  }
  if (finding) finding.nodeIds.forEach((id) => selectedNodeIds.add(id));
  // The highlighted path belongs to the selected finding, not to the primary
  // one: its own anchors decide which chain is emphasised.
  const selectedFindingFocusIds = useMemo(
    () =>
      finding && finding.nodeIds.length
        ? initialFocusNodeIds(model.nodes, model.edges, model.findings, finding.id)
        : [],
    [finding, model.nodes, model.edges, model.findings],
  );
  const adjacentIds = new Set(selectedNodeIds);
  if (finding) selectedFindingFocusIds.forEach((id) => adjacentIds.add(id));
  for (const item of model.edges) {
    if (selectedNodeIds.has(item.fromId)) adjacentIds.add(item.toId);
    if (selectedNodeIds.has(item.toId)) adjacentIds.add(item.fromId);
  }
  const hasSelection = Boolean(selection);
  const unknownEdges = model.edges.filter((item) => item.resolution === "unknown");
  const partialEdges = model.edges.filter((item) => item.resolution === "partial");
  const unresolvedEdges = [...unknownEdges, ...partialEdges];
  const unlinkedFindingCount = model.findings.filter(
    (item) => item.nodeIds.length === 0,
  ).length;
  const unlinkedFindings = model.findings.filter((item) => item.nodeIds.length === 0);
  const edgeLabel = (id: string) => model.nodes.find((node) => node.id === id)?.label ?? id;
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
      if (
        selectedFindingFocusIds.includes(item.fromId) &&
        selectedFindingFocusIds.includes(item.toId)
      ) {
        connectedEdges.add(item.id);
      }
    }
  }
  const adjacencyKey = [...adjacentIds].sort().join("|");
  const unknownNodeKey = [...unknownNodeIds].sort().join("|");

  useEffect(() => {
    setMapNodes((current) =>
      current.map((stored) => {
        const item = model.nodes.find((candidate) => candidate.id === stored.id);
        if (!item) return stored;
        const data = stored.data as unknown as SecurityNodeData;
        const treeHidden = viewMode === "tree" && !treeModel.visibleIds.has(item.id);
        const summary = findingByNode.get(item.id);
        return {
          ...stored,
          selected: node?.id === item.id,
          data: {
            ...data,
            findingCount: summary?.count ?? 0,
            severity: summary?.severity,
            unknown: unknownNodeIds.has(item.id),
            neighbour: adjacentIds.has(item.id) && node?.id !== item.id,
            dimmed: (hasSelection && !adjacentIds.has(item.id)) || treeHidden,
            searchMatch: searchMatchIds.has(item.id),
            treeMode: viewMode === "tree",
          },
        };
      }),
    );
  }, [
    selection?.kind,
    selection?.id,
    model.revision,
    model.nodes,
    model.edges.length,
    model.findings.length,
    focusIds.join("|"),
    layout?.viewId,
    JSON.stringify(layout?.nodePositions),
    viewMode,
    treeVisibleKey,
    searchMatchKey,
    findingByNode,
    unknownNodeKey,
    adjacencyKey,
    hasSelection,
    node?.id,
  ]);

  const findingPositions = useMemo(() => {
    if (viewMode !== "overview") return {};
    const anchorCenter = new Map<string, number>();
    for (const item of mapNodes) {
      const data = item.data as unknown as SecurityNodeData;
      const height =
        item.measured?.height ??
        estimateNodeHeight({
          id: item.id,
          kind: data.kind,
          label: data.label,
          symbolKey: "",
          evidenceRefs: [],
          producerVersion: "",
        });
      anchorCenter.set(item.id, item.position.y + height / 2);
    }
    const ordered = model.findings
      .map((item) => {
        const anchors = item.nodeIds
          .map((id) => anchorCenter.get(id))
          .filter((value): value is number => value !== undefined);
        const center = anchors.length
          ? anchors.reduce((sum, value) => sum + value, 0) / anchors.length
          : null;
        return { item, center };
      })
      .sort((a, b) => {
        if (a.center === null && b.center === null)
          return (
            (findingSeverityOrder[a.item.severity] ?? 9) -
              (findingSeverityOrder[b.item.severity] ?? 9) ||
            a.item.label.localeCompare(b.item.label)
          );
        if (a.center === null) return 1;
        if (b.center === null) return -1;
        return (
          a.center - b.center ||
          (findingSeverityOrder[a.item.severity] ?? 9) -
            (findingSeverityOrder[b.item.severity] ?? 9) ||
          a.item.label.localeCompare(b.item.label)
        );
      });
    const placed: Record<string, { x: number; y: number }> = {};
    let nextTop = 0;
    for (const { item, center } of ordered) {
      // A finding without findingNodeLinks has no path and would look like a
      // disconnected "end of chain". It stays in the rail, explicitly unlinked.
      if (!item.nodeIds.length) continue;
      const desiredTop = (center ?? nextTop + 96) - 60;
      const top = Math.max(desiredTop, nextTop);
      placed[item.id] = { x: laneX(PROBLEM_LANE), y: top };
      nextTop = top + 132;
    }
    return placed;
  }, [mapNodes, model.findings, viewMode]);

  useEffect(() => {
    if (viewMode !== "overview") {
      setFindingNodes((previous) => (previous.length ? [] : previous));
      return;
    }
    setFindingNodes((previous) => {
      const measured = new Map(previous.map((item) => [item.id, item.measured]));
      return model.findings
        .filter((item) => item.nodeIds.length > 0)
        .map((item) => {
          const id = `${FINDING_NODE_PREFIX}${item.id}`;
          const active =
            !hasSelection ||
            finding?.id === item.id ||
            item.nodeIds.some((nodeId) => adjacentIds.has(nodeId));
          const data: FindingNodeData = {
            label: item.label,
            severity: item.severity,
            status: item.status,
            confidence: item.confidence,
            evidenceCount: item.evidenceRefs.length,
            anchorCount: item.nodeIds.length,
            anchored: true,
            dimmed: !active,
            neighbour:
              hasSelection &&
              finding?.id !== item.id &&
              item.nodeIds.some((nodeId) => adjacentIds.has(nodeId)),
          };
          return {
            id,
            type: "findingNode",
            position: findingPositions[item.id] ?? {
              x: laneX(PROBLEM_LANE),
              y: 0,
            },
            data,
            draggable: false,
            style: { width: NODE_WIDTH },
            measured: measured.get(id),
            ariaLabel: `Проблема: ${item.label}`,
          } satisfies Node;
        });
    });
  }, [
    findingPositions,
    model.findings,
    viewMode,
    selection?.kind,
    selection?.id,
    adjacencyKey,
    hasSelection,
    finding?.id,
  ]);

  const visibleFactNodes = useMemo(
    () =>
      viewMode === "tree"
        ? mapNodes.filter((item) => treeModel.visibleIds.has(item.id))
        : mapNodes,
    [viewMode, mapNodes, treeModel],
  );
  const displayNodes = useMemo(() => {
    const treeMode = viewMode === "tree";
    const factNodes = visibleFactNodes.map((item) => {
      const data = item.data as unknown as SecurityNodeData;
      return data.treeMode === treeMode
        ? item
        : { ...item, data: { ...data, treeMode } };
    });
    return treeMode ? factNodes : [...factNodes, ...findingNodes];
  }, [viewMode, visibleFactNodes, findingNodes]);

  const edgeRoutes = useMemo(
    () => buildEdgeRoutes(mapNodes, model.edges),
    [mapNodes, model.edges],
  );

  const visibleEdges = useMemo(
    () =>
      viewMode === "tree"
        ? model.edges.filter(
            (item) =>
              treeModel.visibleIds.has(item.fromId) &&
              treeModel.visibleIds.has(item.toId),
          )
        : model.edges,
    [viewMode, model.edges, treeModel],
  );

  const flowEdges: Edge[] = visibleEdges.map((item) => {
    const selected = edge?.id === item.id;
    const highlighted = selected || (!hasSelection ? false : connectedEdges.has(item.id));
    const crosslink =
      viewMode === "tree" && !treeModel.treeEdgeIds.has(item.id);
    const dimmed = (hasSelection && !highlighted) || (viewMode === "tree" && crosslink);
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
    const treeEdge = viewMode === "tree";
    const evidenceLabel = item.evidenceRefs.length
      ? `${item.evidenceRefs.length} док.`
      : "нет док.";
    return {
      id: item.id,
      type: treeEdge ? "default" : "securityEdge",
      source: item.fromId,
      target: item.toId,
      ...(treeEdge
        ? { sourceHandle: "tree-out", targetHandle: "tree-in" }
        : {}),
      label: treeEdge
        ? `${edgeText(item)} · ${evidenceLabel}`
        : edgeText(item),
      labelStyle: treeEdge
        ? { fill: "#cbd5e1", fontSize: 9, fontWeight: 600 }
        : undefined,
      labelBgStyle: treeEdge
        ? { fill: "#0b1220", fillOpacity: 0.92 }
        : undefined,
      labelBgPadding: treeEdge ? ([4, 2] as [number, number]) : undefined,
      ariaLabel: `Связь ${edgeText(item)}`,
      selected,
      markerEnd: { type: MarkerType.ArrowClosed, color: colour },
      data: treeEdge
        ? undefined
        : route
          ? {
              points: route.points,
              labelPoint: route.labelPoint,
              evidenceCount: item.evidenceRefs.length,
              resolution: item.resolution,
            }
          : {
              evidenceCount: item.evidenceRefs.length,
              resolution: item.resolution,
            },
      style: {
        stroke: colour,
        strokeWidth: selected ? 3.2 : 1.7,
        strokeDasharray:
          crosslink || item.resolution !== "resolved" ? "7 6" : undefined,
        opacity: dimmed ? 0.3 : 1,
      },
      interactionWidth: 36,
      className: `sm-edge sm-edge--${item.resolution}${selected ? " is-selected" : ""}${
        crosslink ? " is-crosslink" : ""
      }${viewMode === "tree" && !crosslink ? " is-tree-edge" : ""}`,
    };
  });

  const findingLinks: Edge[] = useMemo(() => {
    if (viewMode !== "overview") return [];
    const nodeX = new Map(mapNodes.map((item) => [item.id, item.position.x]));
    const links: Edge[] = [];
    for (const item of model.findings) {
      const anchors = item.nodeIds.filter((id) => nodeX.has(id));
      if (!anchors.length) continue;
      // One presented link keeps the map readable; the card states how many
      // real findingNodeLinks exist and the details panel lists them all.
      const anchor = [...anchors].sort(
        (a, b) =>
          (nodeX.get(b) ?? 0) - (nodeX.get(a) ?? 0) || a.localeCompare(b),
      )[0];
      const colour = severityColor[item.severity.toLowerCase()] ?? "#94a3b8";
      links.push({
        id: `${FINDING_LINK_PREFIX}${item.id}:${anchor}`,
        source: anchor,
        sourceHandle: "out",
        target: `${FINDING_NODE_PREFIX}${item.id}`,
        targetHandle: "in",
        type: "default",
        style: {
          stroke: colour,
          strokeWidth: 1.2,
          strokeDasharray: "3 5",
          opacity: hasSelection && finding?.id !== item.id ? 0.18 : 0.62,
        },
        markerEnd: { type: MarkerType.ArrowClosed, color: colour },
        selectable: false,
        focusable: false,
        interactionWidth: 0,
      });
    }
    return links;
  }, [model.findings, mapNodes, viewMode, hasSelection, finding?.id]);

  const allFlowEdges = useMemo(
    () => [...flowEdges, ...findingLinks],
    [flowEdges, findingLinks],
  );

  const commitLayout = (
    currentNodes: Node[],
    nextViewport: { x: number; y: number; zoom: number } | undefined =
      layout?.viewport,
  ) => {
    const nodePositions = Object.fromEntries(
      currentNodes
        .filter((item) => !item.id.startsWith(FINDING_NODE_PREFIX))
        .map((item) => [item.id, item.position]),
    );
    props.onLayoutChange?.({
      scope: model.scope,
      viewId: layout?.viewId ?? "security-map",
      nodePositions,
      ...(nextViewport ? { viewport: nextViewport } : {}),
    });
  };

  const onNodesChange = (changes: NodeChange<Node>[]) => {
    const changeId = (change: NodeChange<Node>) =>
      "id" in change ? change.id : change.item.id;
    const findingChanges = changes.filter((item) =>
      changeId(item).startsWith(FINDING_NODE_PREFIX),
    );
    const factChanges = changes.filter(
      (item) => !changeId(item).startsWith(FINDING_NODE_PREFIX),
    );
    if (findingChanges.length) {
      setFindingNodes((previous) => applyNodeChanges(findingChanges, previous));
    }
    if (!factChanges.length) return;
    const next = applyNodeChanges(factChanges, mapNodes);
    setMapNodes(next);
    if (factChanges.some((item) => item.type === "position" && !item.dragging)) {
      const positions = Object.fromEntries(
        next.map((item) => [item.id, item.position]),
      );
      overviewPositions.current = positions;
      setTreeSavedPositions(positions);
      writeTreePositions(treeLayoutKey, model.scope, positions, viewport);
      commitLayout(next);
    }
  };

  const focusViewport = (
    ids: string[],
    minZoom = 0.34,
    maxZoom = 0.9,
  ) => {
    if (!flow || !ids.length) return;
    const positions =
      viewMode === "tree" ? treePositions : overviewPositions.current;
    const measured = new Map(
      [...mapNodes, ...findingNodes].map((item) => [item.id, item.measured]),
    );
    const selected = ids.flatMap((id) => {
      const position =
        positions[id] ??
        computed[id] ??
        findingNodes.find((item) => item.id === id)?.position;
      if (!position) return [];
      return [{ position, measured: measured.get(id) }];
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
    if (finding) {
      finding.nodeIds.forEach((id) => ids.add(id));
      ids.add(`${FINDING_NODE_PREFIX}${finding.id}`);
    }
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

  // Fit-all belongs to a real user mode switch only. Keying this effect on
  // `flow` alone used to re-run it after onInit/StrictMode remount and wipe the
  // readable initial focus with a fit of the whole graph. The fit also waits for
  // the switched layout to reach the canvas: ReactFlow re-initialises on a
  // canvas resize, and a fit against the previous instance is lost.
  const previousViewMode = useRef(viewMode);
  const pendingModeFit = useRef(false);
  useEffect(() => {
    if (previousViewMode.current === viewMode) return;
    previousViewMode.current = viewMode;
    pendingModeFit.current = true;
  }, [viewMode]);

  useEffect(() => {
    if (!pendingModeFit.current || !flow || !nodesMeasured) return;
    const timer = window.setTimeout(() => {
      pendingModeFit.current = false;
      void flow.fitView({ padding: 0.14, minZoom: 0.05, maxZoom: 1.2, duration: 220 });
    }, 80);
    return () => window.clearTimeout(timer);
  }, [viewMode, flow, nodesMeasured, mapNodes]);

  const reset = () => {
    if (viewMode === "tree") {
      setTreeSavedPositions({});
      try {
        window.localStorage.removeItem(treeLayoutKey);
      } catch {
        // Storage is optional; the in-memory reset still applies.
      }
      const positions = computeTreeLayout(treeModel, model.nodes).positions;
      setMapNodes((previous) =>
        previous.map((item) => ({
          ...item,
          position: positions[item.id] ?? item.position,
        })),
      );
      requestAnimationFrame(fitAll);
      return;
    }
    const nextPositions = computeMapLayout(model.nodes, model.edges);
    overviewPositions.current = nextPositions;
    setMapNodes((previous) =>
      previous.map((item) => ({
        ...item,
        position: nextPositions[item.id] ?? item.position,
      })),
    );
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

  const laneTop = useMemo(() => {
    const tops = [...mapNodes, ...findingNodes].map((item) => item.position.y);
    return (tops.length ? Math.min(...tops) : 0) - 96;
  }, [mapNodes, findingNodes]);
  const laneHeight = useMemo(() => {
    const boxes = [...mapNodes, ...findingNodes].map(
      (item) =>
        item.position.y + (item.measured?.height ?? 140),
    );
    return (boxes.length ? Math.max(...boxes) : 0) - laneTop + 40;
  }, [mapNodes, findingNodes, laneTop]);
  const usedLanes = useMemo(() => {
    const used = new Set(model.nodes.map((item) => laneIndexForKind(item.kind)));
    if (model.findings.some((item) => item.nodeIds.length > 0)) {
      used.add(PROBLEM_LANE);
    }
    return MAP_LANES.filter((lane) => used.has(lane.index));
  }, [model.nodes, model.findings]);

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
          <button className="sm-tool" onClick={reset} title="Сбросить расположение">
            ↺
          </button>
          <button className="sm-tool" onClick={onRetry} title="Обновить карту">
            ⟳
          </button>
        </div>
      </header>

      <div className="sm-intro">
        <span>
          Дерево связей: кто обращается → правило → маршрут → обработчик → данные.
          Блоки можно перетаскивать; расположение сохраняется для этого запуска.
        </span>
      </div>

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
            <span>{viewMode === "tree" ? "Дерево связей" : "Путь и проблемы"}</span>
            <button onClick={() => setRailOpen(false)} aria-label="Закрыть список">×</button>
          </div>
          {pathSummary.total > 0 && (
            <section className="sm-rail-section sm-paths-section">
              <h2>
                Все пути <span>{pathSummary.total}</span>
              </h2>
              <p className="sm-rail-empty sm-tree-meta">
                Простые пути без повторных узлов: цикл останавливается на повторе.
                {pathSummary.truncated ? " Показаны первые 300." : ""}
              </p>
              <div className="sm-path-list">
                {pathSummary.paths.slice(0, 80).map((path) => (
                  <button
                    key={path.id}
                    type="button"
                    className="sm-path-row"
                    onClick={() => selectPath(path)}
                    title={path.labels.join(" → ")}
                  >
                    <span className="sm-path-chain">{path.labels.join(" → ")}</span>
                    <span className="sm-path-badges">
                      {path.guarded ? (
                        <i className="is-ok">guard</i>
                      ) : (
                        <i className="is-warn">без guard</i>
                      )}
                      {path.permitted ? <i className="is-open">permitAll</i> : null}
                      {path.findings.length ? (
                        <i className="is-risk">{path.findings.length} проблем</i>
                      ) : null}
                    </span>
                  </button>
                ))}
              </div>
            </section>
          )}
          {unlinkedFindingCount > 0 && (
            <section className="sm-rail-section">
              <h2>
                Вне графа <span>{unlinkedFindingCount}</span>
              </h2>
              <p className="sm-rail-empty">
                Для этих проблем нет узла в графе: путь не строится. Они остаются в списке
                «Проблемы» и не выдаются за конец цепочки.
              </p>
              <div className="sm-path-list">
                {unlinkedFindings.slice(0, 60).map((finding) => (
                  <button
                    key={finding.id}
                    type="button"
                    className="sm-path-row"
                    onClick={() => choose({ kind: "finding", id: finding.id })}
                    title={finding.label}
                  >
                    <span className="sm-path-chain">{finding.label}</span>
                    <span className="sm-path-badges">
                      <i className={`is-${finding.severity}`}>{finding.severity}</i>
                      <i className="is-muted">{finding.status}</i>
                    </span>
                  </button>
                ))}
              </div>
            </section>
          )}
          {unresolvedEdges.length > 0 && (
            <section className="sm-rail-section">
              <h2>
                Неустановленные связи <span>{unresolvedEdges.length}</span>
              </h2>
              <p className="sm-rail-empty">
                Пробел анализа, а не безопасное состояние. Откройте связь, чтобы увидеть основание.
              </p>
              <div className="sm-path-list">
                {unresolvedEdges.slice(0, 30).map((edge) => (
                  <button
                    key={edge.id}
                    type="button"
                    className="sm-path-row"
                    onClick={() => choose({ kind: "edge", id: edge.id })}
                  >
                    <span className="sm-path-chain">
                      {edgeLabel(edge.fromId)} → {edgeLabel(edge.toId)}
                    </span>
                    <span className="sm-path-badges">
                      <i className="is-warn">{edge.resolution}</i>
                    </span>
                  </button>
                ))}
              </div>
            </section>
          )}
          {viewMode === "tree" && (
            <section className="sm-rail-section sm-tree-section">
              <h2>
                Связи <span>{treeModel.rows.length}</span>
              </h2>
              <p className="sm-rail-empty sm-tree-meta">
                Корни: {treeModel.roots.length} · сироты: {treeModel.orphans.length} · перекрёстные связи:{" "}
                {treeModel.crosslinkEdgeIds.size}
              </p>
              <div className="sm-tree-tools">
                <button type="button" onClick={() => setExpandedTree(allExpanded)}>
                  Раскрыть всё
                </button>
                <button
                  type="button"
                  onClick={() => setExpandedTree(new Set(treeModel.roots))}
                >
                  Только корни
                </button>
              </div>
              <p className="sm-tree-note">
                <b>Это не структура файлов.</b> Дерево строится по связям доступа:
                корень — кто обращается, ниже — правила, маршруты, обработчики и
                данные. Путь в файлах показывает только, где лежит доказательство;
                один маршрут может проходить через несколько файлов.
              </p>
              <div className="sm-tree">
                {treeModel.rows.map((row: TreeRow, index) => {
                  const meta = nodeMeta(row.node.kind);
                  const expanded = expandedTree.has(row.node.id);
                  const rowEdge = row.edgeId
                    ? model.edges.find((item) => item.id === row.edgeId)
                    : undefined;
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
                            {rowEdge
                              ? ` · ${edgeText(rowEdge)}`
                              : ""}
                          </small>
                          {rowEdge ? (
                            <small
                              className={
                                rowEdge.evidenceRefs.length ? "" : "is-missing"
                              }
                            >
                              {rowEdge.evidenceRefs.length
                                ? `${rowEdge.evidenceRefs.length} ${plural(
                                    rowEdge.evidenceRefs.length,
                                    "доказательство",
                                    "доказательства",
                                    "доказательств",
                                  )}`
                                : "нет фрагмента-основания"}
                              {rowEdge.resolution !== "resolved"
                                ? ` · ${resolutionText[rowEdge.resolution]}`
                                : ""}
                            </small>
                          ) : null}
                        </span>
                        {row.cycle ? <em className="sm-tree-badge">цикл</em> : null}
                        {row.crosslink ? <em className="sm-tree-badge">crosslink</em> : null}
                      </button>
                    </div>
                  );
                })}
              </div>
              <details className="sm-tech sm-tree-diff">
                <summary>Структура файлов ≠ карта доступа</summary>
                <p>
                  Структура файлов отвечает «где лежит код». Карта доступа
                  отвечает «кто и через какую проверку доходит до данных».
                </p>
                <p>
                  Папка и файл не доказывают доступ. Доказательство — конкретный
                  фрагмент с путём и строками; он открывается из узла или связи.
                </p>
              </details>
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
                  <small className={item.evidenceRefs.length ? "" : "is-missing"}>
                    {item.evidenceRefs.length
                      ? `${item.evidenceRefs.length} ${plural(
                          item.evidenceRefs.length,
                          "доказательство",
                          "доказательства",
                          "доказательств",
                        )}`
                      : "нет фрагмента-основания"}
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
                  <small className={item.nodeIds.length ? "" : "is-missing"}>
                    {item.nodeIds.length
                      ? `привязано к узлам: ${item.nodeIds.length}`
                      : "не привязано к узлам карты"}
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
                nodes={displayNodes}
                edges={allFlowEdges}
                nodeTypes={nodeTypes}
                edgeTypes={edgeTypes}
                onNodesChange={onNodesChange}
                onNodeClick={(_, item) => {
                  if (item.id.startsWith(FINDING_NODE_PREFIX)) {
                    choose({ kind: "finding", id: item.id.slice(FINDING_NODE_PREFIX.length) });
                    return;
                  }
                  choose({ kind: "node", id: item.id });
                }}
                onEdgeClick={(_, item) => {
                  if (item.id.startsWith(FINDING_LINK_PREFIX)) {
                    const [, findingId] = item.id
                      .slice(FINDING_LINK_PREFIX.length)
                      .split(":");
                    if (findingId) choose({ kind: "finding", id: findingId });
                    return;
                  }
                  choose({ kind: "edge", id: item.id });
                }}
                onPaneClick={clearSelection}
                onNodeDragStop={(_, item) => {
                  const next = mapNodes.map((candidate) =>
                    candidate.id === item.id
                      ? { ...candidate, position: item.position }
                      : candidate,
                  );
                  setMapNodes(next);
                  const positions = Object.fromEntries(
                    next.map((candidate) => [candidate.id, candidate.position]),
                  );
                  overviewPositions.current = positions;
                  setTreeSavedPositions(positions);
                  writeTreePositions(treeLayoutKey, model.scope, positions, viewport);
                  commitLayout(next);
                }}
                onMoveEnd={(_, nextViewport) => {
                  setViewport(nextViewport);
                  commitLayout(mapNodes, nextViewport);
                }}
                nodesConnectable={false}
                edgesReconnectable={false}
                nodesDraggable
                deleteKeyCode={null}
                key={canvasKey}
                fitView={false}
                defaultViewport={layout?.viewport ?? viewport}
                minZoom={0.18}
                maxZoom={1.6}
                panOnScroll
                selectionOnDrag={false}
                proOptions={{ hideAttribution: false }}
              >
                <Background
                  variant={BackgroundVariant.Dots}
                  gap={20}
                  size={1.2}
                  color="#334155"
                />
                {viewMode === "overview" ? (
                  <ViewportPortal>
                    {usedLanes.map((lane) => (
                      <div
                        key={lane.key}
                        className={`sm-lane${
                          lane.index === PROBLEM_LANE ? " sm-lane--problem" : ""
                        }`}
                        style={{
                          transform: `translate(${laneX(lane.index)}px, ${laneTop}px)`,
                        }}
                      >
                        <div className="sm-lane-head">
                          <span className="sm-lane-title">{lane.title}</span>
                          <span className="sm-lane-hint">{lane.hint}</span>
                        </div>
                        <div
                          className="sm-lane-guide"
                          style={{ height: laneHeight }}
                        />
                      </div>
                    ))}
                  </ViewportPortal>
                ) : null}
                <Controls className="sm-controls" showInteractive={false} />
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
                <span className={`sm-pill${edge.evidenceRefs.length ? "" : " is-missing"}`}>
                  {edge.evidenceRefs.length
                    ? `${edge.evidenceRefs.length} ${plural(
                        edge.evidenceRefs.length,
                        "доказательство",
                        "доказательства",
                        "доказательств",
                      )}`
                    : "нет доказательства"}
                </span>
              </div>
              <p className={edge.resolution === "resolved" ? "" : "sm-amber-text"}>{edge.resolutionReason}</p>
              {edge.resolution === "unknown" && (
                <p className="sm-amber-text">
                  Связь не установлена. Это пробел анализа, а не подтверждение,
                  что доступ безопасен.
                </p>
              )}
              {!edge.evidenceRefs.length && (
                <p className="sm-amber-text">
                  У связи нет сохранённого фрагмента-основания: утверждение
                  анализатора без отдельного доказательства.
                </p>
              )}
              <h3>Доказательства связи</h3>
              {edge.evidenceRefs.length ? (
                evidenceButtons(edge.evidenceRefs)
              ) : (
                <p className="sm-muted">
                  Фрагментов нет. Смотрите доказательства узлов по обе стороны
                  связи; они не заменяют основание перехода.
                </p>
              )}
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
              {!node.evidenceRefs.length && (
                <p className="sm-amber-text">
                  У узла нет сохранённого фрагмента: его роль известна из связи,
                  но подтвердить её отдельным кодом нельзя.
                </p>
              )}
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
              {finding.evidenceRefs.length ? (
                evidenceButtons(finding.evidenceRefs)
              ) : (
                <p className="sm-muted">У проблемы нет сохранённых фрагментов.</p>
              )}
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