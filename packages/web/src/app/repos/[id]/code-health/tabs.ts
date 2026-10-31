/**
 * Code Health's tab list, and where its retired tabs went. The one source the
 * page, its legacy redirect, and the route-link guard all read.
 *
 * Four tabs: the Map (once "triage"), Findings, Performance and Dead code.
 * Tests and Security became pages of their own, Doc drift a Docs view, and
 * blast radius the Commits page's "Review a change" view.
 */

import { Bug, Gauge, LayoutDashboard, Scissors, type LucideIcon } from "lucide-react";
import { reviewHref } from "@/lib/changes";

export const CODE_HEALTH_TABS = ["map", "findings", "performance", "dead-code"] as const;
export type CodeHealthTabId = (typeof CODE_HEALTH_TABS)[number];

/** The tab with no `?tab=`. */
export const DEFAULT_TAB: CodeHealthTabId = "map";

/**
 * Label key under `views.codeHealth` and the tab's mark. The icons are
 * identity, not status: monochrome and inheriting the tab's color.
 */
export const TAB_DEFS: Record<CodeHealthTabId, { labelKey: string; icon: LucideIcon }> = {
  map: { labelKey: "tabs.map", icon: LayoutDashboard },
  findings: { labelKey: "tabs.findings", icon: Bug },
  performance: { labelKey: "tabs.performance", icon: Gauge },
  "dead-code": { labelKey: "tabs.deadCode", icon: Scissors },
};

/**
 * Retired ids that still open a tab here. `triage` is the Map's old name;
 * `heatmap` and `hotspots` were churn tabs the Map's churn lens replaced;
 * `modules` folded into its hub layer and `trend` into the section under it.
 */
export const TAB_ALIASES: Record<string, CodeHealthTabId> = {
  triage: "map",
  heatmap: "map",
  hotspots: "map",
  modules: "map",
  trend: "map",
};

/** Tabs that left Code Health, by the id their old links carry. */
export const MOVED_TABS = ["coverage", "security", "doc-drift", "impact"] as const;

/** The tab a `?tab=` value opens; `?pillar=performance` predates the tab. */
export function resolveCodeHealthTab(
  rawTab: string | null,
  rawPillar: string | null,
): CodeHealthTabId {
  if (rawTab) {
    const aliased = TAB_ALIASES[rawTab];
    if (aliased) return aliased;
    if ((CODE_HEALTH_TABS as readonly string[]).includes(rawTab)) return rawTab as CodeHealthTabId;
  }
  return rawPillar === "performance" ? "performance" : DEFAULT_TAB;
}

/**
 * Where a link to a tab that moved out should land now, carrying the
 * parameters its new home reads; null when the tab is still here.
 */
export function movedTabHref(repoId: string, params: URLSearchParams): string | null {
  const base = `/repos/${repoId}`;
  switch (params.get("tab")) {
    case "coverage":
      return `${base}/tests`;
    case "security":
      return `${base}/security`;
    case "doc-drift": {
      const document = params.get("document");
      return document
        ? `${base}/docs/drift?document=${encodeURIComponent(document)}`
        : `${base}/docs/drift`;
    }
    case "impact":
      return reviewHref(repoId, { file: params.getAll("file") });
    default:
      return null;
  }
}
