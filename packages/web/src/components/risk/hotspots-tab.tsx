"use client";

import useSWR from "swr";
import { Shield } from "lucide-react";
import { StatCard } from "@repowise-dev/ui/shared/stat-card";
import { HotspotTable } from "@repowise-dev/ui/git/hotspot-table";
import { ContributorBar } from "@repowise-dev/ui/git/contributor-bar";
import { ChurnHistogram } from "@repowise-dev/ui/git/churn-histogram";
import { CommitCategoryDonut } from "@repowise-dev/ui/git/commit-category-donut";
import { RiskDistributionChart } from "@repowise-dev/ui/git/risk-distribution-chart";
import { Card, CardContent, CardHeader, CardTitle } from "@repowise-dev/ui/ui/card";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { getHotspots, getGitSummary } from "@/lib/api/git";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import type { GitSummaryResponse, HotspotResponse } from "@/lib/api/types";

export function HotspotsTab({ repoId }: { repoId: string }) {
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
  const busFactorRiskCount = list.filter((h) => h.bus_factor <= 1).length;

  if (loadingHotspots && list.length === 0) {
    return (
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full rounded-lg" />
          ))}
        </div>
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
      {summary && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
          <StatCard label="Hotspot Files" value={formatNumber(summary.hotspot_count)} description="high-churn files" />
          <StatCard label="Stable Files" value={formatNumber(summary.stable_count)} description="low-churn files" />
          <StatCard label="Total Files" value={formatNumber(summary.total_files)} description="with git history" />
          <StatCard
            label="Avg Churn"
            value={`${Math.round(summary.average_churn_percentile)}%`}
            description="percentile"
          />
          <StatCard
            label="Bus Factor Risk"
            value={formatNumber(busFactorRiskCount)}
            description="files with factor ≤ 1"
            icon={<Shield className="h-4 w-4 text-red-400" />}
          />
        </div>
      )}

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
      )}

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-4">
        <div className="xl:col-span-3">
          <HotspotTable hotspots={list} repoId={repoId} />
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
    </div>
  );
}
