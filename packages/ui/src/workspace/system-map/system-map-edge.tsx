"use client";

/**
 * A typed system-map edge. Structural edges are solid ink, co-change edges a
 * quieter dotted line; the dash follows match confidence (exact solid,
 * candidate dashed, inferred dotted). Kind is named in the label, the legend
 * and the drawer rather than in a hue per transport.
 *
 * Labels show only for the edges touching what the reader hovers or selects,
 * plus edges a lens badged. Drawn always, a busy pair's labels land on top of
 * each other and none of them can be read.
 */

import { memo } from "react";
import { BaseEdge, EdgeLabelRenderer, getSmoothStepPath, type EdgeProps } from "@xyflow/react";
import { edgeKindStyle, matchTypeDash } from "./edge-kinds";
import { useEdgeActive } from "./system-map-focus";
import { weightShort } from "./system-map-model";
import type { SystemMapEdgeData } from "./types";

function strokeWidth(weight: number): number {
  return Math.min(1 + Math.log2(weight + 1) * 0.4, 3.2);
}

function badgeColor(tone: "danger" | "warning" | "info"): string {
  if (tone === "danger") return "var(--color-error)";
  if (tone === "warning") return "var(--color-warning)";
  return "var(--color-text-secondary)";
}

function SystemMapEdgeInner(props: EdgeProps) {
  const { id, source, target, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, markerEnd, data } =
    props;
  const { edge, overlay } = data as unknown as SystemMapEdgeData;
  const style = edgeKindStyle(edge.kind);
  const active = useEdgeActive(id, source, target);

  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    borderRadius: 8,
  });

  const dimmed = (overlay?.dimmed ?? false) && !active;
  const lensed = overlay?.highlighted ?? false;
  const stroke = active
    ? "var(--color-accent-primary)"
    : lensed
      ? "var(--color-text-primary)"
      : edge.structural
        ? "var(--color-diagram-edge)"
        : "var(--color-text-tertiary)";
  const width = active || lensed ? Math.max(2, strokeWidth(edge.weight)) : strokeWidth(edge.weight);
  const showLabel = !dimmed && (active || Boolean(overlay?.badge));
  const Icon = style.icon;

  return (
    <g className="sm-edge" data-active={active || lensed ? "" : undefined}>
      <BaseEdge
        id={id}
        path={edgePath}
        {...(markerEnd ? { markerEnd } : {})}
        interactionWidth={16}
        style={{
          stroke,
          strokeWidth: width,
          strokeDasharray: matchTypeDash(edge.match_type),
          opacity: dimmed ? 0.1 : 1,
          transition: "opacity 120ms, stroke 120ms",
        }}
      />
      {showLabel && (
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
              padding: "2px 6px",
              borderRadius: 4,
              fontFamily: "var(--font-mono, ui-monospace, monospace)",
              fontSize: 10,
              lineHeight: "14px",
              pointerEvents: "none",
              border: `1px solid ${active ? "var(--color-accent-primary)" : "var(--color-border-hover)"}`,
              whiteSpace: "nowrap",
              zIndex: active ? 2 : 1,
            }}
          >
            <Icon size={10} aria-hidden style={{ flexShrink: 0 }} />
            {style.label} · {weightShort(edge)}
            {overlay?.badge && (
              <span style={{ color: badgeColor(overlay.badge.tone), fontWeight: 600 }}>
                · {overlay.badge.label}
              </span>
            )}
          </div>
        </EdgeLabelRenderer>
      )}
    </g>
  );
}

export const SystemMapEdge = memo(SystemMapEdgeInner);

export const systemMapEdgeTypes = { systemEdge: SystemMapEdge };
