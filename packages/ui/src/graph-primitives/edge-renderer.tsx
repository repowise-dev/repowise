"use client";

import { memo } from "react";
import {
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
  type EdgeProps,
} from "@xyflow/react";
import { ArrowRight, Check, Folder, Link2, Zap, type LucideIcon } from "lucide-react";
import { THEME } from "../c4/theme/theme-variables";

export interface ArchEdgeData {
  edge_type: string;
  count: number;
  category?: string | undefined;
  isPortalEdge?: boolean | undefined;
  dimmed?: boolean | undefined;
}

export const EDGE_CATEGORY_COLORS: Record<string, string> = {
  structural: "var(--color-accent-secondary)",
  behavioral: "var(--color-success)",
  "data-flow": "var(--color-edge-co-change)",
  dependencies: "var(--color-accent-fill)",
  semantic: "var(--color-text-tertiary)",
};

const EDGE_DEFAULT_COLOR = "var(--color-text-tertiary)";

/** Semantic tint — carried by the chip glyph, not the stroke (kg-ux §2.3). */
function getEdgeColor(edgeType: string | undefined): string {
  if (!edgeType) return EDGE_DEFAULT_COLOR;
  return THEME.edge[edgeType] ?? EDGE_CATEGORY_COLORS[edgeType] ?? EDGE_DEFAULT_COLOR;
}

const EDGE_TYPE_ICONS: Record<string, LucideIcon> = {
  imports: ArrowRight,
  calls: Zap,
  contains: Folder,
  tested_by: Check,
  depends_on: Link2,
};

export function computeEdgeStrokeWidth(count: number): number {
  return Math.min(1 + Math.log2(count + 1) * 0.5, 2.5);
}

/**
 * Blueprint edge (kg-ux plan §2.3): orthogonal smooth-step path, thin dashed
 * ink stroke (--color-diagram-edge), with a small paper CHIP riding the edge
 * — relation glyph (semantic tint) + label in mono. Selected = solid orange
 * with marching ants; everything else is calm and static.
 */
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

  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    borderRadius: 8,
  });

  const stroke = selected ? "var(--color-viz-selection)" : "var(--color-diagram-edge)";
  const strokeWidth = dimmed ? 1 : selected ? 2 : computeEdgeStrokeWidth(count);
  const strokeOpacity = selected ? 1.0 : dimmed ? 0.06 : 0.9;

  const typeLabel = (edgeType ?? "").replace(/_/g, " ");
  const label = dimmed ? "" : count > 1 ? `${typeLabel} ×${count}` : typeLabel || "";
  const ChipIcon = (edgeType && EDGE_TYPE_ICONS[edgeType]) || ArrowRight;
  const chipTint = getEdgeColor(edgeType);

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        {...(markerEnd ? { markerEnd } : {})}
        style={{
          stroke,
          strokeWidth,
          strokeDasharray: selected ? "none" : isPortal ? "4 3" : "6 4",
          animation: selected ? "edgeFlow 1.5s linear infinite" : "none",
          opacity: strokeOpacity,
        }}
      />
      {label && !dimmed && (
        <EdgeLabelRenderer>
          <div
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
              border: selected
                ? "1px solid var(--color-viz-selection)"
                : "1px solid var(--color-border-default)",
              boxShadow: "0 1px 2px rgba(0,0,0,0.08)",
              whiteSpace: "nowrap",
              opacity: selected ? 1 : 0.92,
            }}
            className="nodrag nopan"
          >
            <ChipIcon size={10} aria-hidden style={{ color: chipTint, flexShrink: 0 }} />
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
