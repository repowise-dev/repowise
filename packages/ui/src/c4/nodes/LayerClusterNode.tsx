"use client";

import { memo, useState } from "react";
import type { NodeProps } from "@xyflow/react";
import { InkNodeShell, type InkRole } from "./ink-node-shell";
import { getKindIcon } from "./kind-icons";
import { useArchitectureStore } from "../store/use-architecture-store";
import { THEME } from "../theme/theme-variables";
import type { ArchLayer } from "../types";

export interface LayerClusterNodeProps {
  layer: ArchLayer;
  searchHighlight?: boolean | undefined;
  /** "layer" (default) drills into the layer; "subGroup" renders the same
   * card as a curated sub-group and drills into it. Reuse, not a fork. */
  kind?: "layer" | "subGroup" | undefined;
  /** Visually de-emphasized (Test layer by default — decision 2). */
  demoted?: boolean | undefined;
  /** Collapsed sibling card in the detail tier — supporting cast, not the
   * active scope, so it recedes to secondary ink (kg-ux plan §2.2). */
  sibling?: boolean | undefined;
  /** Unrelated to the current selection — fade, never vanish (plan D). */
  dimmed?: boolean | undefined;
}

function LayerClusterNodeImpl(props: NodeProps) {
  const { data, selected } = props as NodeProps & { data: LayerClusterNodeProps };
  const { layer, searchHighlight, demoted, sibling, dimmed } = data;
  const kind = data.kind ?? "layer";
  const [hovered, setHovered] = useState(false);

  // Active-scope cards are primary ink; demoted (Test) and collapsed
  // siblings recede to secondary ink.
  const role: InkRole = demoted || sibling ? "secondary" : "primary";

  // Unified click grammar (kg-ux plan B5): single click / Enter = select +
  // inspect (the page-level onNodeClick handles mouse; keyboard mirrors it
  // here). Drilling moved to double-click, owned by the page handler.
  const handleSelect = () => {
    useArchitectureStore.getState().selectNode(layer.id);
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
          color: layer.health_score >= 80 ? THEME.health.good : layer.health_score >= 60 ? THEME.health.fair : THEME.health.poor,
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
          color: "currentColor",
          transition: "opacity 0.2s ease",
          textAlign: "right",
        }}
      >
        Double-click to open →
      </div>
    </div>
  );

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`${kind === "subGroup" ? "Inspect group" : "Inspect layer"} ${layer.name}`}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          handleSelect();
        }
      }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        cursor: "pointer",
        boxShadow: hovered
          ? "0 4px 16px rgba(0,0,0,0.25)"
          : "none",
        borderRadius: 12,
        opacity: (demoted || dimmed) && !hovered ? 0.45 : 1,
        transition: "box-shadow 0.2s ease, opacity 0.2s ease",
      }}
    >
      <InkNodeShell
        role={role}
        icon={getKindIcon(kind === "subGroup" ? "subGroup" : "layer")}
        heroIcon
        kindLabel={kind === "subGroup" ? "GROUP" : "LAYER"}
        title={layer.name}
        subtitle={layer.description}
        meta={footer}
        selected={selected}
        searchHighlight={searchHighlight}
        width={360}
        height={220}
        titleFontSize={16}
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
