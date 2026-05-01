import type { Metadata } from "next";
import { Flame, Shield } from "lucide-react";
import { StatCard } from "@repowise/ui/shared/stat-card";
import { HotspotTable } from "@/components/git/hotspot-table";
import { ContributorBar } from "@/components/git/contributor-bar";
import { ChurnHistogram } from "@/components/git/churn-histogram";
import { CommitCategoryDonut } from "@/components/git/commit-category-donut";
import { RiskDistributionChart } from "@/components/git/risk-distribution-chart";
import { Card, CardContent, CardHeader, CardTitle } from "@repowise/ui/ui/card";
import { getHotspots, getGitSummary } from "@/lib/api/git";
import { formatNumber } from "@repowise/ui/lib/format";

export const metadata: Metadata = { title: "Hotspots" };

export default async function HotspotsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  let hotspots: Awaited<ReturnType<typeof getHotspots>> = [];
  let summary: Awaited<ReturnType<typeof getGitSummary>> | null = null;

  let loadError: Error | null = null;
  try {
    [hotspots, summary] = await Promise.all([
      getHotspots(id, 100),
      getGitSummary(id),
    ]);
  } catch (err) {
    loadError = err instanceof Error ? err : new Error("Couldn't load hotspots");
    console.error("[hotspots] load failed:", err);
  }

  // Aggregate commit categories across all hotspot files
  const aggregatedCategories: Record<string, number> = {};
  for (const h of hotspots) {
    for (const [cat, count] of Object.entries(h.commit_categories || {})) {
      aggregatedCategories[cat] = (aggregatedCategories[cat] || 0) + (count as number);
    }
  }

  const busFactorRiskCount = hotspots.filter((h) => h.bus_factor <= 1).length;

  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-[1600px]">
      <div>
        <h1 className="text-xl font-semibold text-[var(--color-text-primary)] mb-1 flex items-center gap-2">
          <Flame className="h-5 w-5 text-red-500" />
          Hotspots
        </h1>
        <p className="text-sm text-[var(--color-text-secondary)]">
          High-churn files — where the most risky code lives.
        </p>
      </div>

      {loadError && hotspots.length === 0 && (
        <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-4 text-sm text-[var(--color-text-secondary)]">
          Couldn&apos;t load hotspots. The data may not be ready yet — try running a sync first.
        </div>
      )}

      {/* Summary cards */}
      {summary && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
          <StatCard
            label="Hotspot Files"
            value={formatNumber(summary.hotspot_count)}
            description="high-churn files"
          />
          <StatCard
            label="Stable Files"
            value={formatNumber(summary.stable_count)}
            description="low-churn files"
          />
          <StatCard
            label="Total Files"
            value={formatNumber(summary.total_files)}
            description="with git history"
          />
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

      {/* Charts row */}
      {hotspots.length > 0 && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Churn Distribution</CardTitle>
            </CardHeader>
            <CardContent className="pt-0">
              <ChurnHistogram hotspots={hotspots} />
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

      {/* Risk distribution */}
      {hotspots.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Risk Distribution</CardTitle>
            <p className="text-xs text-[var(--color-text-tertiary)]">
              Composite risk score: churn (40%) + bus factor (35%) + trend (25%)
            </p>
          </CardHeader>
          <CardContent className="pt-0">
            <RiskDistributionChart hotspots={hotspots} />
          </CardContent>
        </Card>
      )}

      {/* Table + sidebar */}
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-4">
        <div className="xl:col-span-3">
          <HotspotTable hotspots={hotspots} />
        </div>

        {/* Top owners leaderboard */}
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
