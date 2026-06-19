"use client";

/**
 * A typed system-map edge. Colour + glyph come from the edge-kind registry;
 * the dash pattern encodes match confidence (exact solid, candidate dashed,
 * inferred dotted). Co-change edges read as behavioral, contract/dep edges as
 * structural. Selection and overlay state (highlight / dim / badge) reuse the
 * same calm-vs-emphasised treatment as the knowledge-graph edges.
 */

import { memo } from "react";
import { BaseEdge, EdgeLabelRenderer, getSmoothStepPath, type EdgeProps } from "@xyflow/react";
import { edgeKindStyle, matchTypeDash } from "./edge-kinds";
import type { SystemMapEdgeData } from "./types";

function strokeWidth(weight: number): number {
  return Math.min(1 + Math.log2(weight + 1) * 0.5, 3);
}

function SystemMapEdgeInner(props: EdgeProps) {
  const { id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, markerEnd, data, selected } = props;
  const { edge, overlay } = data as unknown as SystemMapEdgeData;
  const style = edgeKindStyle(edge.kind);

  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    borderRadius: 8,
  });

  const emphasized = selected || overlay?.highlighted;
  const dimmed = overlay?.dimmed ?? false;
  const stroke = emphasized ? "var(--color-viz-selection)" : style.color;
  const dash = emphasized ? "none" : matchTypeDash(edge.match_type);
  const opacity = dimmed ? 0.08 : emphasized ? 1 : 0.85;

  const Icon = style.icon;
  const label = edge.weight > 1 ? `${style.label} ×${edge.weight}` : style.label;

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        {...(markerEnd ? { markerEnd } : {})}
        style={{
          stroke,
          strokeWidth: emphasized ? 2.5 : strokeWidth(edge.weight),
          strokeDasharray: dash,
          opacity,
          animation: emphasized ? "edgeFlow 1.5s linear infinite" : "none",
        }}
      />
      {!dimmed && (
        <EdgeLabelRenderer>
          <div
            className="nodrag nopan"
            style={{
              position: "absolute",
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              background: "var(--color-bg-surface)",
              color: "var(--color-text-secondary)",
              padding: "2px 7px",
              borderRadius: 6,
              fontFamily: "var(--font-mono, ui-monospace, monospace)",
              fontSize: 9.5,
              fontWeight: 500,
              letterSpacing: 0.3,
              pointerEvents: "none",
              border: emphasized
                ? "1px solid var(--color-viz-selection)"
                : "1px solid var(--color-border-default)",
              whiteSpace: "nowrap",
              opacity: emphasized ? 1 : 0.92,
            }}
          >
            <Icon size={10} aria-hidden style={{ color: style.color, flexShrink: 0 }} />
            {label}
            {overlay?.badge && (
              <span style={{ color: "var(--color-risk-high)", fontWeight: 700 }}>· {overlay.badge.label}</span>
            )}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

export const SystemMapEdge = memo(SystemMapEdgeInner);

export const systemMapEdgeTypes = { systemEdge: SystemMapEdge };
