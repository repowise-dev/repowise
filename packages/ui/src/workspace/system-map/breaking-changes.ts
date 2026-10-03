/**
 * Breaking-change overlay — turns a `BreakingChangeReport` (computed by the core
 * / REST layer) into a `SystemMapOverlay` the Live System Map renders without any
 * component change. Changed provider services are badged with their breaking
 * count, endpoint-exposed consumers of incompatible changes are badged "exposed",
 * and the edges between them are highlighted. Warning-only uncertainty never
 * becomes a claim that a consumer will fail. This is additive (no dimming).
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
    if (change.severity !== "breaking") continue;
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
    if (!nodeBadges[nid]) nodeBadges[nid] = { label: "exposed", tone: "warning" };
  }

  const highlightEdgeIds = new Set<string>();
  const edgeBadges: Record<string, SystemMapBadge> = {};
  for (const edge of graph.edges) {
    if (atRiskPairs.has(`${edge.source}->${edge.target}`)) {
      highlightEdgeIds.add(edge.id);
      edgeBadges[edge.id] = { label: "incompatible", tone: "danger" };
    }
  }

  const overlay: SystemMapOverlay = { nodeBadges };
  if (highlightEdgeIds.size > 0) {
    overlay.highlightEdgeIds = highlightEdgeIds;
    overlay.edgeBadges = edgeBadges;
  }
  return overlay;
}
