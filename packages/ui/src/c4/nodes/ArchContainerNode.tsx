"use client";

import { memo, useState } from "react";
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
}

function ArchContainerNodeImpl(props: NodeProps) {
  const { data, selected } = props as NodeProps & { data: ArchContainerNodeProps };
  const { containerId, label, childCount, expanded, searchHitCount } = data;
  const [hovered, setHovered] = useState(false);

  const ChevronIcon = expanded ? ChevronDown : ChevronRight;

  const handleClick = () => {
    useArchitectureStore.getState().toggleContainer(containerId);
  };

  const borderColor = selected
    ? "#fbbf24"
    : "rgba(96, 165, 250, 0.2)";

  return (
    <div
      onClick={handleClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        cursor: "pointer",
        width: expanded ? 320 : 260,
        minHeight: expanded ? undefined : 56,
        background: hovered ? "rgba(96, 165, 250, 0.07)" : "rgba(96, 165, 250, 0.04)",
        border: `1.5px solid ${borderColor}`,
        borderLeft: `3px solid ${selected ? "#fbbf24" : "rgba(96, 165, 250, 0.3)"}`,
        borderRadius: 12,
        padding: "10px 14px",
        display: "flex",
        flexDirection: "column",
        gap: 2,
        boxShadow: selected ? "0 0 0 2px rgba(251,191,36,0.3)" : "none",
        transition: "background 0.15s ease",
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
