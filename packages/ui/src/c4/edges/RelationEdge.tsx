"use client";

/**
 * Styled C4 relation edge — a smooth-step arrow, solid inside the system and
 * dashed to an external, with a paper chip reading "verb · count".
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
import { relationLabel } from "../relation-label";

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
  const edgeData = data as C4EdgeData | undefined;
  const relation = edgeData?.relation;

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

  const label = relation ? relationLabel(relation) : "";
  // Edges laid out before the flag existed keep the old all-dashed look.
  const dashed = edgeData?.toExternal !== false && !selected;

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        {...(markerEnd ? { markerEnd } : {})}
        style={{ stroke, strokeWidth, strokeDasharray: dashed ? "6 4" : "none" }}
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
