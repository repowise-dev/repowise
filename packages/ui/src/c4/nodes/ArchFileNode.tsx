"use client";

import { memo } from "react";
import { Flame, Skull, Play } from "lucide-react";
import type { NodeProps } from "@xyflow/react";
import { NodeShell } from "../../graph-primitives/node-shell";
import type { ArchNode } from "../types";

export interface ArchFileNodeProps {
  node: ArchNode;
  hasDocs?: boolean | undefined;
  searchHighlight?: boolean | undefined;
  tourHighlight?: boolean | undefined;
  diffState?: "changed" | "affected" | "faded" | undefined;
}

const COMPLEXITY_COLORS: Record<string, string> = {
  simple: "#4ade80",
  moderate: "#fbbf24",
  complex: "#f87171",
};

function ArchFileNodeImpl(props: NodeProps) {
  const { data, selected } = props as NodeProps & { data: ArchFileNodeProps };
  const { node, hasDocs, searchHighlight, tourHighlight, diffState } = data;

  const kindLabel = node.language
    ? `${node.node_type.toUpperCase()} · ${node.language}`
    : node.node_type.toUpperCase();

  const complexityColor = COMPLEXITY_COLORS[node.complexity] ?? "#94a3b8";

  const badges = (
    <>
      {node.is_entry_point && (
        <span title="Entry point" aria-label="Entry point" style={{ display: "inline-flex", color: "#4ade80" }}>
          <Play size={10} aria-hidden />
        </span>
      )}
      {node.is_hotspot && (
        <span title="Hotspot" aria-label="Hotspot" style={{ display: "inline-flex", color: "#fca5a5" }}>
          <Flame size={10} aria-hidden />
        </span>
      )}
      {node.is_dead && (
        <span title="Dead code" aria-label="Dead code" style={{ display: "inline-flex", color: "#cbd5e1" }}>
          <Skull size={10} aria-hidden />
        </span>
      )}
    </>
  );

  const footer = (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <span
        title={`Complexity: ${node.complexity}`}
        style={{
          width: 8,
          height: 8,
          borderRadius: "50%",
          background: complexityColor,
          display: "inline-block",
        }}
      />
      <span>↓{node.in_degree} ↑{node.out_degree}</span>
    </div>
  );

  return (
    <NodeShell
      tone={node.node_type}
      kindLabel={kindLabel}
      title={node.name}
      subtitle={node.summary}
      footer={footer}
      selected={selected}
      searchHighlight={searchHighlight}
      tourHighlight={tourHighlight}
      diffState={diffState}
      hasDocs={hasDocs ?? node.has_doc}
      badges={badges}
      width={240}
      height={100}
    />
  );
}

export const ArchFileNode = memo(ArchFileNodeImpl);
