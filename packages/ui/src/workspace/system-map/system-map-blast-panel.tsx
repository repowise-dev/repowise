"use client";

/**
 * Blast-radius results rail for the Live System Map. Given a
 * `CrossRepoBlastRadius`, it lists the impacted services (strongest first),
 * split into structural (a real dependency will break) and behavioral (only
 * co-changes historically). Clicking a service re-targets the ripple from it,
 * so you can walk the impact outward. Pure presentation — the host owns the
 * fetch and passes the result in; the ripple itself rides the map's overlay prop.
 *
 * Lives in the map's rail, as a card among the other panels, rather than floating
 * on the canvas.
 */

import { Zap } from "lucide-react";
import type { CrossRepoBlastRadius, ImpactedNode } from "@repowise-dev/types";
import { impactBadgeTone } from "./blast-radius";
import { RailChip, RailEyebrow, SystemMapRailPanel } from "./system-map-rail";

export interface SystemMapBlastPanelProps {
  result: CrossRepoBlastRadius | null;
  loading?: boolean;
  /** Re-target the ripple from an impacted service (walk the impact outward). */
  onSelectTarget: (nodeId: string) => void;
  onClear: () => void;
}

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
      className="flex w-full cursor-pointer items-center gap-2 border-b border-[var(--color-border-subtle)] px-3 py-1.5 text-left hover:bg-[var(--color-bg-overlay)]"
    >
      <span title={`distance ${node.distance}`}>
        <RailChip color={color}>d{node.distance}</RailChip>
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate font-semibold text-[var(--color-text-primary)]">{node.name}</span>
        <span className="block truncate text-[10px] text-[var(--color-text-tertiary)]">
          {node.repo} · {node.edge_kinds.join(", ")}
        </span>
      </span>
      <span className="shrink-0 tabular-nums text-[var(--color-text-tertiary)]">{node.score.toFixed(2)}</span>
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
      <div className="bg-[var(--color-bg-surface)] px-3 py-1.5">
        <RailEyebrow>
          {title} · {nodes.length}
        </RailEyebrow>
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

  const repos = result.impacted_repos.length;

  return (
    <SystemMapRailPanel
      eyebrow="Blast radius"
      icon={<Zap size={13} style={{ color: "var(--color-accent-primary)" }} />}
      onClear={onClear}
      clearLabel="Clear blast radius"
      summary={
        <>
          <div className="break-words font-semibold text-[var(--color-text-primary)]">{sourceLabel || "—"}</div>
          <div className="mt-0.5">
            {loading
              ? "Computing impact…"
              : `${result.total_impacted} impacted across ${repos} other ${repos === 1 ? "repo" : "repos"}`}
          </div>
        </>
      }
    >
      {!loading && result.impacted.length === 0 && (
        <div className="p-3 text-[var(--color-text-tertiary)]">
          {result.targets.length === 0
            ? "No matching service in the graph."
            : "Nothing downstream — no other service depends on this one."}
        </div>
      )}

      <Section title="Will break (dependency)" nodes={structural} onSelect={onSelectTarget} />
      <Section title="May drift (co-change)" nodes={behavioral} onSelect={onSelectTarget} />
    </SystemMapRailPanel>
  );
}
