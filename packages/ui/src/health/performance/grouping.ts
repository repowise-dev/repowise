import type {
  PerformanceActionabilityState,
  PerformanceAreaFacet,
  PerformanceOpportunity,
  PerformanceQueueExclusion,
} from "@repowise-dev/types/health";

import { areaOf, groupByArea, separatingDepth } from "../../lib/areas";
import type { PerformanceFilterState, PerformanceScope } from "./query";

/**
 * Pure derivations over the loaded result: the actionability split, the area
 * groups, and the "left out" review links. Nothing here re-ranks a cause; the
 * server's order survives inside every group.
 */

export type ActionabilityCounts = Record<PerformanceActionabilityState, number>;

export function actionabilityCounts(items: readonly PerformanceOpportunity[]): ActionabilityCounts {
  const counts: ActionabilityCounts = { plan_ready: 0, advisory: 0, investigate: 0, expected: 0 };
  for (const item of items) counts[item.actionability_state] += 1;
  return counts;
}

/** The server's actionability facet as counts, every state present. */
export function countsFromFacet(
  facet: readonly { value: string; total: number }[] | undefined,
): ActionabilityCounts {
  const counts: ActionabilityCounts = { plan_ready: 0, advisory: 0, investigate: 0, expected: 0 };
  for (const entry of facet ?? []) {
    if (entry.value in counts) counts[entry.value as PerformanceActionabilityState] = entry.total;
  }
  return counts;
}

/** Plan ready and advisory: a named change exists, proven safe or not. */
export function actionableCount(counts: ActionabilityCounts): number {
  return counts.plan_ready + counts.advisory;
}

export interface PerformanceAreaGroup {
  area: string;
  /** The loaded causes in the area, in the server's order; may be fewer than `total`. */
  items: PerformanceOpportunity[];
  counts: ActionabilityCounts;
  /** Causes the area holds in the scope and filters. */
  total: number;
  /** The opened area's own files, which "Show all" would not narrow. */
  own?: boolean;
}

/**
 * Area cards from the server's area facet: every area in the scope with its
 * true counts, in the server's ranking, each holding the loaded causes that
 * sit in it at the facet's depth.
 */
export function facetAreaGroups(
  items: readonly PerformanceOpportunity[],
  facet: PerformanceAreaFacet,
): PerformanceAreaGroup[] {
  return facet.items.map((entry) => ({
    area: entry.area,
    total: entry.total,
    ...(entry.own ? { own: true } : {}),
    counts: countsFromFacet(
      Object.entries(entry.actionability).map(([value, total]) => ({ value, total: total ?? 0 })),
    ),
    items: items.filter((item) => areaOf(item.file_path, facet.depth) === entry.area),
  }));
}

/**
 * Causes grouped by the area of the file the edit goes in, at the depth where
 * the result's files first separate. Groups rank by plan-ready causes, then
 * advisory, then size, then the best rank inside them, so the area worth
 * opening first leads.
 */
export function areaGroups(items: readonly PerformanceOpportunity[]): PerformanceAreaGroup[] {
  const depth = separatingDepth(items.map((item) => item.file_path));
  return groupByArea(items, (item) => item.file_path, depth)
    .map((group) => ({
      ...group,
      counts: actionabilityCounts(group.items),
      total: group.items.length,
    }))
    .sort(
      (a, b) =>
        b.counts.plan_ready - a.counts.plan_ready ||
        b.counts.advisory - a.counts.advisory ||
        b.items.length - a.items.length ||
        a.items[0]!.rank_position - b.items[0]!.rank_position,
    );
}

export interface ExcludedReview {
  reason: PerformanceQueueExclusion;
  count: number;
  label: string;
  /** The scope and filters that list these causes. */
  patch: ReviewPatch;
}

export type ReviewPatch = Partial<PerformanceFilterState> & { scope: PerformanceScope };

const REVIEWS: Record<PerformanceQueueExclusion, { label: string; patch: ReviewPatch }> = {
  unmeasured_cost: { label: "loop size unknown", patch: { scope: "production", proof: "unproven" } },
  background_unproven: {
    label: "scheduled, growth not shown",
    patch: { scope: "production", proof: "background_unproven" },
  },
  no_strategy: {
    label: "need investigation",
    patch: { scope: "production", actionability: "investigate" },
  },
  expected: {
    label: "expected repetition",
    patch: { scope: "production", actionability: "expected" },
  },
  gated_off: {
    label: "switched off by a flag",
    patch: { scope: "production", actionability: "expected" },
  },
  cold_path: { label: "run rarely", patch: { scope: "production", actionability: "expected" } },
  cold_role: { label: "startup, CLI or UI only", patch: { scope: "production" } },
  test: { label: "in tests", patch: { scope: "all", context: "test" } },
  tooling: { label: "in tooling", patch: { scope: "all", context: "tooling" } },
  unknown: { label: "unclassified", patch: { scope: "all", context: "unknown" } },
};

/**
 * What the default queue left out, largest first, each with the filters that
 * list it. Zero counts are dropped: a reason with nothing behind it is not a
 * place worth sending anybody.
 */
export function excludedReviews(
  excluded: Partial<Record<PerformanceQueueExclusion, number>> | undefined,
): ExcludedReview[] {
  if (!excluded) return [];
  return (Object.entries(excluded) as [PerformanceQueueExclusion, number][])
    .filter(([reason, count]) => count > 0 && reason in REVIEWS)
    .map(([reason, count]) => ({ reason, count, ...REVIEWS[reason] }))
    .sort((a, b) => b.count - a.count);
}
