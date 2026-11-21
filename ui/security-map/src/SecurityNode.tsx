import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { NodeIcon, nodeMeta, type NodeTone } from "./NodeIcon";
import { plural, severityText } from "./labels";

export type SecurityNodeData = {
  kind: string;
  label: string;
  symbolKey: string;
  evidenceCount: number;
  findingCount: number;
  severity?: string;
  tone: NodeTone;
  unknown?: boolean;
  neighbour?: boolean;
  dimmed?: boolean;
  searchMatch?: boolean;
  treeMode?: boolean;
};

export const SecurityNode = memo(({ data, selected }: NodeProps) => {
  const node = data as unknown as SecurityNodeData;
  const meta = nodeMeta(node.kind);
  const tone = node.tone ?? meta.tone;
  const hasEvidence = node.evidenceCount > 0;

  return (
    <div
      className={`sm-node sm-node--${tone}${selected ? " is-selected" : ""}${
        node.neighbour ? " is-neighbour" : ""
      }${node.dimmed ? " is-dimmed" : ""}${node.searchMatch ? " is-search-match" : ""}${
        node.treeMode ? " is-tree" : ""
      }`}
      data-kind={node.kind}
      data-tone={tone}
      title={node.label}
    >
      <Handle type="target" position={Position.Left} id="in" className="sm-handle sm-handle--in" />
      <Handle type="source" position={Position.Right} id="out" className="sm-handle sm-handle--out" />
      <Handle type="target" position={Position.Top} id="tree-in" className="sm-handle sm-handle--tree-in" />
      <Handle type="source" position={Position.Bottom} id="tree-out" className="sm-handle sm-handle--tree-out" />
      <div className="sm-node-head">
        <span className="sm-node-icon" aria-hidden="true">
          <NodeIcon kind={node.kind} size={17} />
        </span>
        <span className="sm-node-kind">{meta.label}</span>
        {node.findingCount > 0 ? (
          <span
            className={`sm-node-problem sm-node-problem--${node.severity ?? "info"}`}
            title={severityText[node.severity ?? ""] ?? "Есть проблема"}
          >
            {node.findingCount}
          </span>
        ) : null}
        {node.unknown ? (
          <span className="sm-node-unknown" title="Связь с этим узлом не установлена">
            ?
          </span>
        ) : null}
      </div>
      <div className="sm-node-title">{node.label}</div>
      {node.symbolKey ? (
        <div className="sm-node-symbol" title={node.symbolKey}>
          {node.symbolKey}
        </div>
      ) : null}
      <div className="sm-node-stats">
        <span className={hasEvidence ? "" : "is-missing"}>
          {hasEvidence ? (
            <>
              <b>{node.evidenceCount}</b>{" "}
              {plural(node.evidenceCount, "доказательство", "доказательства", "доказательств")}
            </>
          ) : (
            "нет фрагмента"
          )}
        </span>
        <span className={node.findingCount ? "has-findings" : ""}>
          {node.findingCount}{" "}
          {plural(node.findingCount, "проблема", "проблемы", "проблем")}
        </span>
      </div>
    </div>
  );
});

SecurityNode.displayName = "SecurityNode";