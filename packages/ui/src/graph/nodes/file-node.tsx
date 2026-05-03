"use client";

import { memo, useContext } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { FileText } from "lucide-react";
import { GraphContext } from "../context";
import { languageColor } from "../../lib/confidence";
import type { FileNodeData } from "../elk-layout";

const COMMUNITY_COLORS = [
  "#6366f1", "#ec4899", "#10b981", "#f59e0b", "#3b82f6", "#a855f7",
  "#14b8a6", "#f97316", "#84cc16", "#06b6d4", "#e11d48", "#8b5cf6",
  "#22c55e", "#eab308", "#0ea5e9", "#d946ef", "#ef4444", "#78716c",
  "#64748b", "#0891b2", "#059669", "#b45309", "#7c3aed", "#db2777",
];

function riskColor(score: number): string {
  if (score <= 0.3) return "#22c55e";
  if (score <= 0.7) return "#f59520";
  return "#ef4444";
}

function FileNodeInner({ id, data }: NodeProps) {
  const d = data as FileNodeData;
  const ctx = useContext(GraphContext);

  const isOnPath = ctx.highlightedPath.has(id);
  const hasActivePath = ctx.highlightedPath.size > 0;
  const isDimmed = hasActivePath && !isOnPath;
  const isSearchDimmed = ctx.searchDimmedNodes?.has(id) ?? false;
  const isSelected = ctx.selectedNodeId === id;
  const isHovered = ctx.hoveredNodeId === id;
  const hasHover = ctx.hoveredNodeId !== null;
  const isConnected = ctx.connectedNodeIds.has(id);
  const isHoverDimmed = hasHover && !isConnected && !hasActivePath;

  // Determine node accent color based on color mode
  let accentColor: string;
  switch (ctx.colorMode) {
    case "risk": {
      const score = ctx.riskScores.get(d.fullPath) ?? (d.pagerank * 3);
      accentColor = riskColor(Math.min(1, score));
      break;
    }
    case "community":
      accentColor = COMMUNITY_COLORS[d.communityId % COMMUNITY_COLORS.length] ?? "#6366f1";
      break;
    case "language":
    default:
      accentColor = languageColor(d.language);
      break;
  }

  // Compute opacity
  const searchDimmed = isSearchDimmed && !isOnPath && !isSelected;
  let opacity = 1;
  if (isDimmed) opacity = 0.15;
  else if (searchDimmed) opacity = 0.12;
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
          ? `0 0 24px ${accentColor}80`
          : isSelected
            ? `0 4px 20px rgba(0,0,0,0.5), 0 0 0 3px ${accentColor}`
            : isHovered
              ? `0 4px 16px rgba(0,0,0,0.4), 0 0 16px ${accentColor}40`
              : `0 2px 8px rgba(0,0,0,0.4)`,
        animation: isOnPath ? "graph-path-pulse 2s ease-in-out infinite" : undefined,
        borderStyle: d.isTest ? "dashed" : undefined,
      }}
      aria-label={d.fullPath}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="!w-2 !h-2 !bg-[var(--color-border-subtle)] !border-none"
      />

      {/* Same layout as module node: icon + label + badge */}
      <div className="flex items-center gap-2">
        <FileText className="w-3 h-3 shrink-0" style={{ color: accentColor }} />
        <span className="text-[11px] font-medium text-[var(--color-text-primary)] truncate">
          {d.label}
        </span>
        {d.isEntryPoint && (
          <span className="ml-auto shrink-0 text-[8px] font-bold uppercase px-1 py-0.5 rounded" style={{ background: `${accentColor}30`, color: accentColor }}>
            EP
          </span>
        )}
      </div>

      <Handle
        type="source"
        position={Position.Bottom}
        className="!w-2 !h-2 !bg-[var(--color-border-subtle)] !border-none"
      />
    </div>
  );
}

export const FileNode = memo(FileNodeInner);
