"use client";

/**
 * Styled C4 relation edge — a bezier line with an optional label pill
 * showing edge count and dominant edge type.
 */

import { memo } from "react";
import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  type EdgeProps,
} from "@xyflow/react";
import type { C4EdgeData } from "../types";

function RelationEdgeImpl(props: EdgeProps) {
  const {
    id,
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    markerEnd,
    data,
    selected,
  } = props;
  const relation = (data as C4EdgeData | undefined)?.relation;

  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  const stroke = selected ? "#fbbf24" : "#94a3b8";
  const strokeWidth = relation && relation.edge_count > 5 ? 2 : 1.25;

  const label = relation
    ? relation.label ||
      (relation.edge_count > 1
        ? `${relation.edge_count} ${relation.edge_types[0] ?? "calls"}`
        : (relation.edge_types[0] ?? ""))
    : "";

  return (
    <>
      <BaseEdge id={id} path={edgePath} {...(markerEnd ? { markerEnd } : {})} style={{ stroke, strokeWidth }} />
      {label && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: "absolute",
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              background: "rgba(15, 23, 42, 0.92)",
              color: "#e2e8f0",
              padding: "2px 6px",
              borderRadius: 4,
              fontSize: 10,
              fontWeight: 500,
              pointerEvents: "none",
              border: "1px solid rgba(148, 163, 184, 0.25)",
              whiteSpace: "nowrap",
            }}
            className="nodrag nopan"
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

export const RelationEdge = memo(RelationEdgeImpl);

export const c4EdgeTypes = {
  relation: RelationEdge,
};
