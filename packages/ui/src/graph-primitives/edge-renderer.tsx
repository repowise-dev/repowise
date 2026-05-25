"use client";

import { memo } from "react";
import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  type EdgeProps,
} from "@xyflow/react";
import { THEME } from "../c4/theme/theme-variables";

export interface ArchEdgeData {
  edge_type: string;
  count: number;
  category?: string | undefined;
  isPortalEdge?: boolean | undefined;
  dimmed?: boolean | undefined;
}

export const EDGE_CATEGORY_COLORS: Record<string, string> = {
  structural: "#60a5fa",
  behavioral: "#4ade80",
  "data-flow": "#c084fc",
  dependencies: "#fb923c",
  semantic: "#94a3b8",
};

const EDGE_DEFAULT_COLOR = "#8b9dc3";

function getEdgeColor(edgeType: string | undefined): string {
  if (!edgeType) return EDGE_DEFAULT_COLOR;
  return THEME.edge[edgeType] ?? EDGE_CATEGORY_COLORS[edgeType] ?? EDGE_DEFAULT_COLOR;
}

export function computeEdgeStrokeWidth(count: number): number {
  return Math.min(1.5 + Math.log2(count + 1), 5);
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
  const edgeType = edgeData?.edge_type;
  const isPortal = edgeData?.isPortalEdge ?? false;
  const dimmed = edgeData?.dimmed ?? false;

  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  const baseColor = getEdgeColor(edgeType);
  const stroke = selected ? "#fbbf24" : baseColor;
  const strokeWidth = dimmed ? 1 : computeEdgeStrokeWidth(count);
  const strokeOpacity = selected ? 1.0 : dimmed ? 0.06 : 0.75;

  const typeLabel = (edgeType ?? "").replace(/_/g, " ");
  const label = dimmed ? "" : count > 3 ? `${typeLabel} (${count})` : typeLabel || "";

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        {...(markerEnd ? { markerEnd } : {})}
        style={{
          stroke,
          strokeWidth,
          strokeDasharray: isPortal ? "6 3" : "8 4",
          animation: dimmed ? "none" : "edgeFlow 1.5s linear infinite",
          opacity: strokeOpacity,
        }}
      />
      {label && !dimmed && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: "absolute",
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              background: "rgba(15, 23, 42, 0.88)",
              color: stroke,
              padding: "2px 6px",
              borderRadius: 4,
              fontSize: 10,
              fontWeight: 500,
              letterSpacing: 0.3,
              pointerEvents: "none",
              border: `1px solid ${stroke}33`,
              whiteSpace: "nowrap",
              opacity: selected ? 1 : 0.85,
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
