import type {
  EvidenceRef,
  Finding,
  Location,
  RunScope,
  SecurityMapDto,
} from "../../../docs/data-contracts/contracts";
import type { MapViewModel, SourceState, ViewNode } from "./types";

export const scopeKey = (s: RunScope) =>
  JSON.stringify([s.workspaceId, s.runId, s.snapshotId]);
export const sameScope = (a: RunScope, b: RunScope) =>
  scopeKey(a) === scopeKey(b);
const unique = (values: readonly string[]) =>
  new Set(values).size === values.length;
const sameLocation = (a: Location, b: Location) =>
  a.path === b.path &&
  a.line === b.line &&
  a.column === b.column &&
  a.endLine === b.endLine &&
  a.endColumn === b.endColumn &&
  a.byteStart === b.byteStart &&
  a.byteEnd === b.byteEnd;
const hash = (s: string) => /^[a-f0-9]{64}$/.test(s);
const uuid = (s: string) =>
  /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(s);

/**
 * Gate for one evidence fragment. Shared by the production map and the file
 * graph so a ready fragment is only shown when it belongs to the same scope,
 * points at the saved evidence row, and repeats its hash, full coordinates and
 * a nonempty masking marker.
 */
export function validateSourceContent(
  scope: RunScope,
  evidence: EvidenceRef | undefined,
  source: SourceState,
): string | null {
  if (source.status === "idle") return null;
  if (!sameScope(scope, source.request)) return "SOURCE_SCOPE_MISMATCH";
  if (!evidence) return "SOURCE_REFERENCE_MISMATCH";
  if (
    source.status === "ready" &&
    (source.sourceHash !== evidence.sourceHash ||
      !sameLocation(source.location, evidence.location) ||
      !source.redactionVersion)
  )
    return "SOURCE_CONTENT_MISMATCH";
  return null;
}

/** A still validates JSON Schema and authorization; this gate prevents mixed response scopes and broken refs. */
export function validateProduction(
  scope: RunScope,
  data: SecurityMapDto,
  findings: readonly Finding[],
  source?: SourceState,
): string | null {
  if (
    !sameScope(scope, data.identity) ||
    [
      ...data.nodes,
      ...data.edges,
      ...data.evidence,
      ...data.diagnostics,
      ...findings,
    ].some((item) => !sameScope(scope, item)) ||
    data.policyInputs.some((p) => p.workspaceId !== scope.workspaceId)
  )
    return "SCOPE_MISMATCH";
  if (source && source.status !== "idle" && !sameScope(scope, source.request))
    return "SOURCE_SCOPE_MISMATCH";
  if (
    data.schemaVersion !== "0.1" ||
    ![
      scope.workspaceId,
      scope.runId,
      scope.snapshotId,
      data.identity.workflowRevisionId,
    ].every(uuid) ||
    ![
      data.identity.artifactHash,
      data.identity.snapshotManifestHash,
      data.bundleHash,
      data.signature.parserHash,
      data.signature.rulesHash,
      data.signature.advisoryHash,
      data.signature.scanConfigHash,
    ].every(hash) ||
    ![
      data.signature.parserVersion,
      data.signature.rulesVersion,
      data.signature.advisoryVersion,
      data.signature.configVersion,
      data.signature.hmacKeyVersion,
    ].every(Boolean)
  )
    return "INVALID_PRODUCTION_IDENTITY";
  const nodeIds = new Set(data.nodes.map((n) => n.id));
  const evidenceIds = new Set(data.evidence.map((e) => e.id));
  const findingIds = new Set(findings.map((f) => f.id));
  const policyIds = new Set(data.policyInputs.map((p) => p.id));
  if (
    ![
      data.nodes,
      data.edges,
      data.evidence,
      data.diagnostics,
      findings,
      data.policyInputs,
    ].every((items) => unique(items.map((i) => i.id))) ||
    !unique(data.findingRefs)
  )
    return "DUPLICATE_ID";
  if (
    data.nodes.some((n) => n.evidenceRefs.some((id) => !evidenceIds.has(id))) ||
    data.edges.some(
      (e) =>
        !nodeIds.has(e.fromId) ||
        !nodeIds.has(e.toId) ||
        e.evidenceRefs.some((id) => !evidenceIds.has(id)) ||
        (e.policyRef !== null && !policyIds.has(e.policyRef)),
    ) ||
    data.diagnostics.some(
      (d) => d.evidenceId !== null && !evidenceIds.has(d.evidenceId),
    )
  )
    return "BROKEN_GRAPH_REFERENCE";
  if (
    data.findingRefs.some((id) => !findingIds.has(id)) ||
    findings.some(
      (f) =>
        !data.findingRefs.includes(f.id) ||
        [
          f.primaryEvidenceId,
          ...f.relatedEvidenceIds,
          ...f.traceEvidenceIds,
        ].some((id) => !evidenceIds.has(id)) ||
        f.graphNodeIds.some((id) => !nodeIds.has(id)),
    ) ||
    data.findingNodeLinks.some(
      (l) => !findingIds.has(l.findingId) || !nodeIds.has(l.nodeId),
    )
  )
    return "BROKEN_FINDING_REFERENCE";
  if (
    findings.some(
      (f) =>
        !sameLocation(
          f.primaryLocation,
          data.evidence.find((e) => e.id === f.primaryEvidenceId)!.location,
        ),
    )
  )
    return "FINDING_LOCATION_MISMATCH";
  if (
    findings.some((f) => {
      const links = data.findingNodeLinks
        .filter((l) => l.findingId === f.id)
        .map((l) => l.nodeId);
      return (
        !unique(links) ||
        links.length !== f.graphNodeIds.length ||
        f.graphNodeIds.some((id) => !links.includes(id))
      );
    })
  )
    return "FINDING_LINK_MISMATCH";
  const policy = data.signature.domainPolicyRevisionId;
  if (
    policy === null
      ? data.signature.domainPolicyVersion !== null ||
        data.signature.domainPolicyHash !== null ||
        data.policyInputs.length > 0
      : !data.policyInputs.some(
          (p) =>
            p.id === policy &&
            p.version === data.signature.domainPolicyVersion &&
            p.hash === data.signature.domainPolicyHash,
        )
  )
    return "POLICY_SIGNATURE_MISMATCH";
  if (source && source.status !== "idle") {
    const issue = validateSourceContent(
      scope,
      data.evidence.find((e) => e.id === source.request.evidenceId),
      source,
    );
    if (issue) return issue;
  }
  return null;
}

const FINDING_TITLES: Record<string, string> = {
  "SECRET-BEARER-TOKEN": "Токен доступа в коде",
  "SECRET-API-KEY": "API-ключ в коде",
  "SECRET-PASSWORD-LITERAL": "Пароль в коде",
  "CF-SQL-PARAM-INTERPOLATION": "SQL-запрос собирается из параметра",
  "CF-ROUTE-UNAUTH-PII": "Маршрут без проверки доступа читает персональные данные",
  "CF-OWNER-CHECK-UNPROVEN": "Не подтверждена проверка владельца",
  "CG26-SEC-004": "Секрет как запасной вариант",
  "CG26-SEC-001": "Пароль в исходном коде",
  "CG26-SEC-002": "API-ключ в исходном коде",
  "CG26-SEC-005": "Токен восстанавливается из кода",
  "CG26-SEC-003": "Токен хранится открыто",
  "CG26-SCA-001": "Уязвимая зависимость",
  "CG26-SAST-003": "Cookie уходит на чужой сайт",
  "CG26-SAST-004": "Нет роли, а данные других пользователей",
  "CG26-SAST-002": "Отключена проверка имени хоста",
  "CG26-SAST-001": "Параметр меняет SQL-запрос",
};

const findingTitle = (ruleId: string, confidence: string, status: string): string => {
  if (ruleId === "CG26-SAST-005") {
    return confidence === "high" && status === "confirmed"
      ? "Можно запросить чужой объект"
      : "Возможное чтение чужого объекта";
  }
  if (FINDING_TITLES[ruleId]) return FINDING_TITLES[ruleId];
  if (ruleId.startsWith("SECRET-")) return "Секрет в коде";
  if (ruleId.startsWith("CF-SQL")) return "SQL-запрос без плейсхолдера";
  if (ruleId.startsWith("CF-ROUTE")) return "Маршрут без проверки доступа";
  if (ruleId.startsWith("CF-OWNER")) return "Не подтверждена проверка владельца";
  if (ruleId.startsWith("CG26-SCA")) return "Уязвимая зависимость";
  if (ruleId.startsWith("CG26-SAST")) return "Проблема в обработке запроса";
  if (ruleId.startsWith("CG26-SEC")) return "Секрет в коде или конфигурации";
  return "Найденная проблема";
};

export function productionModel(
  data: SecurityMapDto,
  findings: readonly Finding[],
): MapViewModel {
  const evidencePath = new Map(data.evidence.map((item) => [item.id, item.location.path]));
  const syntheticNodes: ViewNode[] = [];
  const syntheticByPath = new Map<string, string>();
  const syntheticNodeFor = (finding: Finding): string => {
    const path = evidencePath.get(finding.primaryEvidenceId) || "файл не указан";
    const known = syntheticByPath.get(path);
    if (known) return known;
    const id = `file:${path}`;
    syntheticByPath.set(path, id);
    syntheticNodes.push({
      id,
      kind: "file",
      label: path,
      symbolKey: `file:${path}`,
      evidenceRefs: [],
      producerVersion: "nedra-map/file-group",
    });
    return id;
  };
  const findingRefs = (finding: Finding): string[] => [
    ...new Set([
      finding.primaryEvidenceId,
      ...finding.relatedEvidenceIds,
      ...finding.traceEvidenceIds,
    ]),
  ];
  const viewFindings = findings.map((f) => {
    const explicit = data.findingNodeLinks
      .filter((l) => l.findingId === f.id)
      .map((l) => l.nodeId);
    return {
      id: f.id,
      label: findingTitle(f.ruleId, f.confidence, f.status),
      status: f.status.toLowerCase(),
      severity: f.severity.toLowerCase(),
      confidence: f.confidence.toLowerCase(),
      reasons: [f.severityBasis, f.confidenceBasis, ...f.coverageWarnings],
      evidenceRefs: findingRefs(f),
      nodeIds: explicit.length ? explicit : [syntheticNodeFor(f)],
    };
  });
  return {
    scope: data.identity,
    revision: data.bundleHash,
    provenance: "analyzer",
    nodes: [...data.nodes, ...syntheticNodes],
    edges: data.edges,
    evidence: data.evidence,
    findings: viewFindings,
    policies: data.policyInputs.map((p) => ({
      id: p.id,
      status: p.approvalStatus,
      version: p.version,
      hash: p.hash,
      source: p.sourceRef,
      description: JSON.stringify(p.resources, null, 2),
      evidenceRefs: [],
      limitations:
        p.approvalStatus === "assumption" ? ["Политика не утверждена."] : [],
    })),
    diagnostics: data.diagnostics.map((d) => ({
      ...d,
      evidenceRefs: d.evidenceId ? [d.evidenceId] : [],
    })),
    coverage: {
      status: data.coverage.status,
      summary: `${data.coverage.filesVisited} из ${data.coverage.filesTotal} файлов проверено; правил: ${data.coverage.rulesApplied.length}`,
      warnings: [
        ...data.coverage.skippedScopes,
        ...data.coverage.unresolvedSymbols,
        ...data.coverage.limitsReached,
        ...data.coverage.scopes
          .filter((s) => s.status !== "checked")
          .map((s) => `${s.path} · ${s.ruleId}: ${s.status} — ${s.reason}`),
        ...data.coverage.parserErrors.map((id) => `Ошибка parser: ${id}`),
      ],
    },
  };
}
