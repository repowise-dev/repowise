import type { Metadata } from "next";
import { notFound } from "next/navigation";
import Link from "next/link";
import { getOverviewSummary } from "@/lib/api/overview";
import { getProviders } from "@/lib/api/providers";
import { getStatsHighlights } from "@/lib/api/stats";
import { getCommitsPage } from "@/lib/api/git";
import { FirstIndexExperience } from "@/components/repos/first-index-experience";
import { QuickActionsWrapper } from "@/components/dashboard/quick-actions-wrapper";
import { AskAnythingRow } from "@/components/overview/ask-anything-row";
import {
  AttentionRows,
  ChangeLine,
  CommitRows,
  DecisionRows,
  ExploreList,
  HealthLede,
  HotspotTable,
  LanguageBar,
  OverviewSection,
  ReadsColumn,
  RepoIdentityHeader,
  SectionLink,
  type ChangeStat,
  type ExploreEntry,
  type ReadItem,
  type RepoIdentityMeta,
} from "@repowise-dev/ui/overview";
import { getDefaultHref } from "@repowise-dev/ui/dashboard/attention-href";
// Direct path, not the `ui/stats` barrel: that barrel re-exports two
// "use client" modules, which would ship them to the browser for a page that
// renders entirely on the server.
import { StatRibbon, type RibbonStat } from "@repowise-dev/ui/stats/stat-ribbon";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import {
  formatBytes,
  formatCost,
  formatDateTime,
  formatLOC,
  formatNumber,
  formatRelativeTime,
  formatTokens,
} from "@repowise-dev/ui/lib/format";

export const metadata: Metadata = { title: "Overview" };

interface Props {
  params: Promise<{ id: string }>;
}

async function safeFetch<T>(fn: () => Promise<T>): Promise<T | null> {
  try {
    return await fn();
  } catch {
    return null;
  }
}

/** Rows shown in the activity section. Fetched server-side so the list is in
 *  the initial HTML instead of waterfalling in after paint. */
const COMMIT_LIMIT = 6;

export default async function OverviewPage({ params }: Props) {
  const { id } = await params;

  // Every fetch the page needs, in one wave, on the server. Nothing below
  // renders client-side: this is a static read, so a hydration boundary would
  // only buy a slower first paint and worse indexability. The commits call
  // replaces a client-side SWR fetch that used to waterfall in after paint.
  const [summary, providers, statsHighlights, commitsPage] = await Promise.all([
    safeFetch(() => getOverviewSummary(id)),
    safeFetch(() => getProviders()),
    safeFetch(() => getStatsHighlights(id)),
    safeFetch(() => getCommitsPage(id, { sort: "date", limit: COMMIT_LIMIT })),
  ]);
  if (!summary) notFound();

  const { repo, stats, health, sync } = summary;
  const isFresh = stats.file_count === 0;

  const base = `/repos/${id}`;
  const lastActivityAt = sync.last_sync_at ?? sync.last_resync_at ?? health.last_indexed_at;

  // ── Identity ───────────────────────────────────────────────────────────
  const meta: RepoIdentityMeta[] = [];
  const primaryLanguage = summary.languages[0]?.language;
  if (primaryLanguage) meta.push({ label: primaryLanguage, dot: true });
  meta.push({ label: repo.default_branch, mono: true });
  if (repo.head_commit) meta.push({ label: repo.head_commit.slice(0, 7), mono: true });
  if (statsHighlights) {
    meta.push({ label: `${formatLOC(statsHighlights.scale.total_nloc)} lines`, mono: true });
  }
  if (lastActivityAt) {
    meta.push({
      label: `synced ${formatRelativeTime(lastActivityAt)}`,
      title: formatDateTime(lastActivityAt),
    });
  }

  const header = (
    <RepoIdentityHeader
      name={repo.name}
      owner={repo.owner}
      description={repo.local_path}
      remoteUrl={repo.remote_url}
      meta={isFresh ? [] : meta}
      actions={
        isFresh ? null : (
          <QuickActionsWrapper
            variant="header"
            repoId={id}
            repoName={repo.name}
            pageCount={sync.page_count || stats.file_count}
            modelName={providers?.active.model ?? sync.last_sync_model ?? ""}
            lastSyncAt={sync.last_sync_at}
            lastResyncAt={sync.last_resync_at}
          />
        )
      }
    />
  );

  if (isFresh) {
    return (
      <div className="mx-auto flex w-full max-w-[1280px] flex-col gap-8 p-[var(--page-pad)]">
        {header}
        <FirstIndexExperience repoId={id} repoName={repo.name} />
      </div>
    );
  }

  // ── What moved between the last two indexes ────────────────────────────
  // Deltas, not "things newer than the last sync". A commit only lands in the
  // index once a sync ingests it, and that same sync stamps `last_sync_at`, so
  // counting commits after that timestamp returns zero on a healthy repo.
  // Decisions are worse: the sync run creates them. The snapshot deltas the
  // server already computes are the figures that genuinely move.
  const commits = commitsPage?.items ?? [];
  const history = health.history;
  const previousSnapshotAt =
    history.length >= 2 ? (history[history.length - 2]?.taken_at ?? null) : null;

  const changeStats: ChangeStat[] = [];
  if (stats.deltas.file_count) {
    const d = stats.deltas.file_count;
    changeStats.push({
      value: `${d > 0 ? "+" : ""}${formatNumber(d)}`,
      label: Math.abs(d) === 1 ? "file" : "files",
    });
  }
  if (stats.deltas.average_health != null && Math.abs(stats.deltas.average_health) >= 0.05) {
    const d = stats.deltas.average_health;
    changeStats.push({
      value: `${d > 0 ? "+" : ""}${d.toFixed(1)}`,
      label: "defect risk",
    });
  }
  if (stats.deltas.hotspot_health != null && Math.abs(stats.deltas.hotspot_health) >= 0.05) {
    const d = stats.deltas.hotspot_health;
    changeStats.push({
      value: `${d > 0 ? "+" : ""}${d.toFixed(1)}`,
      label: "hotspot health",
    });
  }

  // ── The reads beside health ────────────────────────────────────────────
  // Health is the hero and keeps the biggest number, but it cannot be the only
  // reason to open this page. Each row headlines a subject someone might have
  // come for, at the same altitude as the health score and one click from the
  // page that owns it.
  const docPages = stats.doc_page_count ?? sync.page_count ?? 0;
  const prosePages = stats.doc_prose_page_count;
  const reads: ReadItem[] = [];

  if (docPages > 0) {
    // Pages a model wrote and pages built from the index alone are different
    // things — one costs a generation call and can go stale, the other is free
    // and rebuilt every sync — and a single total hides which you have.
    //
    // The copy says exactly what the query measured ("with model-written
    // prose" / "without") rather than the tempting shorthand "deterministic".
    // A page can lack prose because its provider call failed, and calling that
    // deterministic would report an outage as a design choice.
    const hasSplit = prosePages != null;
    const withoutProse = hasSplit ? Math.max(0, docPages - prosePages) : 0;
    reads.push({
      key: "docs",
      label: "Documentation",
      value: formatNumber(docPages),
      unit: "pages",
      href: `${base}/docs`,
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

  // Savings come from a CLI-written `.repowise/omissions` sidecar, so this row
  // is simply absent wherever that sidecar cannot exist. Hosted composes the
  // same component without it — no flag, no empty state pitching a CLI the
  // viewer is not running.
  const savings = summary.savings;
  const savedTokens = (savings.saved_tokens ?? 0) + (savings.mcp_tokens ?? 0);
  if (savings.available && savedTokens > 0) {
    reads.push({
      key: "savings",
      label: "Agent savings",
      value: formatTokens(savedTokens),
      ...(savings.estimated_usd_saved
        ? { unit: formatCost(savings.estimated_usd_saved) }
        : {}),
      href: `${base}/costs`,
      why: `Tokens your agent did not spend${savings.pricing_model ? `, priced at ${savings.pricing_model}` : ""}`,
    });
  }

  if (stats.dead_export_count > 0) {
    reads.push({
      key: "dead-code",
      label: "Dead code",
      value: formatNumber(stats.dead_export_count),
      unit: "exports",
      href: `${base}/code-health?tab=dead-code`,
      why: "Unused exports that nothing in the graph reaches",
    });
  }

  // Index storage is a local-disk figure. Hosted stores indexes elsewhere and
  // simply never adds this row.
  if (sync.index_storage_bytes) {
    reads.push({
      key: "index",
      label: "Index",
      value: formatBytes(sync.index_storage_bytes),
      href: `${base}/settings`,
      why: `Wiki, symbols and vectors on disk · ${Math.round(stats.doc_coverage_pct)}% average doc confidence`,
    });
  }

  // ── Scale ribbon: the whole first-visit orientation job, in one row ─────
  const ribbon: RibbonStat[] = [];
  if (statsHighlights) {
    ribbon.push({ label: "Lines of code", value: formatLOC(statsHighlights.scale.total_nloc) });
  }
  // Linked, because the KPI strip this replaces was five links: without hrefs
  // here, Files and Symbols lose their only entry point from this page.
  ribbon.push(
    { label: "Files", value: formatNumber(stats.file_count), href: `${base}/files` },
    {
      label: "Symbols",
      value: formatNumber(stats.symbol_count),
      href: `${base}/architecture?view=symbols`,
    },
    { label: "Modules", value: formatNumber(stats.module_count), href: `${base}/architecture` },
    {
      label: "Languages",
      value: formatNumber(summary.languages.length),
      href: `${base}/architecture?view=files&colorMode=language`,
    },
  );

  // ── Explore ────────────────────────────────────────────────────────────
  // Replaces four equal-sized cards that were four different species. Every
  // row carries a live figure rather than a description of the feature, which
  // makes the list double as a second, quieter set of statistics.
  const explore: ExploreEntry[] = [
    {
      key: "docs",
      label: "Docs",
      href: `${base}/docs`,
      description: `${formatNumber(docPages)} pages across ${formatNumber(stats.module_count)} modules`,
    },
    {
      key: "files",
      label: "Files",
      href: `${base}/files`,
      description: `${formatNumber(stats.file_count)} files with per-file docs, health and history`,
    },
    {
      key: "architecture",
      label: "Architecture",
      href: `${base}/architecture`,
      description: `Dependency graph, layers, and ${formatNumber(stats.entry_point_count)} entry points`,
    },
    {
      key: "code-health",
      label: "Code health",
      href: `${base}/code-health`,
      description: `Per-file scores, ${formatNumber(health.open_findings)} open findings, coverage and refactoring targets`,
    },
    {
      key: "knowledge-graph",
      label: "Knowledge graph",
      href: `${base}/knowledge-graph`,
      description: "Entities, communities, and the paths between them",
    },
    {
      key: "coupling",
      label: "Change coupling",
      href: `${base}/architecture?view=coupling`,
      description: "Files that keep changing together, mined from commit history",
    },
    {
      key: "commits",
      label: "Commits",
      href: `${base}/commits`,
      description: "Change-risk ranked history with agent provenance",
    },
    {
      key: "contributors",
      label: "Contributors",
      href: `${base}/owners`,
      description: "Bus factor, per-file maintainers, and the human/agent split",
    },
    {
      key: "stats",
      label: "Stats",
      href: `${base}/stats`,
      description: "Size class, origin, lifetime churn, rhythm and records",
    },
  ];

  const langDistribution: Record<string, number> = {};
  for (const l of summary.languages) langDistribution[l.language] = l.file_count;

  return (
    <div className="mx-auto flex w-full max-w-[1280px] flex-col gap-6 p-[var(--page-pad)] sm:gap-8">
      {header}

      <ChangeLine since={previousSnapshotAt} stats={changeStats} />

      {/* Health keeps the largest number and the leftmost position. The column
          beside it carries the other reasons someone opens this page, so a
          visitor who came for docs or costs finds their figure at the same
          altitude rather than three scrolls down. */}
      <div className="grid grid-cols-1 items-start gap-8 lg:grid-cols-[minmax(0,1fr)_300px] lg:gap-12">
        <HealthLede
          score={health.average_health}
          maintainability={health.maintainability_average}
          performance={health.performance_average}
          hotspotHealth={health.hotspot_health}
          hotspotCount={stats.hotspot_count}
          fileCount={stats.file_count}
          href={`${base}/code-health`}
          LinkComponent={Link}
        />
        <ReadsColumn items={reads} LinkComponent={Link} />
      </div>

      <StatRibbon stats={ribbon} LinkComponent={Link} />

      <OverviewSection
        title="Recent activity"
        action={
          <SectionLink href={`${base}/commits`} LinkComponent={Link}>
            All commits
          </SectionLink>
        }
      >
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2 md:gap-10">
          <div className="flex min-w-0 flex-col gap-2">
            <h3 className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
              Commits
            </h3>
            <CommitRows
              commits={commits}
              hrefFor={(sha) => `${base}/commits?commit=${sha}`}
              LinkComponent={Link}
            />
          </div>
          <div className="flex min-w-0 flex-col gap-2">
            <h3 className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
              Decisions
            </h3>
            <DecisionRows
              decisions={summary.recent_decisions.slice(0, 6)}
              hrefFor={(decisionId) => `${base}/decisions/${encodeURIComponent(decisionId)}`}
              LinkComponent={Link}
            />
          </div>
        </div>
      </OverviewSection>

      {summary.attention.length > 0 && (
        <OverviewSection
          title="Needs attention"
          // No section link: these items span decisions, dead code, hotspots
          // and ownership, so there is no single page that holds "all of
          // them". A count that linked to just one of those would be a lie
          // about where the rest live.
          action={
            <span className="font-mono text-[11px] tabular-nums text-[var(--color-text-tertiary)]">
              {formatNumber(summary.attention.length)} open
            </span>
          }
        >
          <AttentionRows
            items={summary.attention.slice(0, 5)}
            hrefFor={(item) => getDefaultHref(item, base)}
            LinkComponent={Link}
          />
        </OverviewSection>
      )}

      {summary.top_hotspots.length > 0 && (
        <OverviewSection
          title="Where the risk concentrates"
          description="Ranked by prior bug fixes and change frequency, mined from full git history rather than from the code alone."
          action={
            <SectionLink href={`${base}/code-health?tab=triage`} LinkComponent={Link}>
              {`All ${formatNumber(stats.hotspot_count)} hotspots`}
            </SectionLink>
          }
        >
          <HotspotTable
            hotspots={summary.top_hotspots.slice(0, 5)}
            hrefFor={(path) => fileEntityPath(base, path)}
            LinkComponent={Link}
          />
        </OverviewSection>
      )}

      {summary.languages.length > 0 && (
        <OverviewSection
          title="Composition"
          action={
            <SectionLink
              href={`${base}/architecture?view=files&colorMode=language`}
              LinkComponent={Link}
            >
              Open the graph
            </SectionLink>
          }
        >
          <LanguageBar distribution={langDistribution} />
        </OverviewSection>
      )}

      <OverviewSection title="Ask this codebase">
        <AskAnythingRow repoId={id} />
      </OverviewSection>

      <OverviewSection title="Explore this codebase">
        <ExploreList entries={explore} LinkComponent={Link} />
      </OverviewSection>
    </div>
  );
}
