/**
 * Blast-radius ripple — turns a `CrossRepoBlastRadius` (computed by the core /
 * REST layer) into a `SystemMapOverlay` the Live System Map renders without any
 * component change. The target service(s) and everything reachable from them are
 * highlighted; the rest of the map is dimmed; impacted nodes carry a small badge
 * whose tone encodes intensity (nearer + structural = stronger). This is the
 * Phase 3 attach point promised by the Phase 2 overlay API.
 */

import type { CrossRepoBlastRadius, SystemGraph } from "@repowise-dev/types";
import type { BadgeTone, SystemMapBadge, SystemMapOverlay } from "./types";

/**
 * Intensity tone for an impacted node. Structural impact (a real dependency)
 * reads stronger than behavioral co-change, and nearer hops stronger than
 * distant ones — a calm gradient rather than a single alarm colour.
 */
export function impactBadgeTone(distance: number, structural: boolean): BadgeTone {
  if (!structural) return "info";
  if (distance <= 1) return "danger";
  if (distance === 2) return "warning";
  return "info";
}

/**
 * Build the ripple overlay for a blast-radius result. Returns an empty overlay
 * (no dimming, no highlight) when nothing is in focus, so passing it through is
 * always safe.
 */
export function buildBlastRadiusOverlay(
  graph: SystemGraph,
  result: CrossRepoBlastRadius,
): SystemMapOverlay {
  const targets = new Set(result.targets);
  const focus = new Set<string>(targets);
  for (const n of result.impacted) focus.add(n.id);

  if (focus.size === 0) return {};

  const dimNodeIds = new Set<string>();
  for (const node of graph.nodes) {
    if (!focus.has(node.id)) dimNodeIds.add(node.id);
  }

  const nodeBadges: Record<string, SystemMapBadge> = {};
  for (const t of targets) nodeBadges[t] = { label: "source", tone: "info" };
  for (const n of result.impacted) {
    nodeBadges[n.id] = {
      label: `d${n.distance}`,
      tone: impactBadgeTone(n.distance, n.structural),
    };
  }

  // Highlight edges fully inside the focus set (the ripple's paths); dim the
  // rest so the impacted subgraph reads cleanly.
  const highlightEdgeIds = new Set<string>();
  const dimEdgeIds = new Set<string>();
  for (const edge of graph.edges) {
    if (focus.has(edge.source) && focus.has(edge.target)) highlightEdgeIds.add(edge.id);
    else dimEdgeIds.add(edge.id);
  }

  return {
    highlightNodeIds: focus,
    dimNodeIds,
    nodeBadges,
    highlightEdgeIds,
    dimEdgeIds,
  };
}
