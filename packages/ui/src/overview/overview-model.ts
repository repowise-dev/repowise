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
 * field the payload either carries or does not: score validation, for example,
 * is absent on OSS because nothing backtests it there. Asking "is the data here"
 * gets the right answer on both hosts without either one declaring which host
 * it is. The one thing that genuinely cannot be inferred is routing, which is
 * why {@link OverviewRoutes} exists and is the whole capability surface.
 */

import type {
  OverviewAttentionAreaRow,
  OverviewSummaryResponse,
} from "@repowise-dev/types/overview";
import type { ChangeStat } from "./change-line";
import type { ExploreCard } from "./explore-cards";
import type { ExploreEntry } from "./explore-list";
import type { ReadItem } from "./reads-column";
import type { RibbonStat } from "../stats/stat-ribbon";
import { formatLOC, formatNumber } from "../lib/format";

/** Keys of the further doors {@link buildExplore} lists. */
export type ExploreEntryKey =
  | "docs"
  | "chat"
  | "files"
  | "architecture"
  | "code-health"
  | "knowledge-graph"
  | "coupling"
  | "commits"
  | "decisions"
  | "contributors"
  | "stats"
  | "costs";

/** The words the builders below put into rows. Counts arrive raw, so a host
 *  can pluralise them. English by default. */
export interface OverviewModelCopy {
  /** Change line: the word after a file-count delta. */
  changeFiles: (delta: number) => string;
  changeCodeHealth: string;
  changeHotspotHealth: string;
  docsLabel: string;
  docsUnit: string;
  docsSplit: (prose: number, withoutProse: number) => string;
  docsModules: (modules: number) => string;
  docsProseBar: string;
  docsIndexBar: string;
  cardHealth: string;
  cardOwnership: string;
  cardArchitecture: string;
  cardDecisions: string;
  cardRefactoring: string;
  cardTests: string;
  healthScored: string;
  healthHotspots: (hotspots: number, files: number) => string;
  ownershipEmpty: string;
  ownershipSole: (files: number) => string;
  architectureModules: (modules: number, entryPoints: number) => string;
  decisionsEmpty: string;
  decisionsRecorded: (accepted: number, candidates: number) => string;
  refactoringEmpty: string;
  refactoringOpen: (opportunities: number) => string;
  testsCoverage: string;
  testsUnknown: string;
  testsNone: string;
  /** Further doors: label per key. */
  exploreLabels: Record<ExploreEntryKey, string>;
  /** Further doors: the tooltip per key. */
  exploreDocs: (pages: number, modules: number) => string;
  exploreFiles: (files: number) => string;
  exploreArchitecture: (entryPoints: number) => string;
  exploreCodeHealth: (findings: number) => string;
  exploreChat: string;
  exploreKnowledgeGraph: string;
  exploreCoupling: string;
  exploreCommits: string;
  exploreDecisions: string;
  exploreContributors: string;
  exploreStats: string;
  exploreCosts: string;
}

export const OVERVIEW_MODEL_COPY: OverviewModelCopy = {
  changeFiles: (delta) => (Math.abs(delta) === 1 ? "file" : "files"),
  changeCodeHealth: "code health",
  changeHotspotHealth: "hotspot health",
  docsLabel: "Documentation",
  docsUnit: "pages",
  docsSplit: (prose, withoutProse) =>
    `${formatNumber(prose)} with model-written prose, ${formatNumber(withoutProse)} built from the index`,
  docsModules: (modules) => `Across ${formatNumber(modules)} modules`,
  docsProseBar: "Pages with model-written prose",
  docsIndexBar: "Pages built from the index alone",
  cardHealth: "Code health map",
  cardOwnership: "Ownership",
  cardArchitecture: "Architecture",
  cardDecisions: "Decisions",
  cardRefactoring: "Refactoring",
  cardTests: "Tests",
  healthScored: "files scored",
  healthHotspots: (hotspots, files) =>
    `${plural(hotspots, "hotspot", "hotspots")} across ${formatNumber(files)} files`,
  ownershipEmpty: "Who knows each file, and what depends on them",
  ownershipSole: (files) => `sole-owned ${plural(files, "file", "files")}: one author, no second reader`,
  architectureModules: (modules, entryPoints) =>
    `${plural(modules, "module", "modules")}, ${formatNumber(entryPoints)} entry points`,
  decisionsEmpty: "No decisions recorded yet",
  decisionsRecorded: (accepted, candidates) =>
    `recorded, of which ${formatNumber(accepted)} accepted and ${formatNumber(candidates)} awaiting review`,
  refactoringEmpty: "No refactoring analysis yet",
  refactoringOpen: (opportunities) =>
    `open ${plural(opportunities, "opportunity", "opportunities")}, one per file`,
  testsCoverage: "line coverage from the latest report",
  testsUnknown: "Coverage and the tests that reach each file",
  testsNone: "No coverage report yet. Run your tests with coverage on, then add the report:",
  exploreLabels: {
    docs: "Docs",
    chat: "Chat",
    files: "Files",
    architecture: "Architecture",
    "code-health": "Code health",
    "knowledge-graph": "Knowledge graph",
    coupling: "Change coupling",
    commits: "Commits",
    decisions: "Decisions",
    contributors: "Contributors",
    stats: "Stats",
    costs: "Costs",
  },
  exploreDocs: (pages, modules) => `${formatNumber(pages)} pages across ${formatNumber(modules)} modules`,
  exploreFiles: (files) => `${formatNumber(files)} files with per-file docs, health and history`,
  exploreArchitecture: (entryPoints) =>
    `Dependency graph, layers, and ${formatNumber(entryPoints)} entry points`,
  exploreCodeHealth: (findings) =>
    `Per-file scores, ${formatNumber(findings)} open findings, coverage and refactoring targets`,
  exploreChat: "Ask this codebase a question and get an answer with its sources.",
  exploreKnowledgeGraph: "Entities, communities, and the paths between them",
  exploreCoupling: "Files that keep changing together, mined from commit history",
  exploreCommits: "Change-risk ranked history with agent provenance",
  exploreDecisions: "Why the code is shaped this way, from decision records and git history",
  exploreContributors: "Bus factor, per-file maintainers, and the human/agent split",
  exploreStats: "Size class, origin, lifetime churn, rhythm and records",
  exploreCosts: "What indexing this snapshot cost, by model and by run.",
};

/** The inventory's units and detail lines. English by default. */
export interface InventoryCopy {
  healthUnit: string;
  securityUnit: string;
  securityElsewhere: (elsewhere: number) => string;
  refactoringUnit: string;
  decisionsUnit: string;
  decisionsDetail: (awaiting: number, drifting: number) => string;
  ownershipUnit: string;
  ownershipDetail: string;
}

export const INVENTORY_COPY: InventoryCopy = {
  healthUnit: "open findings",
  securityUnit: "in shipped source",
  securityElsewhere: (elsewhere) => `${formatNumber(elsewhere)} more in tests and docs`,
  refactoringUnit: "open opportunities",
  decisionsUnit: "to review",
  decisionsDetail: (awaiting, drifting) =>
    `${formatNumber(awaiting)} awaiting review, ${formatNumber(drifting)} drifting from the code`,
  ownershipUnit: "sole-owned files",
  ownershipDetail: "One author, no second reader",
};

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
  /** @deprecated Nothing on Overview links to settings. */
  settings?: string;
  /** Indexing cost breakdown, listed under Explore only where the page exists. */
  costs?: string;
  /** The tests and coverage page, `${base}/tests` by default. `null` means the
   *  host has no such page, and the Tests card is not rendered. */
  tests?: string | null;
  /** The command the Tests card offers when no coverage report exists,
   *  {@link COVERAGE_ADD_COMMAND} by default. */
  coverageCommand?: string;
}

/** Every route in {@link OverviewRoutes}, with the shared defaults applied. */
export type ResolvedOverviewRoutes = ReturnType<typeof resolveOverviewRoutes>;

/**
 * Resolved routes, with the defaults both hosts share filled in.
 *
 * Exported so a renderer that needs one of these hrefs outside a built list
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
    tests: routes.tests === undefined ? `${base}/tests` : routes.tests,
    coverageCommand: routes.coverageCommand ?? COVERAGE_ADD_COMMAND,
  };
}

/**
 * What moved between the last two index snapshots.
 *
 * Measured against snapshots and not against `last_sync_at`: a commit only
 * enters the index when a sync ingests it, and that same sync stamps the
 * timestamp, so "newer than the last sync" is zero by construction.
 */
export function buildChangeStats(
  summary: OverviewSummaryResponse,
  copy?: Partial<OverviewModelCopy>,
): ChangeStat[] {
  const c = { ...OVERVIEW_MODEL_COPY, ...copy };
  const stats: ChangeStat[] = [];
  const { deltas } = summary.stats;

  if (deltas.file_count) {
    const d = deltas.file_count;
    stats.push({
      value: `${d > 0 ? "+" : ""}${formatNumber(d)}`,
      label: c.changeFiles(d),
    });
  }
  // The 0.05 floor is the point below which a score change is rounding rather
  // than news: both figures render to one decimal, so anything smaller shows
  // as "+0.0".
  for (const [value, label] of [
    [deltas.average_health, c.changeCodeHealth],
    [deltas.hotspot_health, c.changeHotspotHealth],
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
 * The column beside the health hero: documentation, and on hosts that
 * backtest it, how well the health ranking predicted bug fixes.
 */
export function buildReads(
  summary: OverviewSummaryResponse,
  routes: OverviewRoutes,
  copy?: Partial<OverviewModelCopy>,
): ReadItem[] {
  const c = { ...OVERVIEW_MODEL_COPY, ...copy };
  const r = resolveOverviewRoutes(routes);
  const { stats, health } = summary;
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
      label: c.docsLabel,
      value: formatNumber(docPages),
      unit: c.docsUnit,
      href: `${r.base}/docs`,
      why: hasSplit ? c.docsSplit(prosePages, withoutProse) : c.docsModules(stats.module_count),
      ...(hasSplit
        ? {
            bar: [
              {
                key: "prose",
                label: c.docsProseBar,
                value: prosePages,
                detail: formatNumber(prosePages),
              },
              {
                key: "index",
                label: c.docsIndexBar,
                value: withoutProse,
                detail: formatNumber(withoutProse),
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
      href: `${r.base}/code-health`,
      why: "Top-ranked files that did go on to need a bug fix",
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

/** Ingests a coverage report; with no path it finds the report itself. */
export const COVERAGE_ADD_COMMAND = "repowise coverage add";

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

/** Coverage when a report exists; the ingest command when the server says none does. */
function testsCard(
  explore: OverviewSummaryResponse["explore"],
  href: string,
  command: string,
  c: OverviewModelCopy,
): ExploreCard {
  const card = { key: "tests", label: c.cardTests, href };
  const coverage = explore?.coverage;
  if (coverage) {
    return {
      ...card,
      value: `${coverage.line_coverage_pct.toFixed(1)}%`,
      caption: c.testsCoverage,
    };
  }
  // A server without the counts cannot say whether a report exists.
  if (!explore) return { ...card, caption: c.testsUnknown };
  return {
    ...card,
    caption: c.testsNone,
    command,
  };
}

/**
 * The six main doors off Overview, each with one live figure read through the
 * function its page leads with (`summary.explore`), so the card and the page
 * agree. Keys match {@link buildExplore}'s, so the line of further doors can
 * drop the entries a card already covers.
 */
export function buildExploreCards(
  summary: OverviewSummaryResponse,
  routes: OverviewRoutes,
  copy?: Partial<OverviewModelCopy>,
): ExploreCard[] {
  const c = { ...OVERVIEW_MODEL_COPY, ...copy };
  const { base, tests, coverageCommand } = resolveOverviewRoutes(routes);
  const { stats, explore } = summary;
  return [
    { key: "code-health", label: c.cardHealth, href: `${base}/code-health`, ...healthFigure(stats, c) },
    { key: "contributors", label: c.cardOwnership, href: `${base}/owners`, ...ownershipFigure(explore, c) },
    {
      key: "architecture",
      label: c.cardArchitecture,
      href: `${base}/architecture`,
      value: formatNumber(stats.module_count),
      caption: c.architectureModules(stats.module_count, stats.entry_point_count),
    },
    { key: "decisions", label: c.cardDecisions, href: `${base}/decisions`, ...decisionsFigure(explore, c) },
    { key: "refactoring", label: c.cardRefactoring, href: `${base}/refactoring`, ...refactoringFigure(explore, c) },
    ...(tests == null ? [] : [testsCard(explore, tests, coverageCommand, c)]),
  ];
}

type CardFigure = Pick<ExploreCard, "value" | "caption">;
type Explore = OverviewSummaryResponse["explore"];

function healthFigure(stats: OverviewSummaryResponse["stats"], c: OverviewModelCopy): CardFigure {
  if (stats.hotspot_count === 0) return { value: formatNumber(stats.file_count), caption: c.healthScored };
  return {
    value: formatNumber(stats.hotspot_count),
    caption: c.healthHotspots(stats.hotspot_count, stats.file_count),
  };
}

function ownershipFigure(explore: Explore, c: OverviewModelCopy): CardFigure {
  const sole = explore?.ownership?.sole_owned_files;
  if (sole == null) return { caption: c.ownershipEmpty };
  return {
    value: formatNumber(sole),
    caption: c.ownershipSole(sole),
  };
}

function decisionsFigure(explore: Explore, c: OverviewModelCopy): CardFigure {
  const d = explore?.decisions;
  if (!d || d.total === 0) return { caption: c.decisionsEmpty };
  return {
    value: formatNumber(d.total),
    caption: c.decisionsRecorded(d.accepted, d.candidates),
  };
}

function refactoringFigure(explore: Explore, c: OverviewModelCopy): CardFigure {
  const r = explore?.refactoring;
  if (!r) return { caption: c.refactoringEmpty };
  return {
    value: formatNumber(r.opportunities_total),
    caption: c.refactoringOpen(r.opportunities_total),
  };
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
  copy?: Partial<OverviewModelCopy>,
): ExploreEntry[] {
  const c = { ...OVERVIEW_MODEL_COPY, ...copy };
  const l = c.exploreLabels;
  const r = resolveOverviewRoutes(routes);
  const { stats, health } = summary;
  const docPages = docPageCount(summary);
  const entries: ExploreEntry[] = [
    {
      key: "docs",
      label: l.docs,
      href: `${r.base}/docs`,
      description: c.exploreDocs(docPages, stats.module_count),
    },
  ];

  if (r.chat) {
    entries.push({
      key: "chat",
      label: l.chat,
      href: r.chat,
      description: c.exploreChat,
    });
  }

  entries.push(
    {
      key: "files",
      label: l.files,
      href: `${r.base}/files`,
      description: c.exploreFiles(stats.file_count),
    },
    {
      key: "architecture",
      label: l.architecture,
      href: `${r.base}/architecture`,
      description: c.exploreArchitecture(stats.entry_point_count),
    },
    {
      key: "code-health",
      label: l["code-health"],
      href: `${r.base}/code-health`,
      description: c.exploreCodeHealth(health.open_findings),
    },
    {
      key: "knowledge-graph",
      label: l["knowledge-graph"],
      href: `${r.base}/knowledge-graph`,
      description: c.exploreKnowledgeGraph,
    },
    {
      key: "coupling",
      label: l.coupling,
      href: r.coupling,
      description: c.exploreCoupling,
    },
    {
      key: "commits",
      label: l.commits,
      href: `${r.base}/commits`,
      description: c.exploreCommits,
    },
    {
      key: "decisions",
      label: l.decisions,
      href: `${r.base}/decisions`,
      description: c.exploreDecisions,
    },
    {
      key: "contributors",
      label: l.contributors,
      href: `${r.base}/owners`,
      description: c.exploreContributors,
    },
    {
      key: "stats",
      label: l.stats,
      href: `${r.base}/stats`,
      description: c.exploreStats,
    },
  );

  if (r.costs) {
    entries.push({
      key: "costs",
      label: l.costs,
      href: r.costs,
      description: c.exploreCosts,
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

/** An inventory row, with the unit its count is in. */
export type InventoryRow = OverviewAttentionAreaRow & { unit?: string };

/**
 * The inventory's area rows, with each count read from the same basis as the
 * explore card and the page it links to: open findings for code health,
 * matches in shipped source for security, open opportunities for
 * refactoring, decisions to review, and sole-owned files for ownership. Rows
 * whose basis the payload does not carry keep the server's count.
 *
 * Open findings is `open_findings`, the total get_overview and `repowise
 * health` print. The queue's `finding_counts.inventory` leaves zero-impact
 * findings out, so it is not the number those surfaces call open findings.
 */
export function inventoryRows(
  summary: OverviewSummaryResponse,
  copy?: Partial<InventoryCopy>,
): InventoryRow[] {
  const c = { ...INVENTORY_COPY, ...copy };
  const areas = summary.attention_summary?.areas ?? [];
  const explore = summary.explore;
  const findings = summary.health.open_findings;
  return areas.map((area): InventoryRow => {
    switch (area.key) {
      case "health":
        return findings != null ? { ...area, total: findings, unit: c.healthUnit } : area;
      case "security": {
        const s = explore?.security;
        if (!s) return area;
        return {
          ...area,
          total: s.source,
          unit: c.securityUnit,
          detail: s.elsewhere > 0 ? c.securityElsewhere(s.elsewhere) : "",
        };
      }
      case "refactoring": {
        const r = explore?.refactoring;
        return r ? { ...area, total: r.opportunities_total, unit: c.refactoringUnit } : area;
      }
      case "decisions": {
        const d = explore?.decisions;
        if (!d) return area;
        return {
          ...area,
          total: d.candidates + d.needs_review,
          unit: c.decisionsUnit,
          detail: c.decisionsDetail(d.candidates, d.needs_review),
        };
      }
      case "ownership": {
        const o = explore?.ownership;
        return o
          ? {
              ...area,
              total: o.sole_owned_files,
              unit: c.ownershipUnit,
              detail: c.ownershipDetail,
            }
          : area;
      }
      default:
        return area;
    }
  });
}
