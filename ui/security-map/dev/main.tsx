import { useState } from "react";
import { createRoot } from "react-dom/client";
import { PickupPreview, pickupPreview } from "../src/preview";
import { SecurityMap } from "../src";
import { MapView } from "../src/MapView";
import type { MapViewModel, SecurityMapLayout } from "../src/types";
import "./style.css";

/**
 * Dev-only layout probe, not analyzer output and not a product fixture:
 * it reproduces the graph shape the extractor emits (shared matcher, role and
 * permitAll rules, several routes, an unknown transition and a backward
 * @PreAuthorize edge) so lanes and crossings can be checked in a browser.
 */
const probeEvidence = (
  id: string,
  path: string,
  line: number,
  redactedSnippet: string,
) => ({
  id,
  location: {
    path,
    line,
    column: 1,
    endLine: line,
    endColumn: 1,
    byteStart: 0,
    byteEnd: 0,
  },
  sourceHash: "0".repeat(64),
  producerVersion: "layout-probe/dev",
  redactedSnippet,
});

const probeScope = {
  workspaceId: "probe-workspace",
  runId: "probe-run",
  snapshotId: "probe-snapshot",
};

const probeModel: MapViewModel = {
  scope: probeScope,
  revision: "layout-probe",
  provenance: "manual_source_review",
  nodes: [
    { id: "actor-anon", kind: "actor", label: "Анонимный клиент", symbolKey: "actor:anonymous", evidenceRefs: ["ev-1"], producerVersion: "probe" },
    { id: "actor-admin", kind: "actor", label: "Роль ADMIN", symbolKey: "actor:role:ADMIN", evidenceRefs: ["ev-2"], producerVersion: "probe" },
    { id: "matcher-api", kind: "matcher", label: "/api/**", symbolKey: "matcher:ANY:/api/**", evidenceRefs: ["ev-3"], producerVersion: "probe" },
    { id: "chain-permit", kind: "security_chain", label: "permitAll /api/**", symbolKey: "chain:permitAll:/api/**", evidenceRefs: ["ev-3"], producerVersion: "probe" },
    { id: "chain-admin", kind: "guard", label: "Требуется роль ADMIN", symbolKey: "guard:role:ADMIN:/api/users", evidenceRefs: ["ev-2"], producerVersion: "probe" },
    { id: "route-export", kind: "route", label: "GET /api/export", symbolKey: "route:GET:/api/export", evidenceRefs: ["ev-4"], producerVersion: "probe" },
    { id: "route-users", kind: "route", label: "POST /api/users", symbolKey: "route:POST:/api/users", evidenceRefs: ["ev-5"], producerVersion: "probe" },
    { id: "route-health", kind: "route", label: "GET /health", symbolKey: "route:GET:/health", evidenceRefs: ["ev-6"], producerVersion: "probe" },
    { id: "guard-pre", kind: "guard", label: "@PreAuthorize на export", symbolKey: "guard:method-security:Export.export", evidenceRefs: ["ev-4"], producerVersion: "probe" },
    { id: "fn-export", kind: "function", label: "ExportController.export", symbolKey: "function:Export.export", evidenceRefs: ["ev-4"], producerVersion: "probe" },
    { id: "fn-users", kind: "function", label: "UsersController.create", symbolKey: "function:Users.create", evidenceRefs: ["ev-5"], producerVersion: "probe" },
    { id: "fn-health", kind: "function", label: "HealthController.status", symbolKey: "function:Health.status", evidenceRefs: [], producerVersion: "probe" },
    { id: "sink-export", kind: "sink", label: "jdbcTemplate.query", symbolKey: "sink:Export:14", evidenceRefs: ["ev-7"], producerVersion: "probe" },
    { id: "sink-users", kind: "sink", label: "usersRepository.save", symbolKey: "sink:Users:9", evidenceRefs: ["ev-8"], producerVersion: "probe" },
    { id: "src-param", kind: "source", label: "Параметр запроса q", symbolKey: "source:Export:q", evidenceRefs: ["ev-7"], producerVersion: "probe" },
    { id: "data-tickets", kind: "data", label: "tickets: ticket_code, customer_id", symbolKey: "data:tickets", evidenceRefs: ["ev-9"], producerVersion: "probe" },
    { id: "data-users", kind: "data", label: "users: email, role", symbolKey: "data:users", evidenceRefs: ["ev-10"], producerVersion: "probe" },
  ],
  edges: [
    { id: "edge-a", fromId: "actor-anon", toId: "matcher-api", kind: "matches", evidenceRefs: ["ev-1"], resolution: "resolved", resolutionReason: "Анонимный клиент проходит matcher.", producerVersion: "probe", basis: "source", policyRef: null },
    { id: "edge-b", fromId: "actor-admin", toId: "matcher-api", kind: "matches", evidenceRefs: ["ev-2"], resolution: "resolved", resolutionReason: "Клиент с ролью ADMIN проходит matcher.", producerVersion: "probe", basis: "source", policyRef: null },
    { id: "edge-c", fromId: "matcher-api", toId: "chain-permit", kind: "permits", evidenceRefs: ["ev-3"], resolution: "resolved", resolutionReason: "Первое правило разрешает все /api/**.", producerVersion: "probe", basis: "source", policyRef: null },
    { id: "edge-d", fromId: "matcher-api", toId: "chain-admin", kind: "requires", evidenceRefs: ["ev-2"], resolution: "resolved", resolutionReason: "Для /api/users требуется роль ADMIN.", producerVersion: "probe", basis: "source", policyRef: null },
    { id: "edge-e", fromId: "chain-permit", toId: "route-export", kind: "permits", evidenceRefs: ["ev-3", "ev-4"], resolution: "resolved", resolutionReason: "Правило покрывает маршрут выгрузки.", producerVersion: "probe", basis: "source", policyRef: null },
    { id: "edge-f", fromId: "chain-admin", toId: "route-users", kind: "requires", evidenceRefs: ["ev-2", "ev-5"], resolution: "resolved", resolutionReason: "Роль проверяется до обработчика.", producerVersion: "probe", basis: "source", policyRef: null },
    { id: "edge-g", fromId: "route-export", toId: "guard-pre", kind: "requires", evidenceRefs: ["ev-4"], resolution: "resolved", resolutionReason: "На методе объявлен @PreAuthorize; runtime не проверялся.", producerVersion: "probe", basis: "source", policyRef: null },
    { id: "edge-h", fromId: "route-export", toId: "fn-export", kind: "routes_to", evidenceRefs: ["ev-4"], resolution: "resolved", resolutionReason: "Spring mapping связан с обработчиком.", producerVersion: "probe", basis: "source", policyRef: null },
    { id: "edge-i", fromId: "route-users", toId: "fn-users", kind: "routes_to", evidenceRefs: ["ev-5"], resolution: "resolved", resolutionReason: "Spring mapping связан с обработчиком.", producerVersion: "probe", basis: "source", policyRef: null },
    { id: "edge-j", fromId: "route-health", toId: "fn-health", kind: "routes_to", evidenceRefs: [], resolution: "partial", resolutionReason: "Обработчик найден, отдельного фрагмента привязки нет.", producerVersion: "probe", basis: "source", policyRef: null },
    { id: "edge-k", fromId: "fn-export", toId: "sink-export", kind: "calls", evidenceRefs: ["ev-7"], resolution: "resolved", resolutionReason: "Прямой вызов SQL в теле функции.", producerVersion: "probe", basis: "source", policyRef: null },
    { id: "edge-l", fromId: "fn-users", toId: "sink-users", kind: "calls", evidenceRefs: ["ev-8"], resolution: "resolved", resolutionReason: "Прямой вызов репозитория.", producerVersion: "probe", basis: "source", policyRef: null },
    { id: "edge-m", fromId: "src-param", toId: "sink-export", kind: "flows_to", evidenceRefs: ["ev-7"], resolution: "resolved", resolutionReason: "Параметр интерполируется в SQL без плейсхолдера.", producerVersion: "probe", basis: "source", policyRef: null },
    { id: "edge-n", fromId: "sink-export", toId: "data-tickets", kind: "reads", evidenceRefs: ["ev-9"], resolution: "resolved", resolutionReason: "SQL читает таблицу tickets.", producerVersion: "probe", basis: "source", policyRef: null },
    { id: "edge-o", fromId: "sink-users", toId: "data-users", kind: "writes", evidenceRefs: ["ev-10"], resolution: "resolved", resolutionReason: "Сохранение пишет в users.", producerVersion: "probe", basis: "source", policyRef: null },
    { id: "edge-p", fromId: "actor-anon", toId: "route-health", kind: "matches", evidenceRefs: [], resolution: "unknown", resolutionReason: "Нет применимого статического правила; runtime неизвестен.", producerVersion: "probe", basis: "source", policyRef: null },
  ],
  findings: [
    { id: "finding-1", label: "Публичная выгрузка заявок", status: "confirmed", severity: "high", confidence: "medium", reasons: ["permitAll покрывает маршрут выгрузки.", "SQL читает персональные поля."], evidenceRefs: ["ev-3", "ev-9"], nodeIds: ["route-export", "fn-export", "sink-export", "data-tickets"] },
    { id: "finding-2", label: "Запись в users без проверки владельца", status: "potential", severity: "critical", confidence: "low", reasons: ["Роль проверяется, владелец объекта — нет."], evidenceRefs: ["ev-8"], nodeIds: ["route-users", "fn-users", "data-users"] },
    { id: "finding-3", label: "Параметр меняет SQL-запрос", status: "confirmed", severity: "medium", confidence: "high", reasons: ["Интерполяция параметра в SQL."], evidenceRefs: ["ev-7"], nodeIds: ["src-param", "sink-export"] },
    { id: "finding-4", label: "Нет фрагмента для health-маршрута", status: "inconclusive", severity: "info", confidence: "low", reasons: ["Связь обработчика подтверждена частично."], evidenceRefs: [], nodeIds: [] },
  ],
  evidence: [
    probeEvidence("ev-1", "src/main/kotlin/config/Security.kt", 17, "permitAll()"),
    probeEvidence("ev-2", "src/main/kotlin/config/Security.kt", 22, "hasRole(\"ADMIN\")"),
    probeEvidence("ev-3", "src/main/kotlin/config/Security.kt", 12, "/api/**"),
    probeEvidence("ev-4", "src/main/kotlin/export/ExportController.kt", 8, "@GetMapping(\"/api/export\")"),
    probeEvidence("ev-5", "src/main/kotlin/users/UsersController.kt", 9, "@PostMapping(\"/api/users\")"),
    probeEvidence("ev-6", "src/main/kotlin/health/HealthController.kt", 4, "@GetMapping(\"/health\")"),
    probeEvidence("ev-7", "src/main/kotlin/export/ExportController.kt", 14, "jdbcTemplate.query(sql)"),
    probeEvidence("ev-8", "src/main/kotlin/users/UsersController.kt", 11, "usersRepository.save(user)"),
    probeEvidence("ev-9", "src/main/resources/schema.sql", 3, "create table tickets"),
    probeEvidence("ev-10", "src/main/resources/schema.sql", 9, "create table users"),
  ],
  policies: [],
  diagnostics: [
    { id: "diag-1", code: "CF-STATIC-ONLY", message: "Только статический анализ; runtime не проверялся.", evidenceRefs: [] },
  ],
  coverage: {
    status: "complete",
    summary: "Проверка раскладки: 17 узлов, 16 связей, 4 проблемы.",
    warnings: [],
  },
};

function Demo() {
  const [mode, setMode] = useState("pickup");
  const [revision, setRevision] = useState(0);
  const [layout, setLayout] = useState<SecurityMapLayout>();
  const [layouts, setLayouts] = useState(0);
  const [retries, setRetries] = useState(0);
  const identity = {
    workspaceId: "preview-only",
    runId: "no-analyzer-run",
    snapshotId: "manual-review-only",
  };
  const retry = () => {
    setRetries((n) => n + 1);
    setMode("pickup");
    setRevision((n) => n + 1);
  };
  const empty: MapViewModel = {
    ...pickupPreview,
    scope: { ...pickupPreview.scope, runId: "illustration-empty-view" },
    nodes: [],
    edges: [],
    findings: [],
    evidence: [],
    policies: [],
    diagnostics: [],
  };
  const changed: MapViewModel = {
    ...pickupPreview,
    scope: { ...pickupPreview.scope, runId: "illustration-changed-identity" },
  };
  return (
    <main className="sm-demo">
      <p>
        Scope refusal — намеренно неполный отрицательный вход, без вымышленных
        signature/hash. Изолированный preview компонента C. Данные — ручной
        reviewed pickup; backend и анализатор не запускаются.
      </p>
      <div className="sm-toolbar">
        <label htmlFor="mode">Сценарий проверки </label>
        <select
          id="mode"
          value={mode}
          onChange={(e) => {
            setMode(e.target.value);
            setLayout(undefined);
          }}
        >
          <option value="pickup">Pickup · partial manual review</option>
          <option value="loading">Loading</option>
          <option value="empty">Empty (manual view)</option>
          <option value="error">Error + retry</option>
          <option value="scope">Production workspace refusal</option>
          <option value="scope-run">Production run refusal</option>
          <option value="scope-snapshot">Production snapshot refusal</option>
          <option value="changed">Changed identity (manual view)</option>
          <option value="unresolved">
            Unknown edge / complete summary (view probe)
          </option>
          <option value="probe">
            Форма реального графа · ветки и правило доступа (layout probe)
          </option>
        </select>
        <button
          onClick={() => {
            setMode("pickup");
            setLayout(undefined);
            setRevision((n) => n + 1);
          }}
        >
          Сбросить preview
        </button>
        <output>
          Layout callbacks: {layouts} · Retry: {retries}
        </output>
      </div>
      {mode === "pickup" ? (
        <PickupPreview
          key={revision}
          layout={layout}
          onLayoutChange={(l) => {
            setLayout(l);
            setLayouts((n) => n + 1);
          }}
          onRetry={retry}
        />
      ) : mode === "probe" ? (
        <>
          <div className="sm-provenance">
            Проверка раскладки — синтетическая форма графа, не факты анализа
          </div>
          <MapView
            key="probe"
            model={probeModel}
            loadState="partial"
            onRetry={retry}
            onLayoutChange={() => setLayouts((n) => n + 1)}
          />
        </>
      ) : mode === "empty" ? (
        <MapView key="empty" model={empty} loadState="empty" onRetry={retry} />
      ) : mode === "unresolved" ? (
        <MapView
          key="unresolved"
          model={{
            ...pickupPreview,
            edges: pickupPreview.edges.map((e, i) =>
              i === 0
                ? {
                    ...e,
                    resolution: "unknown",
                    resolutionReason:
                      "Проверка представления: unknown не скрывается полной summary; не новый факт анализа.",
                  }
                : e,
            ),
            coverage: { ...pickupPreview.coverage, status: "complete" },
          }}
          loadState="ready"
          onRetry={retry}
        />
      ) : mode === "changed" ? (
        <MapView
          key="changed"
          model={changed}
          loadState="partial"
          onRetry={retry}
        />
      ) : (
        <>
          <div className="sm-provenance">
            Ручной разбор исходника — не результат анализатора
          </div>
          <SecurityMap
            identity={identity}
            data={
              mode.startsWith("scope")
                ? ({
                    identity: {
                      ...identity,
                      ...(mode === "scope"
                        ? { workspaceId: "different-workspace" }
                        : mode === "scope-run"
                          ? { runId: "different-run" }
                          : { snapshotId: "different-snapshot" }),
                    },
                  } as never)
                : null
            }
            findings={[]}
            loadState={
              mode === "loading"
                ? "loading"
                : mode === "error"
                  ? "error"
                  : "ready"
            }
            onOpenEvidence={() => {}}
            onRetry={retry}
          />
        </>
      )}
    </main>
  );
}
createRoot(document.getElementById("root")!).render(<Demo />);
