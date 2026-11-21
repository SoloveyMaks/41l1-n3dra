import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { confidenceText, plural, severityText, statusText } from "./labels";

export type FindingNodeData = {
  label: string;
  severity: string;
  status: string;
  confidence: string;
  evidenceCount: number;
  anchorCount: number;
  anchored: boolean;
  dimmed?: boolean;
  neighbour?: boolean;
};

/**
 * Card for a real finding (backend Finding + findingNodeLinks). It is a view
 * of the finding, never a new graph fact: the dashed link starts at the node
 * the finding is attached to.
 */
export const FindingNode = memo(({ data, selected }: NodeProps) => {
  const finding = data as unknown as FindingNodeData;
  return (
    <div
      className={`sm-finding-card sm-finding-card--${finding.severity}${
        selected ? " is-selected" : ""
      }${finding.neighbour ? " is-neighbour" : ""}${
        finding.dimmed ? " is-dimmed" : ""
      }`}
      title={finding.label}
    >
      <Handle
        type="target"
        position={Position.Left}
        id="in"
        className="sm-handle sm-handle--in"
      />
      <div className="sm-finding-head">
        <span className="sm-finding-mark" aria-hidden="true">
          !
        </span>
        <span className="sm-finding-kind">Проблема</span>
        <span className={`sm-finding-severity sm-finding-severity--${finding.severity}`}>
          {severityText[finding.severity] ?? finding.severity}
        </span>
      </div>
      <div className="sm-finding-title">{finding.label}</div>
      <div className="sm-finding-meta">
        {statusText[finding.status] ?? finding.status} ·{" "}
        {confidenceText[finding.confidence] ?? finding.confidence}
      </div>
      <div className="sm-finding-foot">
        {finding.anchored ? (
          <>
            <span className={finding.evidenceCount ? "" : "is-missing"}>
              {finding.evidenceCount
                ? `${finding.evidenceCount} ${plural(finding.evidenceCount, "доказательство", "доказательства", "доказательств")}`
                : "нет фрагмента"}
            </span>
            <span className="sm-finding-anchors">
              привязано к {finding.anchorCount}{" "}
              {plural(finding.anchorCount, "узлу", "узлам", "узлам")}
            </span>
          </>
        ) : (
          <span className="is-missing">не привязано к узлу карты</span>
        )}
      </div>
    </div>
  );
});

FindingNode.displayName = "FindingNode";