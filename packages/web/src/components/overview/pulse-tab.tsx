"use client";

import useSWR from "swr";
import { CommitsMini } from "@repowise-dev/ui/dashboard/commits-mini";
import { DecisionsTimeline } from "@repowise-dev/ui/dashboard/decisions-timeline";
import { HotspotsMini } from "@repowise-dev/ui/dashboard/hotspots-mini";
import { CommitCategorySparkline } from "@repowise-dev/ui/git/commit-category-sparkline";
import { Card, CardContent, CardHeader, CardTitle } from "@repowise-dev/ui/ui/card";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { getCommitsPage } from "@/lib/api/git";
import type { Hotspot } from "@repowise-dev/types/git";
import type { OverviewDecisionSlim } from "@repowise-dev/types/overview";

interface PulseTabProps {
  repoId: string;
  hotspots: Hotspot[];
  hotspotTotal: number;
  decisions: OverviewDecisionSlim[];
}

/** Default Overview tab: real activity — recent commits, change pressure,
 *  and the decisions timeline. Commits stream in client-side; the hotspot
 *  and decision slices arrive with the summary payload. */
export function PulseTab({ repoId, hotspots, hotspotTotal, decisions }: PulseTabProps) {
  const { data: commitsPage, isLoading } = useSWR(
    `overview-commits:${repoId}`,
    () => getCommitsPage(repoId, { sort: "date", limit: 10 }),
    { revalidateOnFocus: false, revalidateOnReconnect: false },
  );

  const aggregatedCategories: Record<string, number> = {};
  for (const h of hotspots) {
    for (const [cat, count] of Object.entries(h.commit_categories ?? {})) {
      aggregatedCategories[cat] = (aggregatedCategories[cat] || 0) + count;
    }
  }
  const hasCategories = Object.values(aggregatedCategories).some((v) => v > 0);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {isLoading ? (
          <Card>
            <CardContent className="space-y-2 pt-4">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-5 w-full" />
              ))}
            </CardContent>
          </Card>
        ) : (
          <CommitsMini commits={commitsPage?.items ?? []} repoId={repoId} />
        )}
        <DecisionsTimeline decisions={decisions} repoId={repoId} />
      </div>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <HotspotsMini hotspots={hotspots} repoId={repoId} total={hotspotTotal} />
        {hasCategories && (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium">Commit Activity</CardTitle>
            </CardHeader>
            <CardContent>
              <CommitCategorySparkline categories={aggregatedCategories} />
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-2 text-[10px] text-[var(--color-text-tertiary)]">
                <span className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-sm" style={{ background: "var(--color-info)" }} /> Feature</span>
                <span className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-sm" style={{ background: "var(--color-error)" }} /> Fix</span>
                <span className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-sm" style={{ background: "var(--color-accent-secondary)" }} /> Refactor</span>
                <span className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-sm" style={{ background: "var(--color-accent-fill)" }} /> Dependency</span>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
