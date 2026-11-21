import illustration from "../../../docs/security-map/pickup-path.illustration.json";
import { MapView } from "./MapView";
import { scopeKey } from "./model";
import type { MapViewModel, SecurityMapLayout, LoadState } from "./types";
import type { Location } from "../../../docs/data-contracts/contracts";

type ManualEvidence = {
  id: string;
  runId: string;
  snapshotId: string;
  location: Location;
  redactedSnippet: string;
  sourceHash: string;
  producerVersion: string;
  redaction: { status: string; contextIncluded: boolean };
};
export type PickupIllustration = {
  schemaVersion: string;
  provenance: { kind: string; analyzerOutput: boolean; sourceCommit: string };
  identity: { workspaceId: string; runId: string; snapshotId: string };
  nodes: {
    id: string;
    kind: string;
    label: string;
    symbolKey: string;
    evidenceRefs: string[];
  }[];
  edges: {
    id: string;
    fromId: string;
    toId: string;
    kind: string;
    evidenceRefs: string[];
    resolution: string;
    resolutionReason: string;
    producerVersion: string;
    policyRef?: string;
  }[];
  evidence: ManualEvidence[];
  findings: {
    id: string;
    status: string;
    severity: string;
    confidence: string;
    severityBasis: string;
    confidenceBasis: string;
    evidenceRefs: string[];
    trace: string[];
    policyRef: string;
    coverageWarnings: string[];
  }[];
  policyInputs: {
    id: string;
    version: string;
    status: string;
    resource: string;
    proposedRule: string;
    evidenceRefs: string[];
    contentHash: string;
    limitations: string[];
  }[];
  unresolvedRelations: {
    id: string;
    fromId: string;
    resolutionReason: string;
    evidenceRefs: string[];
  }[];
  diagnostics: { code: string; message: string }[];
  coverage: {
    filesVisited: number;
    skippedScopes: string[];
    unresolvedSymbols: string[];
  };
};
const nodeKinds: Record<string, string> = {
  "n-anonymous": "actor",
  "n-route": "route",
  "n-permit": "matcher",
  "n-handler": "function",
  "n-sql": "sink",
  "n-data": "data",
};
const edgeKinds: Record<string, string> = {
  "e-anonymous-route": "flows_to",
  "e-route-permit": "matches",
  "e-permit-handler": "permits",
  "e-handler-sql": "calls",
  "e-sql-data": "reads",
};

/** A view projection only: no RunIdentity, signature, fp/v1 or publish bundle is fabricated. */
export function adaptPickupIllustration(
  input: PickupIllustration,
): MapViewModel {
  if (
    input.schemaVersion !== "security-map.contract-illustration/1" ||
    input.provenance.kind !== "manual_source_review" ||
    input.provenance.analyzerOutput !== false
  )
    throw new Error("Not a manual pickup illustration");
  if (
    input.evidence.some(
      (e) =>
        e.runId !== input.identity.runId ||
        e.snapshotId !== input.identity.snapshotId ||
        e.redaction.status !== "reviewed_no_sensitive_literals_in_range" ||
        e.redaction.contextIncluded,
    )
  )
    throw new Error("Illustration evidence not reviewed or scope differs");
  return {
    scope: input.identity,
    revision: `manual:${input.provenance.sourceCommit}`,
    provenance: "manual_source_review",
    nodes: input.nodes
      .filter((n) => n.id in nodeKinds)
      .map((n) => ({
        ...n,
        kind: nodeKinds[n.id],
        producerVersion: "manual-source-review/1",
      })),
    edges: input.edges
      .filter((e) => e.id in edgeKinds)
      .map((e) => ({
        ...e,
        kind: edgeKinds[e.id],
        resolution:
          e.resolution === "resolved"
            ? "resolved"
            : e.resolution === "partial"
              ? "partial"
              : "unknown",
        basis: "source",
        policyRef: null,
      })),
    evidence: input.evidence,
    findings: input.findings.map((f) => ({
      id: f.id,
      label: input.nodes.find((n) => n.id === "n-finding")?.label ?? f.id,
      status: f.status,
      severity: f.severity,
      confidence: f.confidence,
      reasons: [f.severityBasis, f.confidenceBasis, ...f.coverageWarnings],
      evidenceRefs: [
        ...new Set([
          ...f.evidenceRefs,
          ...f.trace.flatMap(
            (id) => input.edges.find((e) => e.id === id)?.evidenceRefs ?? [],
          ),
        ]),
      ],
      nodeIds: Object.keys(nodeKinds),
      policyRef: f.policyRef,
    })),
    policies: input.policyInputs.map((p) => ({
      id: p.id,
      status: "assumption",
      version: p.version,
      hash: p.contentHash,
      source: p.resource,
      description: p.proposedRule,
      evidenceRefs: p.evidenceRefs,
      limitations: p.limitations,
    })),
    diagnostics: [
      ...input.diagnostics.map((d, i) => ({
        ...d,
        id: `manual-diagnostic-${i}`,
        evidenceRefs: [],
      })),
      ...input.unresolvedRelations.map((g) => ({
        id: g.id,
        code: "UNKNOWN_RUNTIME_GUARDS",
        message: g.resolutionReason,
        evidenceRefs: g.evidenceRefs,
        nodeId: g.fromId,
      })),
    ],
    coverage: {
      status: "manual / partial",
      summary: `Просмотрено вручную ${input.coverage.filesVisited} файлов. Scanner coverage отсутствует.`,
      warnings: [
        ...input.coverage.skippedScopes,
        ...input.coverage.unresolvedSymbols,
      ],
    },
  };
}
export const pickupPreview = adaptPickupIllustration(illustration);
export function PickupPreview({
  loadState = "partial",
  layout,
  onLayoutChange,
  onRetry = () => {},
}: {
  loadState?: LoadState;
  layout?: SecurityMapLayout;
  onLayoutChange?: (layout: SecurityMapLayout) => void;
  onRetry?: () => void;
}) {
  const fullscreen =
    typeof window !== "undefined" &&
    new URLSearchParams(window.location.search).has("fullscreen");
  return (
    <div
      style={
        fullscreen
          ? { position: "absolute", top: 0, left: 0, right: 0, zIndex: 50, height: "100vh" }
          : { height: "calc(100vh - 150px)", minHeight: 560 }
      }
    >
      <MapView
        key={scopeKey(pickupPreview.scope)}
        model={pickupPreview}
        loadState={loadState}
        layout={layout}
        onLayoutChange={onLayoutChange}
        onRetry={onRetry}
      />
    </div>
  );
}
