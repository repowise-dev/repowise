/**
 * Breaking-change overlay — turns a `BreakingChangeReport` (computed by the core
 * / REST layer) into a `SystemMapOverlay` the Live System Map renders without any
 * component change. Changed provider services are badged with their breaking
 * count, the consumers they endanger are badged "at risk", and the edges between
 * them are highlighted. This is additive (no dimming): the whole map stays
 * legible, with the at-risk seams called out. The Phase 4 attach point promised
 * by the Phase 2 overlay API.
 */

import type { BreakingChangeReport, SystemGraph } from "@repowise-dev/types";
import type { BadgeTone, SystemMapBadge, SystemMapOverlay } from "./types";

/** A provider with at least one `breaking` change reads danger; warnings read warning. */
function providerTone(severity: "breaking" | "warning"): BadgeTone {
  return severity === "breaking" ? "danger" : "warning";
}

/**
 * Build the at-risk overlay for a breaking-change report. Returns an empty
 * overlay when there are no changes, so passing it through is always safe.
 */
export function buildBreakingChangeOverlay(
  graph: SystemGraph,
  report: BreakingChangeReport | null | undefined,
): SystemMapOverlay {
  if (!report || report.changes.length === 0) return {};

  const providerCount = new Map<string, number>();
  const providerSeverity = new Map<string, "breaking" | "warning">();
  const consumerNodeIds = new Set<string>();
  const atRiskPairs = new Set<string>(); // `${consumerNode}->${providerNode}`

  for (const change of report.changes) {
    const pid = change.provider_node_id;
    providerCount.set(pid, (providerCount.get(pid) ?? 0) + 1);
    if (change.severity === "breaking") providerSeverity.set(pid, "breaking");
    else if (!providerSeverity.has(pid)) providerSeverity.set(pid, "warning");
    for (const consumer of change.impacted_consumers) {
      consumerNodeIds.add(consumer.node_id);
      atRiskPairs.add(`${consumer.node_id}->${pid}`);
    }
  }

  const nodeBadges: Record<string, SystemMapBadge> = {};
  for (const [nid, count] of providerCount) {
    const severity = providerSeverity.get(nid) ?? "warning";
    nodeBadges[nid] = {
      label: severity === "breaking" ? `${count} breaking` : `${count} change`,
      tone: providerTone(severity),
    };
  }
  for (const nid of consumerNodeIds) {
    if (!nodeBadges[nid]) nodeBadges[nid] = { label: "at risk", tone: "warning" };
  }

  const highlightEdgeIds = new Set<string>();
  const edgeBadges: Record<string, SystemMapBadge> = {};
  for (const edge of graph.edges) {
    if (atRiskPairs.has(`${edge.source}->${edge.target}`)) {
      highlightEdgeIds.add(edge.id);
      edgeBadges[edge.id] = { label: "breaking", tone: "danger" };
    }
  }

  const overlay: SystemMapOverlay = { nodeBadges };
  if (highlightEdgeIds.size > 0) {
    overlay.highlightEdgeIds = highlightEdgeIds;
    overlay.edgeBadges = edgeBadges;
  }
  return overlay;
}
