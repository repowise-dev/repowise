"use client";

import { memo, useState } from "react";
import type { NodeProps } from "@xyflow/react";
import { NodeShell } from "../../graph-primitives/node-shell";
import { useArchitectureStore } from "../store/use-architecture-store";
import { THEME } from "../theme/theme-variables";
import type { ArchLayer } from "../types";

export interface LayerClusterNodeProps {
  layer: ArchLayer;
  searchHighlight?: boolean | undefined;
}

function LayerClusterNodeImpl(props: NodeProps) {
  const { data, selected } = props as NodeProps & { data: LayerClusterNodeProps };
  const { layer, searchHighlight } = data;
  const [hovered, setHovered] = useState(false);

  const handleClick = () => {
    useArchitectureStore.getState().drillIntoLayer(layer.id);
  };

  const dominantComplexity = (["complex", "moderate", "simple"] as const)
    .find((c) => (layer.complexity_distribution[c] ?? 0) > 0) ?? "simple";

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
              background: THEME.complexity[level],
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
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span>{layer.file_count} files</span>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {complexityBars}
          {healthDisplay}
        </div>
      </div>
      <div
        style={{
          fontSize: 10,
          opacity: hovered ? 0.9 : 0,
          color: "var(--color-accent-primary, #f59520)",
          transition: "opacity 0.2s ease",
          textAlign: "right",
        }}
      >
        Click to explore →
      </div>
    </div>
  );

  return (
    <div
      onClick={handleClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        cursor: "pointer",
        boxShadow: hovered
          ? "0 4px 16px rgba(0,0,0,0.4)"
          : "none",
        borderRadius: 8,
        transition: "box-shadow 0.2s ease",
      }}
    >
      <NodeShell
        tone="layerCluster"
        kindLabel="LAYER"
        title={layer.name}
        subtitle={layer.description}
        footer={footer}
        selected={selected}
        searchHighlight={searchHighlight}
        width={360}
        height={220}
        titleFontSize={18}
        subtitleLineClamp={2}
        badges={
          <span style={{
            fontSize: 10,
            fontWeight: 600,
            color: THEME.complexity[dominantComplexity],
            textTransform: "uppercase",
            letterSpacing: 0.5,
          }}>
            {dominantComplexity}
          </span>
        }
      />
    </div>
  );
}

export const LayerClusterNode = memo(LayerClusterNodeImpl);
