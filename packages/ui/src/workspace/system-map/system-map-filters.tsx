"use client";

/**
 * The map's section-header controls: which edge kinds are drawn, and whether
 * services or whole repositories are the nodes. Only kinds present in the
 * graph are offered, each with its count, so the row never shows a dead
 * filter. Pure controlled components; state lives in the map.
 */

import type { SystemEdgeKind } from "@repowise-dev/types";
import { Segmented } from "../../shared/segmented";
import { EDGE_KIND_ORDER, SYSTEM_EDGE_KINDS } from "./edge-kinds";

// Re-exported for the lens switch and existing imports; the control lives in shared.
export { Segmented, type SegmentOption } from "../../shared/segmented";

export const MICRO_LABEL =
  "font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";

export interface SystemMapFiltersProps {
  /** Edges per kind in the drawn shape (before the kind filter); only these kinds are offered. */
  kindCounts: ReadonlyMap<SystemEdgeKind, number>;
  visibleKinds: ReadonlySet<SystemEdgeKind>;
  onToggleKind: (kind: SystemEdgeKind) => void;
  /** Whether the repo view would differ from the service view; the switch hides otherwise. */
  canCollapse: boolean;
  collapsed: boolean;
  onCollapsedChange: (collapsed: boolean) => void;
}

export function SystemMapFilters({
  kindCounts,
  visibleKinds,
  onToggleKind,
  canCollapse,
  collapsed,
  onCollapsedChange,
}: SystemMapFiltersProps) {
  const kinds = EDGE_KIND_ORDER.filter((k) => (kindCounts.get(k) ?? 0) > 0);

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2">
      {kinds.length > 0 && (
        <div className="flex min-w-0 flex-wrap items-center gap-1.5" role="group" aria-label="Edge kinds drawn">
          <span className={`${MICRO_LABEL} mr-1`}>Edges</span>
          {kinds.map((kind) => {
            const s = SYSTEM_EDGE_KINDS[kind];
            const Icon = s.icon;
            const active = visibleKinds.has(kind);
            return (
              <button
                key={kind}
                type="button"
                onClick={() => onToggleKind(kind)}
                aria-pressed={active}
                title={`Toggle ${s.label} edges`}
                className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] ${
                  active
                    ? "border-[var(--color-border-hover)] bg-[var(--color-bg-surface)] text-[var(--color-text-primary)]"
                    : "border-dashed border-[var(--color-border-default)] text-[var(--color-text-tertiary)] line-through"
                }`}
              >
                <Icon size={12} aria-hidden />
                {s.label}
                <span className="font-mono text-[10px] tabular-nums text-[var(--color-text-tertiary)] no-underline">
                  {kindCounts.get(kind)}
                </span>
              </button>
            );
          })}
        </div>
      )}
      {canCollapse && (
        <Segmented
          label="Nodes"
          value={collapsed ? "repos" : "services"}
          options={[
            { value: "services", label: "Services", hint: "One node per detected service" },
            { value: "repos", label: "Repositories", hint: "Group services into one node per repository" },
          ]}
          onChange={(v) => onCollapsedChange(v === "repos")}
        />
      )}
    </div>
  );
}
