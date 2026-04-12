"use client";

import { useState } from "react";
import useSWR from "swr";
import { useParams } from "next/navigation";
import { Users, Shield } from "lucide-react";
import { StatCard } from "@/components/shared/stat-card";
import { OwnershipTable } from "@/components/git/ownership-table";
import { ContributorBar } from "@/components/git/contributor-bar";
import { OwnershipTreemap } from "@/components/git/ownership-treemap";
import { BusFactorPanel } from "@/components/git/bus-factor-panel";
import { ContributorNetwork } from "@/components/git/contributor-network";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { getOwnership, getGitSummary, getHotspots } from "@/lib/api/git";
import { formatNumber } from "@/lib/utils/format";
import { cn } from "@/lib/utils/cn";
import type { OwnershipEntry, GitSummaryResponse, HotspotResponse } from "@/lib/api/types";

type Granularity = "module" | "file";

export default function OwnershipPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [granularity, setGranularity] = useState<Granularity>("module");

  const { data: entries, isLoading: loadingEntries } = useSWR<OwnershipEntry[]>(
    `ownership:${id}:${granularity}`,
    () => getOwnership(id, granularity),
    { revalidateOnFocus: false },
  );

  const { data: summary } = useSWR<GitSummaryResponse>(
    `git-summary:${id}`,
    () => getGitSummary(id),
    { revalidateOnFocus: false },
  );

  const { data: hotspotData } = useSWR<HotspotResponse[]>(
    `hotspots-for-ownership:${id}`,
    () => getHotspots(id, 100),
    { revalidateOnFocus: false },
  );

  const siloCount = (entries ?? []).filter((e) => e.is_silo).length;
  const busFactorRiskCount = (hotspotData ?? []).filter((h) => h.bus_factor <= 1).length;

  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-[1600px]">
      <div>
        <h1 className="text-xl font-semibold text-[var(--color-text-primary)] mb-1 flex items-center gap-2">
          <Users className="h-5 w-5 text-[var(--color-accent-primary)]" />
          Code Ownership
        </h1>
        <p className="text-sm text-[var(--color-text-secondary)]">
          Who owns what — silo detection and bus factor risk.
        </p>
      </div>

      {/* Summary stats */}
      {summary && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatCard
            label="Total Files"
            value={formatNumber(summary.total_files)}
            description="with git history"
          />
          <StatCard
            label="Silo Modules"
            value={siloCount}
            description="owner >80%"
            trend={siloCount > 0 ? { value: `${siloCount}`, positive: false } : undefined}
          />
          <StatCard
            label="Contributors"
            value={summary.top_owners.length}
            description="unique owners"
          />
          <StatCard
            label="Bus Factor Risk"
            value={formatNumber(busFactorRiskCount)}
            description="files with factor ≤ 1"
            icon={<Shield className="h-4 w-4 text-red-400" />}
          />
        </div>
      )}

      {/* Treemap visualization */}
      {entries && entries.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm">Ownership Map</CardTitle>
              {/* Granularity toggle */}
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
          </CardHeader>
          <CardContent className="pt-0">
            <OwnershipTreemap entries={entries} />
          </CardContent>
        </Card>
      )}

      {/* Bus Factor + Contributor sidebar */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {hotspotData && hotspotData.length > 0 && (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Bus Factor Analysis</CardTitle>
            </CardHeader>
            <CardContent className="pt-0">
              <BusFactorPanel hotspots={hotspotData} />
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
                {summary.top_owners.slice(0, 10).map((o, i) => (
                  <div key={o.email || `owner-${i}`} className="flex items-center justify-between text-xs">
                    <span className="text-[var(--color-text-secondary)] truncate">{o.name}</span>
                    <span className="text-[var(--color-text-tertiary)] tabular-nums ml-2">
                      {Math.round((o.pct ?? 0) * 100)}%
                    </span>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        )}
      </div>

      {/* Contributor Network */}
      {hotspotData && hotspotData.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Contributor Network</CardTitle>
            <p className="text-xs text-[var(--color-text-tertiary)]">
              Contributors linked by shared file ownership — larger nodes own more files
            </p>
          </CardHeader>
          <CardContent className="pt-0">
            <ContributorNetwork hotspots={hotspotData} />
          </CardContent>
        </Card>
      )}

      {/* Detail table */}
      {loadingEntries ? (
        <div className="space-y-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : (
        <OwnershipTable entries={entries ?? []} />
      )}
    </div>
  );
}
