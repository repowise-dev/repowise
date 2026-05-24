"use client";

import { memo } from "react";
import { ChevronRight, ChevronDown } from "lucide-react";
import type { NodeProps } from "@xyflow/react";
import { NodeShell } from "../../graph-primitives/node-shell";
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

  const footer = searchHitCount && searchHitCount > 0
    ? <span style={{ color: "#f59520" }}>{searchHitCount} matches</span>
    : undefined;

  const handleClick = () => {
    useArchitectureStore.getState().toggleContainer(containerId);
  };

  return (
    <div onClick={handleClick} style={{ cursor: "pointer" }}>
      <NodeShell
        tone="container"
        kindLabel={label}
        title={`${childCount} files`}
        selected={selected}
        footer={footer}
        width={expanded ? 280 : 200}
        height={expanded ? undefined : 70}
        badges={
          <span style={{ display: "inline-flex", alignItems: "center" }}>
            <ChevronIcon size={12} aria-hidden />
          </span>
        }
      />
    </div>
  );
}

export const ArchContainerNode = memo(ArchContainerNodeImpl);
