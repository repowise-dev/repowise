"use client";

/**
 * Blast-radius results rail for the Live System Map. Given a
 * `CrossRepoBlastRadius`, it lists the impacted services (strongest first),
 * split into structural (a real dependency will break) and behavioral (only
 * co-changes historically). Clicking a service re-targets the ripple from it,
 * so you can walk the impact outward. Pure presentation — the host owns the
 * fetch and passes the result in; the ripple itself rides the map's overlay prop.
 */

import { X, Zap } from "lucide-react";
import type { CrossRepoBlastRadius, ImpactedNode } from "@repowise-dev/types";
import { impactBadgeTone } from "./blast-radius";

export interface SystemMapBlastPanelProps {
  result: CrossRepoBlastRadius | null;
  loading?: boolean;
  /** Re-target the ripple from an impacted service (walk the impact outward). */
  onSelectTarget: (nodeId: string) => void;
  onClear: () => void;
}

const panelStyle: React.CSSProperties = {
  position: "absolute",
  top: 12,
  left: 12,
  width: 290,
  maxHeight: "calc(100% - 24px)",
  overflowY: "auto",
  background: "var(--color-bg-elevated)",
  border: "1px solid var(--color-border-default)",
  borderRadius: 10,
  boxShadow: "0 8px 24px rgba(0,0,0,0.28)",
  zIndex: 4,
  fontSize: 12,
  color: "var(--color-text-secondary)",
};

function toneColor(tone: "danger" | "warning" | "info"): string {
  switch (tone) {
    case "danger":
      return "var(--color-risk-high)";
    case "warning":
      return "var(--color-warning)";
    default:
      return "var(--color-accent-fill)";
  }
}

function ImpactedRow({
  node,
  onSelect,
}: {
  node: ImpactedNode;
  onSelect: (id: string) => void;
}) {
  const tone = impactBadgeTone(node.distance, node.structural);
  const color = toneColor(tone);
  return (
    <button
      type="button"
      onClick={() => onSelect(node.id)}
      title={`Re-target the ripple from ${node.name}`}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        width: "100%",
        textAlign: "left",
        padding: "6px 12px",
        cursor: "pointer",
        background: "transparent",
        borderBottom: "1px solid var(--color-border-subtle)",
      }}
    >
      <span
        title={`distance ${node.distance}`}
        style={{
          flexShrink: 0,
          width: 22,
          textAlign: "center",
          color,
          border: `1px solid color-mix(in srgb, ${color} 45%, transparent)`,
          background: `color-mix(in srgb, ${color} 16%, transparent)`,
          borderRadius: 4,
          fontSize: 9,
          fontWeight: 700,
          padding: "1px 0",
        }}
      >
        d{node.distance}
      </span>
      <span style={{ flex: 1, minWidth: 0 }}>
        <span style={{ color: "var(--color-text-primary)", display: "block", fontWeight: 600 }}>
          {node.name}
        </span>
        <span style={{ color: "var(--color-text-tertiary)", fontSize: 10 }}>
          {node.repo} · {node.edge_kinds.join(", ")}
        </span>
      </span>
      <span style={{ flexShrink: 0, color: "var(--color-text-tertiary)", fontVariantNumeric: "tabular-nums" }}>
        {node.score.toFixed(2)}
      </span>
    </button>
  );
}

function Section({
  title,
  nodes,
  onSelect,
}: {
  title: string;
  nodes: ImpactedNode[];
  onSelect: (id: string) => void;
}) {
  if (nodes.length === 0) return null;
  return (
    <div>
      <div
        style={{
          padding: "6px 12px",
          fontSize: 10,
          fontWeight: 700,
          letterSpacing: 0.5,
          textTransform: "uppercase",
          color: "var(--color-text-tertiary)",
          background: "var(--color-bg-surface)",
        }}
      >
        {title} · {nodes.length}
      </div>
      {nodes.map((n) => (
        <ImpactedRow key={n.id} node={n} onSelect={onSelect} />
      ))}
    </div>
  );
}

export function SystemMapBlastPanel({
  result,
  loading,
  onSelectTarget,
  onClear,
}: SystemMapBlastPanelProps) {
  if (!result) return null;

  const structural = result.impacted.filter((n) => n.structural);
  const behavioral = result.impacted.filter((n) => !n.structural);
  const sourceLabel = result.targets.join(", ") || result.unresolved_targets.join(", ");

  return (
    <div style={panelStyle}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "10px 12px",
          borderBottom: "1px solid var(--color-border-default)",
        }}
      >
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <Zap size={13} style={{ color: "var(--color-accent-primary)" }} />
          <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.6, textTransform: "uppercase", color: "var(--color-text-tertiary)" }}>
            Blast radius
          </span>
        </span>
        <button type="button" onClick={onClear} aria-label="Clear blast radius" style={{ cursor: "pointer", color: "var(--color-text-tertiary)", display: "inline-flex" }}>
          <X size={14} />
        </button>
      </div>

      <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--color-border-default)" }}>
        <div style={{ color: "var(--color-text-primary)", fontWeight: 600, wordBreak: "break-word" }}>
          {sourceLabel || "—"}
        </div>
        <div style={{ color: "var(--color-text-tertiary)", fontSize: 11, marginTop: 2 }}>
          {loading
            ? "Computing impact…"
            : `${result.total_impacted} impacted across ${result.impacted_repos.length} other repo(s)`}
        </div>
      </div>

      {!loading && result.impacted.length === 0 && (
        <div style={{ padding: "12px", color: "var(--color-text-tertiary)" }}>
          {result.targets.length === 0
            ? "No matching service in the graph."
            : "Nothing downstream — no other service depends on this one."}
        </div>
      )}

      <Section title="Will break (dependency)" nodes={structural} onSelect={onSelectTarget} />
      <Section title="May drift (co-change)" nodes={behavioral} onSelect={onSelectTarget} />
    </div>
  );
}
