"use client";

/**
 * The map's edge filter: one chip per edge family present in the graph
 * (Runtime, Data, Build, History), each with its relationship count. Runtime
 * and Data are drawn by default; Build and History are opt-in, and the
 * caption names what is hidden. Hovering a chip names the kinds inside it.
 * Controlled; the state lives with the map's level state.
 */

import { ArrowRight, Boxes, Database, Link2, type LucideIcon } from "lucide-react";
import { EDGE_FAMILY_COPY, EDGE_FAMILY_ORDER, type EdgeFamily, type FamilyCount } from "./edge-families";
import { kindMixText } from "./aggregate-facts";

// Re-exported for the lens switch and existing imports; the control lives in shared.
export { Segmented, type SegmentOption } from "../../shared/segmented";

export const MICRO_LABEL =
  "font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-secondary)]";

const FAMILY_ICON: Record<EdgeFamily, LucideIcon> = {
  runtime: ArrowRight,
  data: Database,
  build: Boxes,
  history: Link2,
};

export interface SystemMapFiltersProps {
  /** Relationships per family in the whole graph; only families present get a chip. */
  familyCounts: Partial<Record<EdgeFamily, FamilyCount>>;
  /** Relationships per kind inside each family, named on the chip's hover. */
  kindCounts?: Partial<Record<EdgeFamily, Record<string, number>>> | undefined;
  families: ReadonlySet<EdgeFamily>;
  onToggleFamily: (family: EdgeFamily) => void;
}

export function SystemMapFilters({ familyCounts, kindCounts, families, onToggleFamily }: SystemMapFiltersProps) {
  const present = EDGE_FAMILY_ORDER.filter((f) => (familyCounts[f]?.edges ?? 0) > 0);
  if (present.length === 0) return null;

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-1.5" role="group" aria-label="Edge families drawn">
      <span className={`${MICRO_LABEL} mr-1`}>Edges</span>
      {present.map((family) => {
        const copy = EDGE_FAMILY_COPY[family];
        const Icon = FAMILY_ICON[family];
        const active = families.has(family);
        const count = familyCounts[family]!.edges;
        const kinds = kindCounts?.[family];
        const mix = kinds ? kindMixText(kinds) : "";
        return (
          <button
            key={family}
            type="button"
            onClick={() => onToggleFamily(family)}
            aria-pressed={active}
            title={`${copy.label}: ${copy.meaning}${mix ? ` (${mix})` : ""}. ${active ? "Click to hide." : "Click to show."}`}
            className={`inline-flex min-h-[28px] items-center gap-1.5 rounded-md border px-2 py-1 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)] ${
              active
                ? "border-[var(--color-border-hover)] bg-[var(--color-bg-surface)] text-[var(--color-text-primary)]"
                : "border-dashed border-[var(--color-border-default)] text-[var(--color-text-secondary)]"
            }`}
          >
            <Icon size={12} aria-hidden />
            <span className={active ? undefined : "line-through"}>{copy.label}</span>
            <span className="font-mono text-[10px] tabular-nums text-[var(--color-text-secondary)]">
              {count.toLocaleString()}
            </span>
          </button>
        );
      })}
    </div>
  );
}
