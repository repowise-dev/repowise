"use client";

import { memo } from "react";
import { ArrowRight } from "lucide-react";
import type { NodeProps } from "@xyflow/react";
import { Handle, Position } from "@xyflow/react";
import { useArchitectureStore } from "../store/use-architecture-store";
import { THEME } from "../theme/theme-variables";

export interface PortalNodeProps {
  targetLayerId: string;
  targetLayerName: string;
  edgeCount: number;
}

function PortalNodeImpl(props: NodeProps) {
  const { data, selected } = props as NodeProps & { data: PortalNodeProps };
  const { targetLayerId, targetLayerName, edgeCount } = data;

  const handleClick = () => {
    useArchitectureStore.getState().drillIntoLayer(targetLayerId);
  };

  // Ghost ink: portals are pointers out of the scope, not members of it.
  const borderColor = selected
    ? THEME.selection.ring
    : "var(--color-diagram-cluster-border)";

  return (
    <div
      onClick={handleClick}
      style={{
        cursor: "pointer",
        width: 220,
        background: THEME.surface.glass,
        border: `2px dashed ${borderColor}`,
        borderRadius: 8,
        padding: "8px 12px",
        display: "flex",
        flexDirection: "column",
        gap: 2,
        boxShadow: selected ? `0 0 0 2px ${THEME.selection.ringAlpha}` : "0 2px 8px rgba(0,0,0,0.2)",
      }}
    >
      <Handle type="target" position={Position.Top} style={{ opacity: 0 }} />
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{
          fontSize: 13,
          fontWeight: 500,
          color: "var(--color-text-primary)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}>
          {targetLayerName}
        </span>
        <ArrowRight size={12} color="var(--color-text-secondary)" aria-hidden />
      </div>
      <span style={{ fontSize: 10, color: "var(--color-text-secondary)" }}>
        {edgeCount} connections
      </span>
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
    </div>
  );
}

export const PortalNode = memo(PortalNodeImpl);
