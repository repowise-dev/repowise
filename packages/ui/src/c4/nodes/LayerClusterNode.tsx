"use client";

import { memo } from "react";
import type { NodeProps } from "@xyflow/react";
import { NodeShell } from "../../graph-primitives/node-shell";
import { useArchitectureStore } from "../store/use-architecture-store";
import type { ArchLayer } from "../types";

export interface LayerClusterNodeProps {
  layer: ArchLayer;
  searchHighlight?: boolean | undefined;
}

const COMPLEXITY_BAR_COLORS: Record<string, string> = {
  simple: "#4ade80",
  moderate: "#fbbf24",
  complex: "#f87171",
};

function LayerClusterNodeImpl(props: NodeProps) {
  const { data, selected } = props as NodeProps & { data: LayerClusterNodeProps };
  const { layer, searchHighlight } = data;

  const handleClick = () => {
    useArchitectureStore.getState().drillIntoLayer(layer.id);
  };

  const total = Object.values(layer.complexity_distribution).reduce((a, b) => a + b, 0);

  const complexityBars = (
    <div style={{ display: "flex", gap: 3, alignItems: "flex-end", height: 16 }}>
      {(["simple", "moderate", "complex"] as const).map((level) => {
        const count = layer.complexity_distribution[level] ?? 0;
        const pct = total > 0 ? count / total : 0;
        return (
          <div
            key={level}
            title={`${level}: ${count}`}
            style={{
              width: 12,
              height: Math.max(3, pct * 16),
              background: COMPLEXITY_BAR_COLORS[level],
              borderRadius: 2,
            }}
          />
        );
      })}
    </div>
  );

  const healthDisplay = layer.health_score !== null
    ? (
      <span
        style={{
          fontSize: 12,
          fontWeight: 600,
          color: layer.health_score >= 80 ? "#4ade80" : layer.health_score >= 60 ? "#fbbf24" : "#f87171",
        }}
      >
        {Math.round(layer.health_score)}
      </span>
    )
    : null;

  const footer = (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
      <span>{layer.file_count} files</span>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        {complexityBars}
        {healthDisplay}
      </div>
    </div>
  );

  return (
    <div onClick={handleClick} style={{ cursor: "pointer" }}>
      <NodeShell
        tone="layerCluster"
        kindLabel="LAYER"
        title={layer.name}
        subtitle={layer.description}
        footer={footer}
        selected={selected}
        searchHighlight={searchHighlight}
        width={300}
        height={160}
      />
    </div>
  );
}

export const LayerClusterNode = memo(LayerClusterNodeImpl);
