import { useState } from "react";
import { createRoot } from "react-dom/client";
import { PickupPreview, pickupPreview } from "../src/preview";
import { SecurityMap } from "../src";
import { MapView } from "../src/MapView";
import type { MapViewModel, SecurityMapLayout } from "../src/types";
import "./style.css";

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
