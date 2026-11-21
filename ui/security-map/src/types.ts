import type {
  EvidenceRef,
  Finding,
  RunScope,
  SecurityMapDto,
  Location,
} from "../../../docs/data-contracts/contracts";

export type LoadState = "loading" | "ready" | "empty" | "partial" | "error";
export type EvidenceRequest = RunScope & { evidenceId: string };
export type Selection = { kind: "node" | "edge" | "finding"; id: string };
export type ScopedSelection = RunScope & { value: Selection | null };
export type SecurityMapLayout = {
  scope: RunScope;
  viewId: string;
  nodePositions: Record<string, { x: number; y: number }>;
  viewport?: { x: number; y: number; zoom: number };
};
export type SourceState =
  | { status: "idle" }
  | { status: "loading"; request: EvidenceRequest }
  | { status: "error" | "unavailable"; request: EvidenceRequest; code: string }
  | {
      status: "ready";
      request: EvidenceRequest;
      sourceHash: string;
      location: Location;
      maskedFragment: string;
      redactionVersion: string;
    };

export type SecurityMapProps = {
  /** A owns the active scope independently of the arriving response. */
  identity: RunScope;
  data: SecurityMapDto | null;
  findings: readonly Finding[];
  loadState: LoadState;
  logoUrl?: string;
  layout?: SecurityMapLayout;
  selection?: ScopedSelection | null;
  sourceState?: SourceState;
  error?: { code: string };
  onSelectFinding?: (finding: Finding) => void;
  onSelectNode?: (node: SecurityMapDto["nodes"][number]) => void;
  onSelectEdge?: (edge: SecurityMapDto["edges"][number]) => void;
  onOpenEvidence: (request: EvidenceRequest) => void;
  onLayoutChange?: (layout: SecurityMapLayout) => void;
  onRetry: () => void;
  onRetrySource?: (request: EvidenceRequest) => void;
};

export type ViewNode = {
  id: string;
  kind: string;
  label: string;
  symbolKey: string;
  evidenceRefs: string[];
  producerVersion: string;
};
export type ViewEdge = {
  id: string;
  fromId: string;
  toId: string;
  kind: string;
  evidenceRefs: string[];
  resolution: "resolved" | "partial" | "unknown";
  resolutionReason: string;
  producerVersion: string;
  basis: "source" | "policy_assumption";
  policyRef: string | null;
};
export type ViewFinding = {
  id: string;
  label: string;
  status: string;
  severity: string;
  confidence: string;
  reasons: string[];
  evidenceRefs: string[];
  nodeIds: string[];
  policyRef?: string;
};
export type ViewPolicy = {
  id: string;
  status: "assumption" | "approved";
  version: string;
  hash: string;
  source: string;
  description: string;
  evidenceRefs: string[];
  limitations: string[];
};
export type ViewDiagnostic = {
  id: string;
  code: string;
  message: string;
  evidenceRefs: string[];
  nodeId?: string;
};
export type ViewEvidence = Pick<
  EvidenceRef,
  "id" | "location" | "sourceHash" | "producerVersion" | "redactedSnippet"
>;
export type MapViewModel = {
  scope: RunScope;
  revision: string;
  provenance: "analyzer" | "manual_source_review";
  nodes: ViewNode[];
  edges: ViewEdge[];
  findings: ViewFinding[];
  evidence: ViewEvidence[];
  policies: ViewPolicy[];
  diagnostics: ViewDiagnostic[];
  coverage: { status: string; summary: string; warnings: string[] };
};
