import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
import { OverviewSection, SectionLink } from "@repowise-dev/ui/overview";
import { CodeEvolutionChart } from "@repowise-dev/ui/commits/code-evolution-chart";
import { AgentTrendStrip } from "@repowise-dev/ui/commits/agent-trend-strip";
import { CredibilityInfoButton } from "@repowise-dev/ui/commits/credibility-strip";
import { readPeriodHealth } from "@repowise-dev/ui/commits/period-health";
import type { PeriodHealthSummaryResponse } from "@repowise-dev/types/history";
import { CommitQueue } from "./commit-queue";
import { CommitDetailSheet } from "./commit-detail-sheet";
import { CommitDistribution } from "./commit-distribution";
import { CommitsPeriod } from "./commits-period";
import { historyFilters, isNarrowed, parseCommitFilters } from "@/lib/commit-filters";
import {
  getAgentTrend,
  getCommitEvolution,
  getCommitHealthSummary,
  getCommitStats,
  getCommitsPage,
} from "@/lib/api/git";
import { getWins } from "@/lib/api/history";

const PAGE_SIZE = 50;
// The scatter's own window. Capped at the endpoint's `limit` ceiling; a few
// hundred dots is also about where the plot stops being readable.
const SCATTER_SAMPLE = 200;
const WINS_TOP = 3;

async function safeFetch<T>(fn: () => Promise<T>): Promise<T | null> {
  try {
    return await fn();
  } catch {
    return null;
  }
}

/** Quick-filter sizes from the period summary, under every other filter. */
function impactCounts(summary: PeriodHealthSummaryResponse | null) {
  if (!summary) return undefined;
  const r = readPeriodHealth(summary);
  return { all: summary.total, better: r.better, worse: r.worse, none: r.neutral };
}

/**
 * The commit view: what the work did to code health first.
 *
 * The period headline (improvements first) beside the window's wins, then
 * every commit led by its better/worse chip under the history filters, then
 * the diff-shape charts as context. The history filters live in the URL and
 * are read here, so the headline, the wins and the list's first page come
 * from one fetch wave for the same filters.
 */
export async function CommitsView({
  repoId,
  searchParams,
}: {
  repoId: string;
  searchParams: Record<string, string | string[] | undefined>;
}) {
  const t = await getTranslations("views.commits");
  const base = `/repos/${repoId}`;
  const filters = parseCommitFilters(searchParams);
  const now = new Date();
  const apiFilters = historyFilters(filters, now);
  const narrowed = isNarrowed(filters);
  // Wins follow the window and the author only; the scope line says so.
  const winsRange = historyFilters({ ...filters, impact: "all", hotspots: false, area: undefined }, now);

  // One wave. The scatter plots its own repo-wide recency sample: it explains
  // the scoring model, not the filtered list. The summary leaves the impact
  // filter out so the quick filters keep every option's size.
  const [stats, evolution, trend, firstPage, recent, summary, wins] = await Promise.all([
    safeFetch(() => getCommitStats(repoId)),
    safeFetch(() => getCommitEvolution(repoId)),
    safeFetch(() => getAgentTrend(repoId)),
    safeFetch(() =>
      getCommitsPage(repoId, { ...apiFilters, sort: "date", authorship: "all", limit: PAGE_SIZE }),
    ),
    safeFetch(() => getCommitsPage(repoId, { sort: "date", limit: SCATTER_SAMPLE })),
    safeFetch(() => getCommitHealthSummary(repoId, historyFilters(filters, now, { withImpact: false }))),
    // `/wins` defaults to the past 30 days; all time asks from the epoch.
    safeFetch(() =>
      getWins(repoId, {
        since: winsRange.since ?? new Date(0).toISOString(),
        ...(winsRange.until ? { until: winsRange.until } : {}),
        ...(winsRange.author ? { author: winsRange.author } : {}),
        top: WINS_TOP,
      }),
    ),
  ]);

  if (!firstPage || (stats?.total_commits ?? firstPage.total) === 0) {
    return (
      <EmptyState
        title={t("emptyTitle")}
        description={t("emptyHint")}
        action={{ label: t("emptyAction"), href: `${base}/overview` }}
      />
    );
  }

  return (
    <>
      <CommitsPeriod repoId={repoId} filters={filters} summary={summary} wins={wins} />

      <OverviewSection
        title={t("listTitle")}
        description={t("listDescription")}
        action={<CredibilityInfoButton />}
      >
        <CommitQueue
          repoId={repoId}
          initial={firstPage}
          filters={filters}
          apiFilters={apiFilters}
          narrowed={narrowed}
          impactCounts={impactCounts(summary)}
          impactPending={summary ? readPeriodHealth(summary).tone === "catchingUp" : false}
          // Repo-wide figures: shown only when nothing narrows the list, so a
          // chip never counts a different set than the one beneath it.
          counts={
            stats && !narrowed
              ? { all: stats.total_commits, high: stats.high_priority_count, fixes: stats.fix_commit_count }
              : undefined
          }
        />
      </OverviewSection>

      {evolution && evolution.total_commits > 0 && (
        <OverviewSection title={t("evolutionTitle")} description={t("evolutionDescription")}>
          <CodeEvolutionChart evolution={evolution} />
        </OverviewSection>
      )}

      {trend && <AgentTrendStrip trend={trend} />}

      <OverviewSection
        title={t("scoreBehaviourTitle")}
        description={t("scoreBehaviourDescription")}
        action={
          <SectionLink href={`${base}/code-health?tab=map`} LinkComponent={Link}>
            {t("changeRisk")}
          </SectionLink>
        }
      >
        <CommitDistribution stats={stats} recent={recent?.items ?? []} />
      </OverviewSection>

      <CommitDetailSheet repoId={repoId} />
    </>
  );
}
