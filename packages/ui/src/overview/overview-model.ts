/**
 * Everything the repo Overview derives from its payload, in one place.
 *
 * Both hosts render the same page from the same `OverviewSummaryResponse` and,
 * until this module existed, each rebuilt every row of it by hand: ~460 lines
 * in OSS and ~590 in hosted, computing the same reads, the same ribbon, the
 * same explore list, the same delta thresholds. They had already drifted — a
 * languages link pointing at two different graph views, a coupling route that
 * is a query param on one host and its own page on the other, a savings row
 * fixed on one side only — and every new signal had to be added twice.
 *
 * The split is: this module decides *what the page says*, the host supplies
 * *where its links go* and renders the shell. No fetching and no hooks, so a
 * server component (OSS) and a client component (hosted, which loads through
 * SWR and cannot be a server component) can both call it.
 *
 * Host-specific rows need no flags. Every one of them is already gated on a
 * field the payload either carries or does not: local index size is absent on
 * hosted because hosted has no `.repowise/` directory, agent savings are
 * absent because they come from a CLI-written sidecar, and score validation is
 * absent on OSS because nothing backtests it there. Asking "is the data here"
 * gets the right answer on both hosts without either one declaring which host
 * it is. The one thing that genuinely cannot be inferred is routing, which is
 * why {@link OverviewRoutes} exists and is the whole capability surface.
 */

import type { OverviewSummaryResponse } from "@repowise-dev/types/overview";
import type { ChangeStat } from "./change-line";
import type { ExploreEntry } from "./explore-list";
import type { ReadItem } from "./reads-column";
import type { RibbonStat } from "../stats/stat-ribbon";
import {
  formatBytes,
  formatCost,
  formatLOC,
  formatNumber,
  formatTokens,
} from "../lib/format";

/**
 * Where this host's links go.
 *
 * Only the routes the two hosts disagree about are listed. Everything they
 * agree on (`/files`, `/docs`, `/commits`, `/owners`, `/stats`,
 * `/code-health`, `/architecture`, `/knowledge-graph`, `/decisions`) is built
 * from `base` and is not configurable, because a third spelling of those is a
 * bug rather than a feature.
 *
 * An optional route that is absent means the host does not have that page, and
 * the row that would have linked to it is not rendered. That is the honest
 * behaviour: a row whose link 404s is worse than a missing row.
 */
export interface OverviewRoutes {
  /** Link prefix for this host, e.g. `/repos/{id}` or `/s/{shortId}`. */
  base: string;
  /** Change coupling. OSS renders it as a view of the architecture page;
   *  hosted gives it a route of its own. */
  coupling?: string;
  /** The file graph coloured by language. The two hosts spell the view
   *  differently (`view=files` vs `view=graph`). */
  languageGraph?: string;
  /** Scoped chat. Hosted only; OSS puts an ask box on the page instead. */
  chat?: string;
  /** Local settings, where the on-disk index lives. OSS only. */
  settings?: string;
  /** Indexing cost breakdown. Linked from the savings read on both hosts, and
   *  listed under Explore only where the page exists. */
  costs?: string;
}

/** Every route in {@link OverviewRoutes}, with the shared defaults applied. */
export type ResolvedOverviewRoutes = ReturnType<typeof resolveOverviewRoutes>;

/**
 * Resolved routes, with the defaults both hosts share filled in.
 *
 * Exported so a renderer that needs one of these hrefs outside a built list
 * (the Composition section links to the same language graph the ribbon does)
 * reads it from here rather than rebuilding a list to find one entry in it.
 */
export function resolveOverviewRoutes(routes: OverviewRoutes) {
  const { base } = routes;
  return {
    base,
    coupling: routes.coupling ?? `${base}/architecture?view=coupling`,
    languageGraph:
      routes.languageGraph ?? `${base}/architecture?view=files&colorMode=language`,
    chat: routes.chat,
    settings: routes.settings,
    costs: routes.costs,
  };
}

/**
 * What moved between the last two index snapshots.
 *
 * Measured against snapshots and not against `last_sync_at`: a commit only
 * enters the index when a sync ingests it, and that same sync stamps the
 * timestamp, so "newer than the last sync" is zero by construction.
 */
export function buildChangeStats(summary: OverviewSummaryResponse): ChangeStat[] {
  const stats: ChangeStat[] = [];
  const { deltas } = summary.stats;

  if (deltas.file_count) {
    const d = deltas.file_count;
    stats.push({
      value: `${d > 0 ? "+" : ""}${formatNumber(d)}`,
      label: Math.abs(d) === 1 ? "file" : "files",
    });
  }
  // The 0.05 floor is the point below which a score change is rounding rather
  // than news: both figures render to one decimal, so anything smaller shows
  // as "+0.0".
  for (const [value, label] of [
    [deltas.average_health, "defect risk"],
    [deltas.hotspot_health, "hotspot health"],
  ] as const) {
    if (value != null && Math.abs(value) >= 0.05) {
      stats.push({ value: `${value > 0 ? "+" : ""}${value.toFixed(1)}`, label });
    }
  }
  return stats;
}

/** ISO timestamp of the snapshot the deltas are measured against. */
export function previousSnapshotAt(summary: OverviewSummaryResponse): string | null {
  const history = summary.health.history;
  return history.length >= 2 ? (history[history.length - 2]?.taken_at ?? null) : null;
}

/** Generated page count, however this payload spells it. */
export function docPageCount(summary: OverviewSummaryResponse): number {
  return summary.stats.doc_page_count ?? summary.sync.page_count ?? 0;
}

/**
 * The column beside the health hero: the other reasons someone opens this
 * page, at the same altitude as the score.
 */
export function buildReads(summary: OverviewSummaryResponse, routes: OverviewRoutes): ReadItem[] {
  const r = resolveOverviewRoutes(routes);
  const { stats, health, sync, savings } = summary;
  const docPages = docPageCount(summary);
  const prosePages = stats.doc_prose_page_count;
  const reads: ReadItem[] = [];

  if (docPages > 0) {
    // Pages a model wrote and pages built from the index alone are different
    // things — one costs a generation call and can go stale, the other is free
    // and rebuilt every sync — and a single total hides which you have.
    //
    // The copy says what the query measured ("with model-written prose" /
    // "without") rather than the tempting shorthand "deterministic": a page can
    // lack prose because its provider call failed, and calling that
    // deterministic reports an outage as a design choice.
    const hasSplit = prosePages != null;
    const withoutProse = hasSplit ? Math.max(0, docPages - prosePages) : 0;
    reads.push({
      key: "docs",
      label: "Documentation",
      value: formatNumber(docPages),
      unit: "pages",
      href: `${r.base}/docs`,
      why: hasSplit
        ? `${formatNumber(prosePages)} with model-written prose, ${formatNumber(withoutProse)} built from the index`
        : `Across ${formatNumber(stats.module_count)} modules`,
      ...(hasSplit
        ? {
            bar: [
              {
                fraction: prosePages / docPages,
                color: "var(--color-accent-fill)",
                title: `${formatNumber(prosePages)} pages with model-written prose`,
              },
              {
                fraction: withoutProse / docPages,
                color: "color-mix(in srgb, var(--color-accent-fill) 30%, var(--color-bg-inset))",
                title: `${formatNumber(withoutProse)} pages built from the index alone`,
              },
            ],
          }
        : {}),
    });
  }

  // Backtested precision of the defect ranking. Present wherever the server
  // has run the validation, which today is hosted.
  const accuracy = health.defect_accuracy;
  if (accuracy && accuracy.k > 0) {
    reads.push({
      key: "accuracy",
      label: "Score validation",
      value: `${formatNumber(accuracy.hits)}/${formatNumber(accuracy.k)}`,
      ...(accuracy.lift ? { unit: `${accuracy.lift.toFixed(1)}× baseline` } : {}),
      href: `${r.base}/code-health?tab=triage`,
      why: "Top-ranked files that did go on to need a bug fix",
    });
  }

  // From a CLI-written `.repowise/omissions` sidecar, so this row is simply
  // absent wherever that sidecar cannot exist. No flag, no empty state
  // pitching a CLI the viewer is not running.
  const savedTokens = savings.saved_input_tokens ?? 0;
  if (savings.available && savedTokens > 0 && r.costs) {
    // "Estimated" while any of the total rests on a counterfactual rather than
    // a measured before and after. The two are different claims, and the row
    // is too small to show the split, so it qualifies the label instead.
    const inferred = savings.inferred_saved_input_tokens ?? 0;
    reads.push({
      key: "savings",
      label: inferred > 0 ? "Estimated agent savings" : "Agent savings",
      value: formatTokens(savedTokens),
      ...(savings.estimated_usd_saved ? { unit: formatCost(savings.estimated_usd_saved) } : {}),
      href: r.costs,
      why:
        inferred > 0
          ? "Input tokens your agent did not have to read, part of it estimated"
          : "Input tokens your agent did not have to read",
    });
  }

  if (stats.dead_export_count > 0) {
    reads.push({
      key: "dead-code",
      label: "Dead code",
      value: formatNumber(stats.dead_export_count),
      unit: "exports",
      href: `${r.base}/code-health?tab=dead-code`,
      why: "Unused exports that nothing in the graph reaches",
    });
  }

  // A local-disk figure. Hosted stores indexes elsewhere and never has it.
  if (sync.index_storage_bytes && r.settings) {
    reads.push({
      key: "index",
      label: "Index",
      value: formatBytes(sync.index_storage_bytes),
      href: r.settings,
      why: `Wiki, symbols and vectors on disk · ${Math.round(stats.doc_coverage_pct)}% average doc confidence`,
    });
  }

  return reads;
}

/**
 * The scale ribbon: the whole first-visit orientation job in one row.
 *
 * `totalNloc` is passed in rather than read off the payload because the two
 * hosts source it differently — OSS from the stats endpoint, hosted from a
 * field its backend added ahead of the shared types package. Neither is worth
 * encoding here; the caller has the number.
 */
export function buildRibbon(
  summary: OverviewSummaryResponse,
  routes: OverviewRoutes,
  totalNloc?: number | null,
): RibbonStat[] {
  const r = resolveOverviewRoutes(routes);
  const ribbon: RibbonStat[] = [];
  if (totalNloc != null) {
    ribbon.push({ label: "Lines of code", value: formatLOC(totalNloc) });
  }
  // Linked, because the KPI strip this replaced was five links: without hrefs
  // here, Files and Symbols lose their only entry point from this page.
  ribbon.push(
    { label: "Files", value: formatNumber(summary.stats.file_count), href: `${r.base}/files` },
    {
      label: "Symbols",
      value: formatNumber(summary.stats.symbol_count),
      href: `${r.base}/architecture?view=symbols`,
    },
    {
      label: "Modules",
      value: formatNumber(summary.stats.module_count),
      href: `${r.base}/architecture`,
    },
    {
      label: "Languages",
      value: formatNumber(summary.languages.length),
      href: r.languageGraph,
    },
  );
  return ribbon;
}

/**
 * Front doors to the pages that own each subject.
 *
 * Every row carries a live figure where one exists, which is what keeps this
 * from being the sidebar written out twice: a row that can only describe a
 * feature belongs in navigation, not on a page whose job is to report state.
 */
export function buildExplore(
  summary: OverviewSummaryResponse,
  routes: OverviewRoutes,
): ExploreEntry[] {
  const r = resolveOverviewRoutes(routes);
  const { stats, health } = summary;
  const docPages = docPageCount(summary);
  const entries: ExploreEntry[] = [
    {
      key: "docs",
      label: "Docs",
      href: `${r.base}/docs`,
      description: `${formatNumber(docPages)} pages across ${formatNumber(stats.module_count)} modules`,
    },
  ];

  if (r.chat) {
    entries.push({
      key: "chat",
      label: "Chat",
      href: r.chat,
      description: "Ask this codebase a question and get an answer with its sources.",
    });
  }

  entries.push(
    {
      key: "files",
      label: "Files",
      href: `${r.base}/files`,
      description: `${formatNumber(stats.file_count)} files with per-file docs, health and history`,
    },
    {
      key: "architecture",
      label: "Architecture",
      href: `${r.base}/architecture`,
      description: `Dependency graph, layers, and ${formatNumber(stats.entry_point_count)} entry points`,
    },
    {
      key: "code-health",
      label: "Code health",
      href: `${r.base}/code-health`,
      description: `Per-file scores, ${formatNumber(health.open_findings)} open findings, coverage and refactoring targets`,
    },
    {
      key: "knowledge-graph",
      label: "Knowledge graph",
      href: `${r.base}/knowledge-graph`,
      description: "Entities, communities, and the paths between them",
    },
    {
      key: "coupling",
      label: "Change coupling",
      href: r.coupling,
      description: "Files that keep changing together, mined from commit history",
    },
    {
      key: "commits",
      label: "Commits",
      href: `${r.base}/commits`,
      description: "Change-risk ranked history with agent provenance",
    },
    {
      key: "contributors",
      label: "Contributors",
      href: `${r.base}/owners`,
      description: "Bus factor, per-file maintainers, and the human/agent split",
    },
    {
      key: "stats",
      label: "Stats",
      href: `${r.base}/stats`,
      description: "Size class, origin, lifetime churn, rhythm and records",
    },
  );

  if (r.costs) {
    entries.push({
      key: "costs",
      label: "Costs",
      href: r.costs,
      description: "What indexing this snapshot cost, by model and by run.",
    });
  }

  return entries;
}

/** File counts per language, for the composition bar. */
export function buildLanguageDistribution(
  summary: OverviewSummaryResponse,
): Record<string, number> {
  const distribution: Record<string, number> = {};
  for (const l of summary.languages) distribution[l.language] = l.file_count;
  return distribution;
}
