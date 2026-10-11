"use client";

import { memo } from "react";
import { Flame, Skull, Play } from "lucide-react";
import type { NodeProps } from "@xyflow/react";
import { InkNodeShell, type InkRole } from "./ink-node-shell";
import { getKindIcon } from "./kind-icons";
import { EntityHoverCard } from "../../shared/entity";
import { THEME } from "../theme/theme-variables";
import type { ArchNode } from "../types";

export interface ArchFileNodeProps {
  node: ArchNode;
  hasDocs?: boolean | undefined;
  searchHighlight?: boolean | undefined;
  tourHighlight?: boolean | undefined;
  /** 1-based step number shown while the guided tour highlights this node. */
  tourStepNumber?: number | undefined;
  diffState?: "changed" | "affected" | "faded" | undefined;
  dimmed?: boolean | undefined;
}

function ArchFileNodeImpl(props: NodeProps) {
  const { data, selected } = props as NodeProps & { data: ArchFileNodeProps };
  const { node, hasDocs, searchHighlight, tourHighlight, tourStepNumber, diffState, dimmed } = data;

  // Re-export barrels are de-emphasized with their honest summary (plan C-3):
  // faded card, no entry-point badge — they are shells, not destinations.
  const isBarrel = node.tags.includes("barrel");
  const barrelFaded = isBarrel && !selected && !searchHighlight && !tourHighlight;

  const kindLabel = isBarrel ? "barrel" : node.node_type;

  // Role-based ink (plan §2.2): entry points are the orange "start here"
  // blocks; barrels recede to secondary ink; everything else is primary.
  const role: InkRole = node.is_entry_point && !isBarrel
    ? "accent"
    : isBarrel
      ? "secondary"
      : "primary";

  const complexityColor = THEME.complexity[node.complexity] ?? THEME.text.muted;

  const badges = (
    <>
      {typeof tourStepNumber === "number" && (
        <span
          title={`Tour step ${tourStepNumber}`}
          aria-label={`Tour step ${tourStepNumber}`}
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            width: 14,
            height: 14,
            borderRadius: "50%",
            fontSize: 9,
            fontWeight: 700,
            color: "var(--color-text-on-accent)",
            background: "var(--color-accent-fill)",
          }}
        >
          {tourStepNumber}
        </span>
      )}
      {node.is_entry_point && !isBarrel && (
        <span title="Entry point" aria-label="Entry point" style={{ display: "inline-flex", color: "currentColor" }}>
          <Play size={10} aria-hidden />
        </span>
      )}
      {node.is_hotspot && (
        <span title="Hotspot" aria-label="Hotspot" style={{ display: "inline-flex", color: THEME.status.hotspot }}>
          <Flame size={10} aria-hidden />
        </span>
      )}
      {node.is_dead && (
        <span title="Dead code" aria-label="Dead code" style={{ display: "inline-flex", color: THEME.status.dead }}>
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
    // Summary-rich hover (plan C-4): curated summary + tags + signals via the
    // shared EntityHoverCard — no new card components.
    <EntityHoverCard
      entity={{ kind: "file", id: node.file_path ?? node.id }}
      meta={{
        kind: "file",
        data: {
          summary: node.summary,
          tags: node.tags,
          owner: node.primary_owner,
          busFactor: node.bus_factor,
          language: node.language,
          hasDocs: hasDocs ?? node.has_doc,
          hasDeadCode: node.is_dead,
        },
      }}
    >
      {/* Dimming discipline (plan D): unrelated cards fade, never vanish —
          the diff overlay keeps its stronger "faded" treatment. */}
      <div
        style={
          barrelFaded
            ? { opacity: 0.55, transition: "opacity 0.2s ease" }
            : dimmed && !diffState
              ? { opacity: 0.45, transition: "opacity 0.2s ease" }
              : { transition: "opacity 0.2s ease" }
        }
      >
        <InkNodeShell
          role={role}
          icon={getKindIcon(node.node_type)}
          kindLabel={kindLabel}
          title={node.name}
          subtitle={node.summary}
          meta={footer}
          selected={selected}
          searchHighlight={searchHighlight}
          tourHighlight={tourHighlight}
          diffState={diffState}
          hasDocs={hasDocs ?? node.has_doc}
          badges={badges}
          width={300}
          height={140}
        />
      </div>
    </EntityHoverCard>
  );
}

export const ArchFileNode = memo(ArchFileNodeImpl);
