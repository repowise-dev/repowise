"use client";

import { useState } from "react";
import useSWR from "swr";
import { useQueryState } from "nuqs";
import { AlertTriangle, Bug, GitCommitHorizontal } from "lucide-react";
import { StatCard } from "@repowise-dev/ui/shared/stat-card";
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
import { RiskDistributionChart } from "@repowise-dev/ui/git/risk-distribution-chart";
import { CollapsibleSection } from "@repowise-dev/ui/shared/collapsible-section";
import {
  getAgentTrend,
  getCommit,
  getCommitEvolution,
  getCommitStats,
  getCommitsPage,
  getHotspots,
} from "@/lib/api/git";
import type { CommitResponse, Paginated } from "@/lib/api/types";

const PAGE_SIZE = 50;

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

  // Repo-relative risk distribution — turns the credibility strip's
  // "relative to this repo" claim into a picture.
  const { data: hotspots } = useSWR(
    `commits-risk-dist:${repoId}`,
    () => getHotspots(repoId, 25),
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
        <StatCard
          label="Commits"
          value={total}
          description="with change-risk"
          icon={<GitCommitHorizontal className="h-4 w-4 text-[var(--color-text-tertiary)]" />}
        />
        <StatCard
          label="High priority"
          value={highCount}
          description="top risk tercile"
          icon={<AlertTriangle className="h-4 w-4 text-[var(--color-error)]" />}
        />
        <StatCard
          label="Fix commits"
          value={fixCount}
          description="bug-fix subjects"
          icon={<Bug className="h-4 w-4 text-[var(--color-warning)]" />}
        />
        <StatCard
          label="Avg entropy"
          value={avgEntropy.toFixed(2)}
          description="change diffusion"
        />
      </div>

      {/* Secondary signals — smaller than the headline. Risk distribution is
          collapsible (it's a demoted detail); the agent-trend strip is a thin
          full-width band beneath it. */}
      {hotspots && hotspots.length > 0 && (
        <CollapsibleSection
          title="Risk distribution across the riskiest files"
          hint={`${Math.min(12, hotspots.length)} of ${hotspots.length}`}
          defaultOpen
        >
          <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4">
            <RiskDistributionChart hotspots={hotspots} maxBars={12} />
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
