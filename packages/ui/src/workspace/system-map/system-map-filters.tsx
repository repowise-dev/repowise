"use client";

/**
 * Map controls: per-edge-kind visibility toggles and the collapse-to-repos
 * switch. Only edge kinds actually present in the graph are offered, so the
 * toolbar never shows a dead toggle. Pure controlled component — state lives in
 * the parent map.
 */

import type { SystemEdgeKind } from "@repowise-dev/types";
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

  return (
    <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8 }}>
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
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 5,
              padding: "3px 9px",
              borderRadius: 999,
              fontSize: 11,
              fontWeight: 500,
              cursor: "pointer",
              color: active ? "var(--color-text-primary)" : "var(--color-text-tertiary)",
              background: active ? "var(--color-bg-elevated)" : "transparent",
              border: `1px solid ${active ? "var(--color-border-default)" : "var(--color-border-subtle)"}`,
              opacity: active ? 1 : 0.6,
            }}
          >
            <Icon size={11} style={{ color: s.color }} aria-hidden />
            {s.label}
          </button>
        );
      })}
      <div style={{ width: 1, height: 18, background: "var(--color-border-default)" }} />
      <button
        type="button"
        onClick={onToggleCollapsed}
        aria-pressed={collapsed}
        title="Group services into one node per repository"
        style={{
          padding: "3px 9px",
          borderRadius: 999,
          fontSize: 11,
          fontWeight: 500,
          cursor: "pointer",
          color: collapsed ? "var(--color-text-primary)" : "var(--color-text-tertiary)",
          background: collapsed ? "var(--color-bg-elevated)" : "transparent",
          border: `1px solid ${collapsed ? "var(--color-border-default)" : "var(--color-border-subtle)"}`,
        }}
      >
        {collapsed ? "Repo view" : "Service view"}
      </button>
    </div>
  );
}
