"use client";

/**
 * Styled C4 relation edge — blueprint dashed smooth-step line with an
 * optional paper chip showing edge count and dominant edge type.
 */

import { memo } from "react";
import {
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
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

  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    borderRadius: 8,
  });

  const stroke = selected ? THEME.selection.ring : "var(--color-diagram-edge)";
  // Tighter coupling reads as a heavier line. Falls back to the pair-count
  // when the relation predates the coupling field.
  const couplingWidth: Record<string, number> = { tight: 2.25, moderate: 1.75, loose: 1.25 };
  const strokeWidth = relation
    ? (couplingWidth[relation.coupling ?? ""] ?? (relation.edge_count > 5 ? 2 : 1.25))
    : 1.25;

  // The backend now ships a readable verb ("imports", "co-changes"); keep a
  // bare-type fallback only for older payloads that predate it.
  const label = relation
    ? relation.label || (relation.edge_types[0] ?? "")
    : "";

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        {...(markerEnd ? { markerEnd } : {})}
        style={{ stroke, strokeWidth, strokeDasharray: selected ? "none" : "6 4" }}
      />
      {label && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: "absolute",
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              background: "var(--color-bg-surface)",
              color: THEME.text.secondary,
              padding: "2px 7px",
              borderRadius: 6,
              fontFamily: "var(--font-mono, ui-monospace, monospace)",
              fontSize: 9.5,
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
