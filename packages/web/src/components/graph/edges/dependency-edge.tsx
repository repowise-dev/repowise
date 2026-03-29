"use client";

import { memo, useContext } from "react";
import {
  getBezierPath,
  type EdgeProps,
} from "@xyflow/react";
import { GraphContext } from "../graph-flow";
import type { DependencyEdgeData } from "../elk-layout";

function DependencyEdgeInner({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  source,
  target,
  data,
}: EdgeProps) {
  const d = (data ?? { importedNames: [], edgeCount: 1 }) as DependencyEdgeData;
  const ctx = useContext(GraphContext);

  const isOnPath = ctx.highlightedEdges.has(id);
  const hasActivePath = ctx.highlightedPath.size > 0;
  const isDimmed = hasActivePath && !isOnPath;
  const isDynamic = d.importedNames.length === 0;

  // Hover-aware: highlight edges connected to hovered node
  const isHoverHighlighted = ctx.connectedEdgeIds.has(id);
  const hasHover = ctx.hoveredNodeId !== null;
  const isHoverDimmed = hasHover && !isHoverHighlighted && !hasActivePath;

  const [edgePath] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  });

  const strokeWidth = Math.max(1.5, Math.min(4, 1.5 + Math.sqrt(d.edgeCount) * 0.6));

  let stroke: string;
  let opacity: number;
  let dashArray: string | undefined;
  let animation: string | undefined;

  if (isOnPath) {
    stroke = "var(--color-accent-graph)";
    opacity = 1;
    dashArray = "6 4";
    animation = "graph-marching-ants 0.6s linear infinite";
  } else if (isDimmed) {
    stroke = "rgba(200, 215, 230, 0.25)";
    opacity = 0.5;
  } else if (isHoverHighlighted) {
    stroke = "#93c5fd";
    opacity = 1;
  } else if (isHoverDimmed) {
    stroke = "rgba(200, 215, 230, 0.15)";
    opacity = 0.4;
  } else if (isDynamic) {
    stroke = "rgba(200, 215, 230, 0.5)";
    opacity = 1;
    dashArray = "4 4";
  } else {
    stroke = "rgba(200, 215, 230, 0.55)";
    opacity = 1;
  }

  return (
    <path
      d={edgePath}
      fill="none"
      stroke={stroke}
      strokeWidth={isHoverHighlighted && !isOnPath ? strokeWidth + 0.5 : strokeWidth}
      strokeOpacity={opacity}
      strokeDasharray={dashArray}
      style={{ animation }}
      className="transition-all duration-200"
    />
  );
}

export const DependencyEdge = memo(DependencyEdgeInner);
