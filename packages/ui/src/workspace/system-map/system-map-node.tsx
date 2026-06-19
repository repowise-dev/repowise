"use client";

/**
 * A service node on the Live System Map. Built on the shared `NodeShell`
 * (categorical tone by kind) with the reused workspace `HealthRing` for the
 * repo health rollup and small flags for orphan / isolated services. Overlay
 * state (Phase 3+ highlight / dim / badge) is applied via NodeShell's existing
 * selection/diff vocabulary so later phases need no new node component.
 */

import { memo } from "react";
import type { NodeProps } from "@xyflow/react";
import { HealthRing } from "../workspace-graph-node";
import { NodeShell } from "../../graph-primitives/node-shell";
import { nodeKindStyle } from "./node-kinds";
import type { SystemMapNodeData } from "./types";

/** Tone → a single theme colour used for the flag's text, border, and tint. */
function flagColor(tone: "danger" | "warning" | "info"): string {
  switch (tone) {
    case "danger":
      return "var(--color-risk-high)";
    case "warning":
      return "var(--color-warning)";
    default:
      return "var(--color-accent-fill)";
  }
}

function Flag({ label, title, tone }: { label: string; title: string; tone: "danger" | "warning" | "info" }) {
  const color = flagColor(tone);
  return (
    <span
      title={title}
      style={{
        background: `color-mix(in srgb, ${color} 18%, transparent)`,
        color,
        border: `1px solid color-mix(in srgb, ${color} 45%, transparent)`,
        borderRadius: 4,
        padding: "0 4px",
        fontSize: 8,
        fontWeight: 700,
        letterSpacing: 0.3,
      }}
    >
      {label}
    </span>
  );
}

function SystemMapNodeInner({ data, selected }: NodeProps) {
  const { node, health, overlay } = data as unknown as SystemMapNodeData;
  const kind = nodeKindStyle(node.kind);

  const counts = `${node.provider_count} prov · ${node.consumer_count} cons`;
  const types = node.contract_types.length > 0 ? node.contract_types.join(", ") : "no contracts";

  const badges = (
    <>
      {overlay?.badge && (
        <Flag label={overlay.badge.label} title={overlay.badge.label} tone={overlay.badge.tone} />
      )}
      {node.is_orphan_provider && <Flag label="ORPHAN" title="Exposes contracts no consumer calls" tone="warning" />}
      {node.is_isolated && <Flag label="ISOLATED" title="Participates in no cross-repo edges" tone="info" />}
    </>
  );

  const footer = (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      {health && <HealthRing score={health.score} source={health.source} size={28} />}
      <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
        <span>{counts}</span>
        <span style={{ opacity: 0.7 }}>{types}</span>
      </div>
    </div>
  );

  return (
    <NodeShell
      tone={kind.tone}
      kindLabel={node.service_path ? `${kind.label} · ${node.repo}` : kind.label}
      title={node.name}
      footer={footer}
      badges={badges}
      selected={selected || overlay?.highlighted}
      {...(overlay?.dimmed ? { diffState: "faded" as const } : {})}
      width={200}
      height={84}
      titleFontSize={13}
    />
  );
}

export const SystemMapNode = memo(SystemMapNodeInner);

export const systemMapNodeTypes = { systemService: SystemMapNode };
