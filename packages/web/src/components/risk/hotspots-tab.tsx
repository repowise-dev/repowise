"use client";

import useSWR from "swr";
import { useFileCardHost } from "@/components/shared/file-card-host";
import type { HotspotResponse as HotspotRowType } from "@/lib/api/types";
import type { FileCardData } from "@repowise-dev/ui/shared/file-card";
import { HotspotTable } from "@repowise-dev/ui/git/hotspot-table";
import { ContributorBar } from "@repowise-dev/ui/git/contributor-bar";
import { ChurnHistogram } from "@repowise-dev/ui/git/churn-histogram";
import { CommitCategoryDonut } from "@repowise-dev/ui/git/commit-category-donut";
import { RiskDistributionChart } from "@repowise-dev/ui/git/risk-distribution-chart";
import { ChurnVsBusFactorScatter } from "@repowise-dev/ui/git/churn-vs-bus-factor-scatter";
import { Card, CardContent, CardHeader, CardTitle } from "@repowise-dev/ui/ui/card";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { getHotspots, getGitSummary } from "@/lib/api/git";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import type { GitSummaryResponse, HotspotResponse } from "@/lib/api/types";

function hotspotToFileCard(h: HotspotRowType): FileCardData {
  return {
    file_path: h.file_path,
    git: {
      churn_percentile: h.churn_percentile,
      commit_count_90d: h.commit_count_90d,
      lines_added_90d: h.lines_added_90d,
      lines_deleted_90d: h.lines_deleted_90d,
      bus_factor: h.bus_factor,
      primary_owner: h.primary_owner,
      is_hotspot: h.is_hotspot,
      temporal_hotspot_score: h.temporal_hotspot_score,
    },
  };
}

export function HotspotsTab({ repoId }: { repoId: string }) {
  const { showFile, dialog } = useFileCardHost(repoId);
  const { data: hotspots, isLoading: loadingHotspots, error: hotspotsError } = useSWR<HotspotResponse[]>(
    `risk-hotspots:${repoId}`,
    () => getHotspots(repoId, 100),
    { revalidateOnFocus: false },
  );
  const { data: summary } = useSWR<GitSummaryResponse>(
    `git-summary:${repoId}`,
    () => getGitSummary(repoId),
    { revalidateOnFocus: false },
  );

  const list = hotspots ?? [];
  const aggregatedCategories: Record<string, number> = {};
  for (const h of list) {
    for (const [cat, count] of Object.entries(h.commit_categories || {})) {
      aggregatedCategories[cat] = (aggregatedCategories[cat] || 0) + (count as number);
    }
  }

  if (loadingHotspots && list.length === 0) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-72 w-full" />
      </div>
    );
  }

  if (hotspotsError && list.length === 0) {
    return (
      <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-4 text-sm text-[var(--color-text-secondary)]">
        Couldn&apos;t load hotspots. The data may not be ready yet — try running a sync first.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {list.length > 0 && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Churn Distribution</CardTitle>
            </CardHeader>
            <CardContent className="pt-0">
              <ChurnHistogram hotspots={list} />
            </CardContent>
          </Card>

          {Object.keys(aggregatedCategories).length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">Commit Types</CardTitle>
              </CardHeader>
              <CardContent className="pt-0 flex items-center justify-center">
                <CommitCategoryDonut categories={aggregatedCategories} />
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {list.length > 0 && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Risk Distribution</CardTitle>
              <p className="text-xs text-[var(--color-text-tertiary)]">
                Composite risk score: churn (40%) + bus factor (35%) + trend (25%)
              </p>
            </CardHeader>
            <CardContent className="pt-0">
              <RiskDistributionChart hotspots={list} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Churn × bus factor</CardTitle>
              <p className="text-xs text-[var(--color-text-tertiary)]">
                Top-right is the danger zone: high churn and a single owner. Bubble size
                reflects 90-day commit count.
              </p>
            </CardHeader>
            <CardContent className="pt-0">
              <ChurnVsBusFactorScatter hotspots={list} />
            </CardContent>
          </Card>
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-4">
        <div className="xl:col-span-3">
          <HotspotTable hotspots={list} repoId={repoId} onSelect={(h) => showFile(hotspotToFileCard(h))} />
        </div>

        {summary && summary.top_owners.length > 0 && (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Top Owners</CardTitle>
            </CardHeader>
            <CardContent className="pt-0">
              <ContributorBar owners={summary.top_owners} />
              <div className="mt-3 space-y-1.5">
                {summary.top_owners.slice(0, 5).map((o, i) => (
                  <div key={o.email || `owner-${i}`} className="flex items-center justify-between text-xs">
                    <span
                      className="text-[var(--color-text-secondary)] truncate"
                      title={o.email ? `${o.name} <${o.email}>` : o.name}
                    >
                      {o.name}
                    </span>
                    <span className="text-[var(--color-text-tertiary)] tabular-nums ml-2">
                      {formatNumber(o.file_count)} files ({Math.round(o.pct * 100)}%)
                    </span>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        )}
      </div>
      {dialog}
    </div>
  );
}
