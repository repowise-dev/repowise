"use client";

/**
 * Map controls: per-edge-kind visibility toggles and the collapse-to-repos
 * switch. Only edge kinds actually present in the graph are offered, so the
 * toolbar never shows a dead toggle. Pure controlled component — state lives in
 * the parent map.
 */

import type { SystemEdgeKind } from "@repowise-dev/types";
import { Separator } from "../../ui/separator";
import { EDGE_KIND_ORDER, SYSTEM_EDGE_KINDS } from "./edge-kinds";

export interface SystemMapFiltersProps {
  /** Edge kinds present in the (uncollapsed) graph; only these are shown. */
  availableKinds: ReadonlySet<SystemEdgeKind>;
  visibleKinds: ReadonlySet<SystemEdgeKind>;
  onToggleKind: (kind: SystemEdgeKind) => void;
  collapsed: boolean;
  onToggleCollapsed: () => void;
}

export function SystemMapFilters({
  availableKinds,
  visibleKinds,
  onToggleKind,
  collapsed,
  onToggleCollapsed,
}: SystemMapFiltersProps) {
  const kinds = EDGE_KIND_ORDER.filter((k) => availableKinds.has(k));

  const pill = (active: boolean) =>
    `inline-flex cursor-pointer items-center gap-1.5 rounded-full border px-2.5 py-[3px] text-[11px] font-medium ${
      active
        ? "border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] text-[var(--color-text-primary)]"
        : "border-[var(--color-border-subtle)] bg-transparent text-[var(--color-text-tertiary)] opacity-60"
    }`;

  return (
    <div className="flex flex-wrap items-center gap-2">
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
            className={pill(active)}
          >
            <Icon size={11} style={{ color: s.color }} aria-hidden />
            {s.label}
          </button>
        );
      })}
      <Separator orientation="vertical" className="h-[18px]" />
      <button
        type="button"
        onClick={onToggleCollapsed}
        aria-pressed={collapsed}
        title="Group services into one node per repository"
        className={`${pill(collapsed)} opacity-100`}
      >
        {collapsed ? "Repo view" : "Service view"}
      </button>
    </div>
  );
}
