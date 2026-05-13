"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { OwnershipTreemap } from "@repowise-dev/ui/git/ownership-treemap";
import { listModuleHealth } from "@/lib/api/modules";
import type { ModuleHealthSummary } from "@/lib/api/types";
import { BusFactorPanel } from "@repowise-dev/ui/git/bus-factor-panel";
import { ContributorBar } from "@repowise-dev/ui/git/contributor-bar";
import { HotspotTrendStrip } from "@repowise-dev/ui/git/hotspot-trend-strip";
import { Card, CardContent, CardHeader, CardTitle } from "@repowise-dev/ui/ui/card";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { getOwnership, getGitSummary, getHotspots } from "@/lib/api/git";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import { cn } from "@/lib/utils/cn";
import type { OwnershipEntry, GitSummaryResponse, HotspotResponse } from "@/lib/api/types";

type Granularity = "module" | "file";

export function HeatmapTab({ repoId }: { repoId: string }) {
  const router = useRouter();
  const [granularity, setGranularity] = useState<Granularity>("module");

  const { data: entries, isLoading: loadingEntries } = useSWR<OwnershipEntry[]>(
    `ownership:${repoId}:${granularity}`,
    () => getOwnership(repoId, granularity),
    { revalidateOnFocus: false },
  );
  const { data: summary } = useSWR<GitSummaryResponse>(
    `git-summary:${repoId}`,
    () => getGitSummary(repoId),
    { revalidateOnFocus: false },
  );
  const { data: hotspots } = useSWR<HotspotResponse[]>(
    `hotspots-for-ownership:${repoId}`,
    () => getHotspots(repoId, 100),
    { revalidateOnFocus: false },
  );
  // Module-level bus-factor overlay for the treemap. Only needed at module
  // granularity — file mode uses its own per-row bus factor in tooltips.
  const { data: moduleHealth } = useSWR(
    granularity === "module" ? `module-health:${repoId}:all` : null,
    () => listModuleHealth(repoId, { sort: "file_count", limit: 500 }),
    { revalidateOnFocus: false },
  );

  const busFactorByModule = useMemo(() => {
    const map: Record<string, number> = {};
    for (const m of (moduleHealth?.items ?? []) as ModuleHealthSummary[]) {
      map[m.module_path] = Math.round(m.median_bus_factor);
    }
    return map;
  }, [moduleHealth]);

  const list = entries ?? [];

  return (
    <div className="space-y-6">
      {loadingEntries && list.length === 0 ? (
        <Skeleton className="h-72 w-full" />
      ) : list.length > 0 ? (
        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm">Ownership Heatmap</CardTitle>
              <div className="flex rounded-md border border-[var(--color-border-default)] overflow-hidden text-xs">
                {(["module", "file"] as Granularity[]).map((g) => (
                  <button
                    key={g}
                    onClick={() => setGranularity(g)}
                    className={cn(
                      "px-3 py-1.5 font-medium transition-colors capitalize",
                      granularity === g
                        ? "bg-[var(--color-accent-primary)] text-[var(--color-text-inverse)]"
                        : "bg-transparent text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]",
                    )}
                  >
                    {g}
                  </button>
                ))}
              </div>
            </div>
            <p className="text-xs text-[var(--color-text-tertiary)] pt-1">
              Tile size = code volume. Color = primary owner. Border = bus factor —
              red ≤1, amber 2, green ≥3. Dashed amber outline marks silo modules
              (one owner &gt;80%).
            </p>
          </CardHeader>
          <CardContent className="pt-0">
            <OwnershipTreemap
              entries={list}
              busFactorByModule={granularity === "module" ? busFactorByModule : undefined}
              onSelect={(e) =>
                router.push(
                  `/repos/${repoId}/ownership?module=${encodeURIComponent(e.module_path)}`,
                )
              }
            />
          </CardContent>
        </Card>
      ) : null}

      {hotspots && hotspots.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Hotspot trend</CardTitle>
            <p className="text-xs text-[var(--color-text-tertiary)]">
              Top files by churn. Heating arrows mark files where the last 30 days are
              outpacing the 90-day baseline.
            </p>
          </CardHeader>
          <CardContent className="pt-0">
            <HotspotTrendStrip hotspots={hotspots} />
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {hotspots && hotspots.length > 0 && (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Bus Factor Analysis</CardTitle>
            </CardHeader>
            <CardContent className="pt-0">
              <BusFactorPanel hotspots={hotspots} />
            </CardContent>
          </Card>
        )}

        {summary && summary.top_owners.length > 0 && (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Top Contributors</CardTitle>
            </CardHeader>
            <CardContent className="pt-0">
              <ContributorBar owners={summary.top_owners} />
              <div className="mt-3 space-y-1.5">
                {summary.top_owners.slice(0, 10).map((o, i) => {
                  const key = o.email ?? `name:${o.name}`;
                  return (
                    <button
                      key={o.email || `owner-${i}`}
                      onClick={() =>
                        router.push(`/repos/${repoId}/owners/${encodeURIComponent(key)}`)
                      }
                      className="flex w-full items-center justify-between rounded-md px-2 py-1 text-xs hover:bg-[var(--color-bg-elevated)]"
                    >
                      <span className="text-[var(--color-text-secondary)] truncate text-left">{o.name}</span>
                      <span className="text-[var(--color-text-tertiary)] tabular-nums ml-2">
                        {Math.round((o.pct ?? 0) * 100)}%
                      </span>
                    </button>
                  );
                })}
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
