import type { Metadata } from "next";
import Link from "next/link";
import { GitCommitHorizontal } from "lucide-react";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { OverviewSection, SectionLink } from "@repowise-dev/ui/overview";
import { CommitsLede } from "@repowise-dev/ui/commits/commits-lede";
import { CodeEvolutionChart } from "@repowise-dev/ui/commits/code-evolution-chart";
import { AgentTrendStrip } from "@repowise-dev/ui/commits/agent-trend-strip";
import { CredibilityInfoButton } from "@repowise-dev/ui/commits/credibility-strip";
import { CommitQueue } from "@/components/commits/commit-queue";
import { CommitDetailSheet } from "@/components/commits/commit-detail-sheet";
import { CommitDistribution } from "@/components/commits/commit-distribution";
import {
  getAgentTrend,
  getCommitEvolution,
  getCommitStats,
  getCommitsPage,
} from "@/lib/api/git";
import { getTranslations } from "next-intl/server";

export const metadata: Metadata = { title: "Commits" };

const PAGE_SIZE = 50;
// The scatter's own window. Capped at the endpoint's `limit` ceiling; a few
// hundred dots is also about where the plot stops being readable.
const SCATTER_SAMPLE = 200;

async function safeFetch<T>(fn: () => Promise<T>): Promise<T | null> {
  try {
    return await fn();
  } catch {
    return null;
  }
}

/**
 * A server component, where this used to be one client component with five SWR
 * waves behind it. Nothing above the queue needs client state, so every figure
 * and both charts now arrive in the initial HTML instead of waterfalling in
 * after paint. What stays interactive is three small islands, each hydrating
 * only itself and sharing the selected commit through `?commit=` rather than
 * through a common client parent.
 *
 * Order is deliberate. The queue is what people come here for, so it sits
 * above the analysis charts rather than below them; the evolution chart keeps
 * the top slot because it is the only one about the repo rather than about the
 * scoring model.
 */
export default async function CommitsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const base = `/repos/${id}`;
  const t = await getTranslations("views.commits");

  // One wave. The scatter plots its own recency sample rather than reusing the
  // queue's first page: the feed is risk-sorted, so reusing it would draw only
  // the top tercile and the "here is the whole spread" reading would be a lie.
  const [stats, evolution, trend, firstPage, recent] = await Promise.all([
    safeFetch(() => getCommitStats(id)),
    safeFetch(() => getCommitEvolution(id)),
    safeFetch(() => getAgentTrend(id)),
    safeFetch(() =>
      getCommitsPage(id, { sort: "date", authorship: "all", limit: PAGE_SIZE }),
    ),
    safeFetch(() => getCommitsPage(id, { sort: "date", limit: SCATTER_SAMPLE })),
  ]);

  const total = stats?.total_commits ?? firstPage?.total ?? 0;

  if (!firstPage || total === 0) {
    return (
      <PageShell
        icon={<GitCommitHorizontal className="h-5 w-5 text-[var(--color-accent-primary)]" />}
        title={t("title")}
        description={t("emptyDescription")}
      >
        <p className="max-w-[62ch] text-sm text-[var(--color-text-secondary)]">
          {t("emptyHint")}
        </p>
      </PageShell>
    );
  }

  return (
    <PageShell
      icon={<GitCommitHorizontal className="h-5 w-5 text-[var(--color-accent-primary)]" />}
      title={t("title")}
      description={t("description")}
    >
      {stats && <CommitsLede stats={stats} base={base} LinkComponent={Link} />}

      {evolution && evolution.total_commits > 0 && (
        <OverviewSection
          title={t("evolutionTitle")}
          description={t("evolutionDescription")}
        >
          <CodeEvolutionChart evolution={evolution} />
        </OverviewSection>
      )}

      {trend && trend.agent_commits > 0 && <AgentTrendStrip trend={trend} />}

      <OverviewSection
        title={t("reviewQueueTitle")}
        description={t("reviewQueueDescription")}
        action={<CredibilityInfoButton />}
      >
        <CommitQueue
          repoId={id}
          initial={firstPage}
          total={total}
          counts={
            stats
              ? {
                  all: stats.total_commits,
                  high: stats.high_priority_count,
                  fixes: stats.fix_commit_count,
                }
              : undefined
          }
        />
      </OverviewSection>

      <OverviewSection
        title={t("scoreBehaviourTitle")}
        description={t("scoreBehaviourDescription")}
        action={
          <SectionLink href={`${base}/code-health?tab=triage`} LinkComponent={Link}>
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
