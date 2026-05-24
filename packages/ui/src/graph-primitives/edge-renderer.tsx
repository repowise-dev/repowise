"use client";

import { memo } from "react";
import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  type EdgeProps,
} from "@xyflow/react";

export interface ArchEdgeData {
  edge_type: string;
  count: number;
  category?: string | undefined;
  isPortalEdge?: boolean | undefined;
}

export const EDGE_CATEGORY_COLORS: Record<string, string> = {
  structural: "#60a5fa",
  behavioral: "#4ade80",
  "data-flow": "#c084fc",
  dependencies: "#fb923c",
  semantic: "#94a3b8",
};

function getEdgeCategoryColor(category: string | undefined): string {
  if (!category) return "#94a3b8";
  return EDGE_CATEGORY_COLORS[category] ?? "#94a3b8";
}

export function computeEdgeStrokeWidth(count: number): number {
  return Math.min(1 + Math.log2(count + 1), 5);
}

function ArchEdgeRendererImpl(props: EdgeProps) {
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

  const edgeData = data as ArchEdgeData | undefined;
  const count = edgeData?.count ?? 1;
  const category = edgeData?.category;
  const isPortal = edgeData?.isPortalEdge ?? false;

  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  const baseColor = getEdgeCategoryColor(category);
  const stroke = selected ? "#fbbf24" : baseColor;
  const strokeWidth = computeEdgeStrokeWidth(count);

  const label = edgeData
    ? count > 1
      ? `${count} ${edgeData.edge_type}`
      : edgeData.edge_type
    : "";

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        {...(markerEnd ? { markerEnd } : {})}
        style={{
          stroke,
          strokeWidth,
          strokeDasharray: isPortal ? "6 3" : undefined,
        }}
      />
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

export const ArchEdgeRenderer = memo(ArchEdgeRendererImpl);

export const archEdgeTypes = {
  arch: ArchEdgeRenderer,
};
