"use client";

import { useState } from "react";
import useSWR from "swr";
import { useQueryState } from "nuqs";
import { AlertTriangle, Bug, GitCommitHorizontal } from "lucide-react";
import { MetricCard } from "@repowise-dev/ui/shared/metric-card";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@repowise-dev/ui/ui/sheet";
import {
  CommitTable,
  type CommitAuthorship,
  type CommitSort,
} from "@repowise-dev/ui/commits/commit-table";
import { CommitDetailCard } from "@repowise-dev/ui/commits/commit-detail-card";
import { AiPromptButton, AiPromptModal, buildCommitAiPrompt } from "@repowise-dev/ui/health";
import { CredibilityInfoButton } from "@repowise-dev/ui/commits/credibility-strip";
import { CodeEvolutionChart } from "@repowise-dev/ui/commits/code-evolution-chart";
import { AgentTrendStrip } from "@repowise-dev/ui/commits/agent-trend-strip";
import { CommitRiskHistogram } from "@repowise-dev/ui/commits/commit-risk-histogram";
import { CommitRiskScatter } from "@repowise-dev/ui/commits/commit-risk-scatter";
import { CollapsibleSection } from "@repowise-dev/ui/shared/collapsible-section";
import {
  getAgentTrend,
  getCommit,
  getCommitEvolution,
  getCommitStats,
  getCommitsPage,
} from "@/lib/api/git";
import type { CommitResponse, Paginated } from "@/lib/api/types";

const PAGE_SIZE = 50;
// The scatter's own window. Capped at the endpoint's `limit` ceiling; a few
// hundred dots is also about where the plot stops being readable.
const SCATTER_SAMPLE = 200;

export function CommitsExplorer({ repoId }: { repoId: string }) {
  const [sort, setSort] = useState<CommitSort>("risk");
  const [authorship, setAuthorship] = useState<CommitAuthorship>("all");
  const [limit, setLimit] = useState(PAGE_SIZE);
  // ?commit= deep link — entity links from Overview/file pages land here with
  // the detail sheet already open, and the sheet state survives refresh.
  const [selectedSha, setSelectedSha] = useQueryState("commit");
  const [promptOpen, setPromptOpen] = useState(false);

  const { data, isLoading, isValidating, error } = useSWR<Paginated<CommitResponse>>(
    `commits:${repoId}:${sort}:${authorship}:${limit}`,
    () => getCommitsPage(repoId, { sort, authorship, limit }),
    { revalidateOnFocus: false, keepPreviousData: true },
  );

  const { data: trend } = useSWR(
    `agent-trend:${repoId}`,
    () => getAgentTrend(repoId),
    { revalidateOnFocus: false },
  );

  // Headline: the repo's development "story arc" — commit-category mix over time.
  const { data: evolution } = useSWR(
    `commit-evolution:${repoId}`,
    () => getCommitEvolution(repoId),
    { revalidateOnFocus: false },
  );

  // Repo-wide stat-card aggregates. Computed server-side over ALL commits — the
  // loaded page is only a window (and, when risk-sorted, entirely top-tercile),
  // so reducing `list` here would under-count fixes and inflate high-priority.
  const { data: stats } = useSWR(
    `commit-stats:${repoId}`,
    () => getCommitStats(repoId),
    { revalidateOnFocus: false },
  );

  // The scatter plots its own recency sample rather than `list`: the feed
  // defaults to risk-sorted, so reusing it would draw only the top tercile and
  // the "here's the whole spread" reading would be a lie.
  const { data: recent } = useSWR(
    `commits-recent:${repoId}`,
    () => getCommitsPage(repoId, { sort: "date", limit: SCATTER_SAMPLE }),
    { revalidateOnFocus: false },
  );

  const { data: detail, isLoading: detailLoading } = useSWR(
    selectedSha ? `commit:${repoId}:${selectedSha}` : null,
    () => getCommit(repoId, selectedSha as string),
    { revalidateOnFocus: false },
  );

  const list = data?.items ?? [];
  const total = stats?.total_commits ?? data?.total ?? list.length;
  const hasMore = data?.has_more ?? false;
  const recentCommits = recent?.items ?? [];
  // Older indexes predate the histogram aggregate, so treat it as optional.
  const hasHistogram = (stats?.risk_histogram?.length ?? 0) > 0;

  // Prefer the repo-wide aggregates; fall back to the loaded page only until
  // the stats request resolves so the cards aren't blank on first paint.
  const highCount =
    stats?.high_priority_count ?? list.filter((c) => c.review_priority === "high").length;
  const fixCount = stats?.fix_commit_count ?? list.filter((c) => c.is_fix).length;
  const avgEntropy =
    stats?.avg_entropy ??
    (list.length > 0
      ? list.reduce((s, c) => s + (c.entropy || 0), 0) / list.length
      : 0);

  if (isLoading && list.length === 0) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }

  if (error && list.length === 0) {
    return (
      <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-4 text-sm text-[var(--color-text-secondary)]">
        Couldn&apos;t load commits. Per-commit change-risk is captured on the next full index —
        try running a sync first.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {evolution && evolution.total_commits > 0 && (
        <CodeEvolutionChart evolution={evolution} />
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MetricCard
          label="Commits"
          value={total}
          description="with change-risk"
          icon={<GitCommitHorizontal className="h-4 w-4 text-[var(--color-text-tertiary)]" />}
        />
        <MetricCard
          label="High priority"
          value={highCount}
          description="top risk tercile"
          icon={<AlertTriangle className="h-4 w-4 text-[var(--color-error)]" />}
        />
        <MetricCard
          label="Fix commits"
          value={fixCount}
          description="bug-fix subjects"
          icon={<Bug className="h-4 w-4 text-[var(--color-warning)]" />}
        />
        <MetricCard
          label="Avg entropy"
          value={avgEntropy.toFixed(2)}
          description="change diffusion"
        />
      </div>

      {/* Secondary signals — smaller than the headline. Both panels are about
          commits (the page's subject); the file-level hotspot view lives on the
          Risk tab, where it isn't a duplicate. Collapsible because this is a
          demoted detail; the agent-trend strip is a thin band beneath it. */}
      {(hasHistogram || recentCommits.length > 0) && (
        <CollapsibleSection
          title="How risky is a typical commit here?"
          hint={`${total.toLocaleString()} scored`}
          defaultOpen
        >
          <div className="grid gap-3 lg:grid-cols-2">
            {hasHistogram && stats && (
              <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4">
                <h4 className="text-xs font-medium text-[var(--color-text-secondary)]">
                  Score distribution
                </h4>
                <p className="mb-2 text-xs text-[var(--color-text-tertiary)]">
                  Every scored commit in the repo. The dashed lines are the
                  tercile cuts behind each row&apos;s priority pill.
                </p>
                <CommitRiskHistogram stats={stats} />
              </div>
            )}
            {recentCommits.length > 0 && (
              <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4">
                <h4 className="text-xs font-medium text-[var(--color-text-secondary)]">
                  Size vs diffusion
                </h4>
                <p className="mb-2 text-xs text-[var(--color-text-tertiary)]">
                  The {recentCommits.length.toLocaleString()} most recent
                  commits. Big and scattered is what the model penalises — click
                  a dot to open it.
                </p>
                <CommitRiskScatter
                  commits={recentCommits}
                  onSelect={(sha) => void setSelectedSha(sha)}
                />
              </div>
            )}
          </div>
        </CollapsibleSection>
      )}
      {trend && trend.agent_commits > 0 && <AgentTrendStrip trend={trend} />}

      <div className="space-y-2">
        <div className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
          <span>Review-priority queue</span>
          <CredibilityInfoButton />
        </div>

        <CommitTable
          commits={list}
          sort={sort}
          onSortChange={(s) => {
            setSort(s);
            setLimit(PAGE_SIZE);
          }}
          authorship={authorship}
          onAuthorshipChange={(a) => {
            setAuthorship(a);
            setLimit(PAGE_SIZE);
          }}
          onSelect={(c) => void setSelectedSha(c.sha)}
          total={total}
          hasMore={hasMore}
          loadingMore={isValidating && !isLoading}
          onLoadMore={() => setLimit((n) => Math.min(n + PAGE_SIZE, 200))}
        />
      </div>

      <Sheet
        open={selectedSha !== null}
        onOpenChange={(open) => !open && void setSelectedSha(null)}
      >
        <SheetContent side="right" className="w-[440px] max-w-[92vw] sm:w-[560px]">
          <SheetHeader>
            <SheetTitle>Commit change-risk</SheetTitle>
          </SheetHeader>
          <div className="flex-1 overflow-y-auto px-4 pb-6">
            {detailLoading || !detail ? (
              <div className="space-y-3 pt-2">
                <Skeleton className="h-24 w-full" />
                <Skeleton className="h-40 w-full" />
              </div>
            ) : (
              <>
                <div className="flex justify-end pt-1 pb-3">
                  <AiPromptButton
                    label="AI review prompt"
                    onClick={() => setPromptOpen(true)}
                  />
                </div>
                <CommitDetailCard commit={detail} />
              </>
            )}
          </div>
        </SheetContent>
      </Sheet>

      <AiPromptModal
        open={promptOpen}
        onOpenChange={setPromptOpen}
        getPrompt={
          detail
            ? (flavor) =>
                buildCommitAiPrompt({
                  commit: {
                    sha: detail.sha,
                    subject: detail.subject,
                    review_priority: detail.review_priority,
                    risk_percentile: detail.risk_percentile,
                    change_risk_score: detail.change_risk_score,
                    is_fix: detail.is_fix,
                    files_changed: detail.files_changed,
                    lines_added: detail.lines_added,
                    lines_deleted: detail.lines_deleted,
                    entropy: detail.entropy,
                    top_drivers: detail.drivers
                      .filter((d) => d.contribution > 0)
                      .map((d) => d.label),
                    author_name: detail.author_name,
                  },
                  flavor,
                })
            : null
        }
        filePath={detail ? detail.short_sha : null}
        title="AI commit review"
        description="A ready-to-paste prompt that has your AI agent review this commit's change-risk, flag what to scrutinize, and suggest reviewers."
      />
    </div>
  );
}
