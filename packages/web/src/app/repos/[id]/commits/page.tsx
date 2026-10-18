import type { Metadata } from "next";
import Link from "next/link";
import { GitCommitHorizontal } from "lucide-react";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
import { OverviewSection, SectionLink } from "@repowise-dev/ui/overview";
import { CodeEvolutionChart } from "@repowise-dev/ui/commits/code-evolution-chart";
import { AgentTrendStrip } from "@repowise-dev/ui/commits/agent-trend-strip";
import { CredibilityInfoButton } from "@repowise-dev/ui/commits/credibility-strip";
import { readPeriodHealth } from "@repowise-dev/ui/commits/period-health";
import { CommitQueue } from "@/components/commits/commit-queue";
import { CommitDetailSheet } from "@/components/commits/commit-detail-sheet";
import { CommitDistribution } from "@/components/commits/commit-distribution";
import { CommitsPeriod } from "@/components/commits/commits-period";
import { CommitsViewToggle } from "@/components/commits/commits-view-toggle";
import { ByChangeView, type ByChangeParams } from "@/components/commits/by-change-view";
import { EventsView } from "@/components/commits/events-view";
import { ImpactTab } from "@/components/risk/impact-tab";
import { parseCommitsView } from "@/lib/changes";
import { historyFilters, isNarrowed, parseCommitFilters } from "@/lib/commit-filters";
import {
  getAgentTrend,
  getCommitEvolution,
  getCommitHealthSummary,
  getCommitStats,
  getCommitsPage,
} from "@/lib/api/git";
import { getWins } from "@/lib/api/history";
import { getTranslations } from "next-intl/server";

export const metadata: Metadata = { title: "Commits" };

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

/**
 * A server component; what stays interactive is small islands (the list, the
 * scatter, the detail sheet) sharing state through the URL.
 *
 * The commit view leads with what the work did to code health: the period
 * headline (improvements first) beside the window's wins, then every commit
 * led by its better/worse chip under the history filters. The history filters
 * live in the URL and are read here, so the headline, the wins and the list's
 * first page come from one fetch wave for the same filters. The diff-shape
 * charts follow as context.
 *
 * `?view=changes` swaps all of that for the history grouped by merged change
 * (`ByChangeView`), `?view=events` for what moved between index runs
 * (`EventsView`), `?view=review` for a change's blast radius before it lands
 * (`ImpactTab`, once Code Health's blast-radius tab); the commit view stays
 * the default.
 */
export default async function CommitsPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<ByChangeParams & Record<string, string | string[] | undefined>>;
}) {
  const { id } = await params;
  const sp = await searchParams;
  const view = parseCommitsView(sp.view);
  const base = `/repos/${id}`;
  const t = await getTranslations("views.commits");
  const toggle = <CommitsViewToggle repoId={id} view={view} />;

  if (view === "changes") {
    const tc = await getTranslations("views.changes");
    return (
      <PageShell
        icon={<GitCommitHorizontal />}
        title={t("title")}
        description={tc("description")}
        actions={toggle}
      >
        <ByChangeView repoId={id} params={sp} />
      </PageShell>
    );
  }

  if (view === "events") {
    const te = await getTranslations("views.events");
    return (
      <PageShell
        icon={<GitCommitHorizontal />}
        title={t("title")}
        description={te("description")}
        actions={toggle}
      >
        <EventsView repoId={id} />
      </PageShell>
    );
  }

  const filters = parseCommitFilters(sp);
  const now = new Date();
  const apiFilters = historyFilters(filters, now);
  const narrowed = isNarrowed(filters);
  const winsRange = historyFilters({ ...filters, impact: "all", hotspots: false, area: undefined }, now);

  // One wave. The scatter plots its own repo-wide recency sample: it explains
  // the scoring model, not the filtered list. The summary leaves the impact
  // filter out so the quick filters keep every option's size.
  const [stats, evolution, trend, firstPage, recent, summary, wins] = await Promise.all([
    safeFetch(() => getCommitStats(id)),
    safeFetch(() => getCommitEvolution(id)),
    safeFetch(() => getAgentTrend(id)),
    safeFetch(() =>
      getCommitsPage(id, { ...apiFilters, sort: "date", authorship: "all", limit: PAGE_SIZE }),
    ),
    safeFetch(() => getCommitsPage(id, { sort: "date", limit: SCATTER_SAMPLE })),
    safeFetch(() => getCommitHealthSummary(id, historyFilters(filters, now, { withImpact: false }))),
    // `/wins` defaults to the past 30 days; all time asks from the epoch.
    safeFetch(() =>
      getWins(id, {
        since: winsRange.since ?? new Date(0).toISOString(),
        ...(winsRange.until ? { until: winsRange.until } : {}),
        ...(winsRange.author ? { author: winsRange.author } : {}),
        top: WINS_TOP,
      }),
    ),
  ]);

  const total = stats?.total_commits ?? firstPage?.total ?? 0;

  if (!firstPage || total === 0) {
    return (
      <PageShell
        icon={<GitCommitHorizontal />}
        title={t("title")}
        description={t("emptyDescription")}
        actions={toggle}
      >
        <EmptyState
          title={t("emptyTitle")}
          description={t("emptyHint")}
          action={{ label: t("emptyAction"), href: `${base}/overview` }}
        />
      </PageShell>
    );
  }

  const read = summary ? readPeriodHealth(summary) : null;

  return (
    <PageShell
      icon={<GitCommitHorizontal />}
      title={t("title")}
      description={t("description")}
      actions={toggle}
    >
      <CommitsPeriod repoId={id} filters={filters} summary={summary} wins={wins} />

      <OverviewSection
        title={t("listTitle")}
        description={t("listDescription")}
        action={<CredibilityInfoButton />}
      >
        <CommitQueue
          repoId={id}
          initial={firstPage}
          total={firstPage.total}
          filters={filters}
          apiFilters={apiFilters}
          narrowed={narrowed}
          impactCounts={
            read && summary
              ? { all: summary.total, better: read.better, worse: read.worse, none: read.neutral }
              : undefined
          }
          attributionEnabled={trend?.enabled !== false}
          // Repo-wide figures: shown only when nothing narrows the list, so a
          // chip never counts a different set than the one beneath it.
          counts={
            stats && !narrowed
              ? {
                  all: stats.total_commits,
                  high: stats.high_priority_count,
                  fixes: stats.fix_commit_count,
                }
              : undefined
          }
        />
      </OverviewSection>

      {evolution && evolution.total_commits > 0 && (
        <OverviewSection
          title={t("evolutionTitle")}
          description={t("evolutionDescription")}
        >
          <CodeEvolutionChart evolution={evolution} />
        </OverviewSection>
      )}

      {trend && trend.enabled && trend.agent_commits > 0 && <AgentTrendStrip trend={trend} />}

      <OverviewSection
        title={t("scoreBehaviourTitle")}
        description={t("scoreBehaviourDescription")}
        action={
          <SectionLink href={`${base}/code-health`} LinkComponent={Link}>
            {t("changeRisk")}
          </SectionLink>
        }
      >
        <CommitDistribution stats={stats} recent={recent?.items ?? []} />
      </OverviewSection>

      <CommitDetailSheet repoId={id} />
    </PageShell>
  );
}
