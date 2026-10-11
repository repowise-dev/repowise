/**
 * Conformance overlay — turns a `ConformanceReport` (computed by the core / REST
 * layer) into a `SystemMapOverlay` the Live System Map renders without any
 * component change. Edges that violate a declared dependency rule are badged
 * "violation" and highlighted; edges participating in a dependency cycle are
 * badged "cycle". The offending services carry a node badge too. Additive (no
 * dimming): the whole map stays legible with the governance seams called out.
 * The Phase 5 attach point promised by the Phase 2 overlay API.
 */

import type { ConformanceReport, SystemGraph } from "@repowise-dev/types";
import type { SystemMapBadge, SystemMapOverlay } from "./types";

/**
 * Build the governance overlay for a conformance report. Returns an empty
 * overlay when there are no findings, so passing it through is always safe.
 */
export function buildConformanceOverlay(
  graph: SystemGraph,
  report: ConformanceReport | null | undefined,
): SystemMapOverlay {
  if (!report || (report.violations.length === 0 && report.cycles.length === 0)) {
    return {};
  }

  const nodeBadges: Record<string, SystemMapBadge> = {};
  const edgeBadges: Record<string, SystemMapBadge> = {};
  const highlightEdgeIds = new Set<string>();
  const highlightNodeIds = new Set<string>();

  // Rule violations — danger. Badge the offending edge and both endpoints.
  for (const v of report.violations) {
    if (v.edge_id) {
      highlightEdgeIds.add(v.edge_id);
      edgeBadges[v.edge_id] = { label: "violation", tone: "danger" };
    }
    for (const nid of [v.source, v.target]) {
      highlightNodeIds.add(nid);
      if (!nodeBadges[nid]) nodeBadges[nid] = { label: "violation", tone: "danger" };
    }
  }

  // Dependency cycles — warning (a smell, not an explicit policy breach). Don't
  // override an existing violation badge on the same edge/node.
  for (const cycle of report.cycles) {
    for (const eid of cycle.edge_ids) {
      highlightEdgeIds.add(eid);
      if (!edgeBadges[eid]) edgeBadges[eid] = { label: "cycle", tone: "warning" };
    }
    for (const nid of cycle.nodes) {
      highlightNodeIds.add(nid);
      if (!nodeBadges[nid]) nodeBadges[nid] = { label: "cycle", tone: "warning" };
    }
  }

  const overlay: SystemMapOverlay = { nodeBadges };
  if (highlightNodeIds.size > 0) overlay.highlightNodeIds = highlightNodeIds;
  if (highlightEdgeIds.size > 0) {
    overlay.highlightEdgeIds = highlightEdgeIds;
    overlay.edgeBadges = edgeBadges;
  }
  return overlay;
}
