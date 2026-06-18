"use client";

import useSWR from "swr";
import { ContributorsStrip, type StripProvenance } from "@repowise-dev/ui/git/contributors-strip";
import { Card, CardContent, CardHeader } from "@repowise-dev/ui/ui/card";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { getAgentTrend, getGitSummary } from "@/lib/api/git";

const SWR_OPTS = { revalidateOnFocus: false, revalidateOnReconnect: false };

/**
 * Overview contributors strip — who owns the code, plus agent-vs-human
 * authorship when provenance is indexed. Ownership comes from the git
 * summary; provenance is best-effort (older indexes / repos with no agent
 * commits simply omit that row).
 */
export function ContributorsStripCard({ repoId }: { repoId: string }) {
  const { data: summary, isLoading } = useSWR(
    `overview-git-summary:${repoId}`,
    () => getGitSummary(repoId, 50),
    SWR_OPTS,
  );
  // Provenance is optional: swallow errors so the strip still renders owners.
  const { data: trend } = useSWR(
    `overview-agent-trend:${repoId}`,
    () => getAgentTrend(repoId).catch(() => null),
    SWR_OPTS,
  );

  if (isLoading) {
    return (
      <Card className="shadow-sm">
        <CardHeader className="pb-2">
          <Skeleton className="h-4 w-32" />
        </CardHeader>
        <CardContent className="pt-0 space-y-2.5">
          <Skeleton className="h-2 w-full rounded-full" />
          <Skeleton className="h-4 w-3/4" />
        </CardContent>
      </Card>
    );
  }

  // git-summary returns pct as a 0–1 fraction; the strip works in 0–100.
  const owners = (summary?.top_owners ?? []).map((o) => ({
    ...o,
    pct: (o.pct ?? 0) * 100,
  }));
  if (owners.length === 0) return null;

  const provenance: StripProvenance | null =
    trend && trend.agent_commits > 0
      ? {
          agentPct: trend.agent_pct,
          agentCommits: trend.agent_commits,
          totalCommits: trend.total_commits,
          agentNames: trend.agent_names ?? [],
        }
      : null;

  return (
    <ContributorsStrip
      owners={owners}
      contributorCount={owners.length}
      provenance={provenance}
      ownersHref={`/repos/${repoId}/owners`}
      commitsHref={`/repos/${repoId}/commits?authorship=agent`}
    />
  );
}
