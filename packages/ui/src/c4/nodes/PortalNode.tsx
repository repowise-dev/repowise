"use client";

import { memo } from "react";
import type { NodeProps } from "@xyflow/react";
import { NodeShell } from "../../graph-primitives/node-shell";
import { useArchitectureStore } from "../store/use-architecture-store";

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

  return (
    <div onClick={handleClick} style={{ cursor: "pointer" }}>
      <NodeShell
        tone="portal"
        kindLabel="PORTAL"
        title={`→ ${targetLayerName}`}
        subtitle={`${edgeCount} connections`}
        selected={selected}
        width={220}
        height={60}
      />
    </div>
  );
}

export const PortalNode = memo(PortalNodeImpl);
