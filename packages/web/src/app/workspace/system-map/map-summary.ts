/**
 * The words and figures around the map: the one-sentence lede above it and
 * the figures ribbon below it. Both count relationships per edge family from
 * the same tally the family chips read, for the same families the filter draws.
 */

import { useCallback } from "react";
import { useFormatter } from "next-intl";
import { familyTally, type EdgeFamily, type FamilyTally } from "@repowise-dev/ui/workspace/system-map";
import type { RibbonStat } from "@repowise-dev/ui/stats/stat-ribbon";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import { unmatchedOutsideService } from "@repowise-dev/ui/workspace/contract-facts";
import type { ExtractionDiagnostics, SystemGraph, WorkspaceArchitectureResponse } from "@/lib/api/types";
import { httpLinkedStat } from "@/lib/http-link-coverage";

/** The minimal translator shape the copy helpers need; next-intl's `t` fits. */
export type Translator = (key: string, values?: Record<string, string | number>) => string;

/** Joins phrases the locale's way ("a, b and c"). */
export type ListJoin = (parts: string[]) => string;

export function useListJoin(): ListJoin {
  const format = useFormatter();
  return useCallback<ListJoin>((parts) => format.list(parts, { type: "conjunction" }), [format]);
}

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
 * "10 services in 3 repositories. The edge filter shows 4 runtime and 4 data
 * relationships and hides 4 build and 6 history." Whole-estate counts per
 * family the filter selects; what a level or the draw budget leaves out is
 * the map caption's to say, beside the canvas. Null before there is a service to state.
 */
export function mapLede(
  t: Translator,
  join: ListJoin,
  graph: SystemGraph | null,
  drawn: ReadonlySet<EdgeFamily>,
): string | null {
  if (!graph || graph.nodes.length === 0) return null;
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
