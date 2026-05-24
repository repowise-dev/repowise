"use client";

import { memo } from "react";
import { ChevronRight, ChevronDown } from "lucide-react";
import type { NodeProps } from "@xyflow/react";
import { Handle, Position } from "@xyflow/react";
import { useArchitectureStore } from "../store/use-architecture-store";

export interface ArchContainerNodeProps {
  containerId: string;
  label: string;
  childCount: number;
  expanded: boolean;
  searchHitCount?: number | undefined;
  layerColor?: string | undefined;
}

function ArchContainerNodeImpl(props: NodeProps) {
  const { data, selected } = props as NodeProps & { data: ArchContainerNodeProps };
  const { containerId, label, childCount, expanded, searchHitCount } = data;

  const ChevronIcon = expanded ? ChevronDown : ChevronRight;

  const handleClick = () => {
    useArchitectureStore.getState().toggleContainer(containerId);
  };

  const borderColor = selected
    ? "#fbbf24"
    : "rgba(148, 163, 184, 0.25)";

  return (
    <div
      onClick={handleClick}
      style={{
        cursor: "pointer",
        width: expanded ? 280 : 220,
        minHeight: expanded ? undefined : 56,
        background: "rgba(255, 255, 255, 0.02)",
        border: `1.5px solid ${borderColor}`,
        borderRadius: 12,
        padding: "10px 14px",
        display: "flex",
        flexDirection: "column",
        gap: 2,
        boxShadow: selected ? "0 0 0 2px rgba(251,191,36,0.3)" : "none",
      }}
    >
      <Handle type="target" position={Position.Top} style={{ opacity: 0 }} />
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{
          fontSize: 13,
          fontWeight: 600,
          color: "var(--color-text-primary, #f1f5f9)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}>
          {label}
        </span>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontSize: 11, color: "var(--color-text-secondary, #94a3b8)" }}>
            {childCount}
          </span>
          <ChevronIcon size={12} color="var(--color-text-secondary, #94a3b8)" aria-hidden />
        </div>
      </div>
      {searchHitCount != null && searchHitCount > 0 && (
        <span style={{
          fontSize: 10,
          fontFamily: "var(--font-mono, ui-monospace, monospace)",
          color: "#f59520",
          background: "rgba(245, 149, 32, 0.12)",
          padding: "1px 6px",
          borderRadius: 4,
          alignSelf: "flex-start",
        }}>
          {searchHitCount} matches
        </span>
      )}
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
    </div>
  );
}

export const ArchContainerNode = memo(ArchContainerNodeImpl);
