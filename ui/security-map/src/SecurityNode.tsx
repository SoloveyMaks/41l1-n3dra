import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { NodeIcon, nodeMeta, type NodeTone } from "./NodeIcon";

export type SecurityNodeData = {
  kind: string;
  label: string;
  symbolKey: string;
  evidenceCount: number;
  findingCount: number;
  tone: NodeTone;
  unknown?: boolean;
  neighbour?: boolean;
  dimmed?: boolean;
};

const plural = (n: number, one: string, few: string, many: string) => {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
};

export const SecurityNode = memo(({ data, selected }: NodeProps) => {
  const node = data as unknown as SecurityNodeData;
  const meta = nodeMeta(node.kind);
  const tone = node.tone ?? meta.tone;

  return (
    <div
      className={`sm-node sm-node--${tone}${selected ? " is-selected" : ""}${
        node.neighbour ? " is-neighbour" : ""
      }${node.dimmed ? " is-dimmed" : ""}`}
      data-kind={node.kind}
      data-tone={tone}
    >
      <Handle
        type="target"
        position={Position.Left}
        id="in"
        className="sm-handle sm-handle--in"
      />
      <div className="sm-node-head">
        <span className="sm-node-icon" aria-hidden="true">
          <NodeIcon kind={node.kind} size={17} />
        </span>
        <span className="sm-node-kind">{meta.label}</span>
        {node.unknown ? (
          <span className="sm-node-unknown" title="Есть неподтверждённая связь">
            ?
          </span>
        ) : null}
      </div>
      <div className="sm-node-title">{node.label}</div>
      <div className="sm-node-symbol">{node.symbolKey}</div>
      <div className="sm-node-stats">
        <span>
          <b>{node.evidenceCount}</b>{" "}
          {plural(node.evidenceCount, "evidence", "evidence", "evidence")}
        </span>
        <span className={node.findingCount ? "has-findings" : ""}>
          <b>{node.findingCount}</b>{" "}
          {plural(node.findingCount, "находка", "находки", "находок")}
        </span>
      </div>
      <Handle
        type="source"
        position={Position.Right}
        id="out"
        className="sm-handle sm-handle--out"
      />
    </div>
  );
});

SecurityNode.displayName = "SecurityNode";