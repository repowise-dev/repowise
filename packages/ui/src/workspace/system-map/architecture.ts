/**
 * Architecture-metrics overlay (Phase 6) — turns the workspace
 * `ArchitectureMetrics` (computed by the core / REST layer) into a
 * `SystemMapOverlay` the Live System Map renders without any component change,
 * plus the shared role styling the DSM and the inspector reuse. The cyclic-core
 * services are highlighted and badged "core" so the architectural center is
 * obvious; the precise per-service role (Core / Shared / Control / Peripheral)
 * shows in the inspector. Additive (no dimming): the map stays legible.
 */

import type { ArchitectureMetrics, NodeRole, SystemGraph } from "@repowise-dev/types";
import type { SystemMapBadge, SystemMapOverlay } from "./types";

export interface RoleStyle {
  label: string;
  /** A CSS color (variable) for tinting badges, diagonal cells, and dots. */
  color: string;
  /** One-line meaning, for legends and tooltips. */
  description: string;
}

/** Canonical styling for each core-periphery role (the one place it lives). */
export const ROLE_STYLE: Record<NodeRole, RoleStyle> = {
  core: {
    label: "Core",
    color: "var(--color-warning)",
    description: "In the largest cyclic group — the architectural center.",
  },
  shared: {
    label: "Shared",
    color: "var(--color-accent-secondary)",
    description: "Many services depend on it; it depends on few (a utility).",
  },
  control: {
    label: "Control",
    color: "var(--color-accent-primary)",
    description: "It depends on many; few depend on it (an orchestrator).",
  },
  peripheral: {
    label: "Peripheral",
    color: "var(--color-text-tertiary)",
    description: "Lightly coupled in both directions.",
  },
};

export const ROLE_ORDER: readonly NodeRole[] = ["core", "shared", "control", "peripheral"];

export function roleStyle(role: NodeRole): RoleStyle {
  return ROLE_STYLE[role] ?? ROLE_STYLE.peripheral;
}

/**
 * Build the architecture overlay: highlight + badge the cyclic-core services.
 * Returns an empty overlay when there is no core, so passing it through is
 * always safe.
 */
export function buildArchitectureOverlay(
  _graph: SystemGraph,
  metrics: ArchitectureMetrics | null | undefined,
): SystemMapOverlay {
  if (!metrics || metrics.core_members.length === 0) return {};

  const nodeBadges: Record<string, SystemMapBadge> = {};
  const highlightNodeIds = new Set<string>();
  for (const id of metrics.core_members) {
    highlightNodeIds.add(id);
    nodeBadges[id] = { label: "core", tone: "info" };
  }

  return { highlightNodeIds, nodeBadges };
}
