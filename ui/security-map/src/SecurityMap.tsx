import { Failure, MapView } from "./MapView";
import {
  productionModel,
  sameScope,
  scopeKey,
  validateProduction,
} from "./model";
import type { SecurityMapProps } from "./types";

export function SecurityMap(props: SecurityMapProps) {
  const { identity, data, findings, loadState, onRetry } = props;
  if (loadState === "loading")
    return (
      <section className="security-map sm-state" role="status">
        <h2>Загружаем карту…</h2>
        <p>run: {identity.runId}</p>
      </section>
    );
  if (loadState === "error")
    return (
      <Failure
        code={
          props.error?.code === "source_unavailable"
            ? "SOURCE_UNAVAILABLE"
            : "MAP_LOAD_FAILED"
        }
        onRetry={onRetry}
      />
    );
  if (!data) return <Failure code="MAP_DATA_MISSING" onRetry={onRetry} />;
  let model: ReturnType<typeof productionModel>;
  try {
    const issue = validateProduction(identity, data, findings, props.sourceState);
    if (issue) return <Failure code={issue} onRetry={onRetry} />;
    model = productionModel(data, findings);
  } catch {
    return <Failure code="MAP_CONTRACT_MISMATCH" onRetry={onRetry} />;
  }
  if (props.layout && !sameScope(identity, props.layout.scope))
    return <Failure code="LAYOUT_SCOPE_MISMATCH" onRetry={onRetry} />;
  if (props.selection && !sameScope(identity, props.selection))
    return <Failure code="SELECTION_SCOPE_MISMATCH" onRetry={onRetry} />;
  if (
    loadState === "empty" &&
    (data.nodes.length || data.edges.length || data.findingRefs.length)
  )
    return <Failure code="EMPTY_STATE_CONFLICT" onRetry={onRetry} />;
  return (
    <MapView
      key={`${scopeKey(identity)}:${data.bundleHash}`}
      {...props}
      model={model}
      onSelect={(selection) => {
        if (selection.kind === "finding") {
          const f = findings.find((f) => f.id === selection.id);
          if (f) props.onSelectFinding?.(f);
        }
        if (selection.kind === "node") {
          const n = data.nodes.find((n) => n.id === selection.id);
          if (n) props.onSelectNode?.(n);
        }
        if (selection.kind === "edge") {
          const e = data.edges.find((e) => e.id === selection.id);
          if (e) props.onSelectEdge?.(e);
        }
      }}
    />
  );
}
