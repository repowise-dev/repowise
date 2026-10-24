/**
 * The words and figures around the map: the one-sentence lede above it and
 * the figures ribbon below it. Both count relationships per edge family from
 * the same tally the family chips and the caption read, so the page and the
 * canvas never disagree.
 */

import { familyTally, type EdgeFamily, type FamilyTally } from "@repowise-dev/ui/workspace/system-map";
import type { RibbonStat } from "@repowise-dev/ui/stats/stat-ribbon";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import { unmatchedOutsideService } from "@repowise-dev/ui/workspace/contract-facts";
import type { ExtractionDiagnostics, SystemGraph, WorkspaceArchitectureResponse } from "@/lib/api/types";
import { httpLinkedStat } from "@/lib/http-link-coverage";

/** The minimal translator shape the copy helpers need; next-intl's `t` fits. */
export type Translator = (key: string, values?: Record<string, string | number>) => string;

/** Joins phrases the locale's way ("a, b and c"); next-intl's `format.list` fits. */
export type ListJoin = (parts: string[]) => string;

function repoCount(graph: SystemGraph): number {
  return new Set(graph.nodes.map((n) => n.repo)).size;
}

/** "4 runtime" per family, in chip order. */
function familyCounts(t: Translator, rows: readonly FamilyTally[]): string[] {
  return rows.map((r) => t("lede.familyCount", { count: r.edges, family: t(`lede.family.${r.family}`) }));
}

/** "4 runtime and 6 history", with the edge total the plural agrees with. */
function familyPhrase(t: Translator, join: ListJoin, rows: readonly FamilyTally[]) {
  return { families: join(familyCounts(t, rows)), total: rows.reduce((sum, r) => sum + r.edges, 0) };
}

/**
 * "10 services in 3 repositories. 8 runtime and data relationships are drawn;
 * 10 build and history relationships are hidden." One sentence pair, stated
 * for the families the filter draws right now.
 */
export function mapLede(
  t: Translator,
  join: ListJoin,
  graph: SystemGraph,
  drawn: ReadonlySet<EdgeFamily>,
): string {
  const estate = t("lede.estate", { services: graph.nodes.length, repos: repoCount(graph) });
  const tally = familyTally(graph.edges, drawn);
  if (tally.length === 0) return estate;
  const shown = tally.filter((r) => r.drawn);
  const hidden = familyPhrase(t, join, tally.filter((r) => !r.drawn));
  if (shown.length === 0) return t("lede.pair", { estate, rest: t("lede.allHidden", hidden) });
  const drawnPhrase = familyPhrase(t, join, shown);
  if (hidden.total === 0) return t("lede.pair", { estate, rest: t("lede.drawn", drawnPhrase) });
  const rest = t("lede.drawnAndHidden", {
    drawn: drawnPhrase.families,
    drawnTotal: drawnPhrase.total,
    hidden: hidden.families,
    hiddenTotal: hidden.total,
  });
  return t("lede.pair", { estate, rest });
}

/** The estate's figures, under the map. */
export function mapRibbon(
  t: Translator,
  tLinks: Translator,
  join: ListJoin,
  graph: SystemGraph,
  architecture: WorkspaceArchitectureResponse | null,
  diag: ExtractionDiagnostics | null | undefined,
): RibbonStat[] {
  const families = familyTally(graph.edges, new Set());
  return [
    {
      label: t("ribbon.services"),
      value: formatNumber(graph.nodes.length),
      sub: t("ribbon.servicesSub", { count: repoCount(graph) }),
    },
    {
      label: t("ribbon.relationships"),
      value: formatNumber(graph.edges.length),
      sub: families.length > 0 ? join(familyCounts(t, families)) : undefined,
    },
    {
      label: t("ribbon.score"),
      // Null when the graph's evidence is insufficient; the notice above the map says why.
      value: architecture?.score != null ? architecture.score.toFixed(1) : "–",
      sub:
        architecture?.propagation_cost_pct != null
          ? t("ribbon.scoreSub", { pct: architecture.propagation_cost_pct.toFixed(1) })
          : t("ribbon.scoreUnit"),
      hint: t("ribbon.scoreHint"),
    },
    {
      label: t("ribbon.contractLinks"),
      value: diag ? formatNumber(diag.total_links) : "–",
      sub: diag
        ? t("ribbon.contractLinksSub", {
            providers: formatNumber(diag.total_providers),
            consumers: formatNumber(diag.total_consumers),
          })
        : undefined,
      hint: t("ribbon.contractLinksHint"),
    },
    // An artifact older than the linked count keeps the unmatched stat.
    (diag && httpLinkedStat(tLinks, diag)) ?? {
      label: t("ribbon.unmatched"),
      value: diag ? formatNumber(unmatchedOutsideService(diag).length) : "–",
      sub: t("ribbon.unmatchedSub"),
      hint: t("ribbon.unmatchedHint"),
    },
  ];
}
