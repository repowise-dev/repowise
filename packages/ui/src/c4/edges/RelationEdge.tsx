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
import { THEME } from "../theme/theme-variables";
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

  const stroke = selected ? THEME.selection.ring : THEME.edge.default ?? "var(--color-text-tertiary)";
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
              background: THEME.surface.overlay,
              color: THEME.text.primary,
              padding: "2px 6px",
              borderRadius: 4,
              fontSize: 10,
              fontWeight: 500,
              pointerEvents: "none",
              border: `1px solid ${THEME.border.default}`,
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
