"use client";

import { memo, useContext } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Folder } from "lucide-react";
import { GraphContext } from "../graph-flow";
import type { ModuleNodeData } from "../elk-layout";

const COMMUNITY_COLORS = [
  "#6366f1", "#ec4899", "#10b981", "#f59e0b", "#3b82f6", "#a855f7",
  "#14b8a6", "#f97316", "#84cc16", "#06b6d4", "#e11d48", "#8b5cf6",
  "#22c55e", "#eab308", "#0ea5e9", "#d946ef", "#ef4444", "#78716c",
  "#64748b", "#0891b2", "#059669", "#b45309", "#7c3aed", "#db2777",
];

function ModuleGroupNodeInner({ id, data }: NodeProps) {
  const d = data as ModuleNodeData;
  const ctx = useContext(GraphContext);
  const docPct = d.docCoveragePct ?? 0;

  // Color mode determines the accent/border color
  let accentColor: string;
  switch (ctx.colorMode) {
    case "risk": {
      if (docPct >= 0.7) accentColor = "var(--color-node-documented)";
      else if (docPct >= 0.3) accentColor = "var(--color-risk-medium)";
      else accentColor = "var(--color-risk-high)";
      break;
    }
    case "community": {
      let hash = 0;
      for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) | 0;
      accentColor = COMMUNITY_COLORS[Math.abs(hash) % COMMUNITY_COLORS.length];
      break;
    }
    case "language":
    default:
      accentColor = "var(--color-accent-graph)";
      break;
  }

  const isOnPath = ctx.highlightedPath.has(id);
  const hasActivePath = ctx.highlightedPath.size > 0;
  const isDimmed = hasActivePath && !isOnPath;
  const isSelected = ctx.selectedNodeId === id;
  const isHovered = ctx.hoveredNodeId === id;
  const hasHover = ctx.hoveredNodeId !== null;
  const isConnected = ctx.connectedNodeIds.has(id);
  const isHoverDimmed = hasHover && !isConnected && !hasActivePath;

  let opacity = 1;
  if (isDimmed) opacity = 0.15;
  else if (isHoverDimmed) opacity = 0.35;

  return (
    <div
      className="rounded-lg px-2 py-1.5 transition-all duration-200 cursor-pointer"
      style={{
        border: `2px solid ${accentColor}`,
        background: `linear-gradient(135deg, ${accentColor}50 0%, #1e293b 100%)`,
        opacity,
        transform: isHovered || isSelected ? "scale(1.05)" : "scale(1)",
        boxShadow: isOnPath
          ? `0 0 24px var(--color-accent-graph)`
          : isSelected
            ? `0 4px 20px rgba(0,0,0,0.5), 0 0 0 3px ${accentColor}`
            : isHovered
              ? `0 4px 16px rgba(0,0,0,0.4), 0 0 16px ${accentColor}40`
              : `0 2px 8px rgba(0,0,0,0.4)`,
        animation: isOnPath ? "graph-path-pulse 2s ease-in-out infinite" : undefined,
      }}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="!w-2 !h-2 !bg-[var(--color-border-subtle)] !border-none"
      />

      {/* Header row: icon + label + file count */}
      <div className="flex items-center gap-2">
        <Folder className="w-3 h-3 shrink-0" style={{ color: accentColor }} />
        <span className="text-[11px] font-medium text-[var(--color-text-primary)] truncate">
          {d.label}
        </span>
        <span className="ml-auto text-[9px] text-[var(--color-text-tertiary)] tabular-nums shrink-0">
          {d.fileCount ?? 0}
        </span>
      </div>

      <Handle
        type="source"
        position={Position.Bottom}
        className="!w-2 !h-2 !bg-[var(--color-border-subtle)] !border-none"
      />
    </div>
  );
}

export const ModuleGroupNode = memo(ModuleGroupNodeInner);
