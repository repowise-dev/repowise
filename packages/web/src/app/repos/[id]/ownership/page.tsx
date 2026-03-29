"use client";

import { useState } from "react";
import useSWR from "swr";
import { useParams } from "next/navigation";
import { Users } from "lucide-react";
import { OwnershipTable } from "@/components/git/ownership-table";
import { ContributorBar } from "@/components/git/contributor-bar";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { getOwnership, getGitSummary } from "@/lib/api/git";
import { formatNumber } from "@/lib/utils/format";
import { cn } from "@/lib/utils/cn";
import type { OwnershipEntry, GitSummaryResponse } from "@/lib/api/types";

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

  const siloCount = (entries ?? []).filter((e) => e.is_silo).length;

  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-6xl">
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
        <div className="grid grid-cols-3 gap-3">
          <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4">
            <p className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-1">
              Total Files
            </p>
            <p className="text-2xl font-bold text-[var(--color-text-primary)] tabular-nums">
              {formatNumber(summary.total_files)}
            </p>
          </div>
          <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4">
            <p className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-1">
              Silo Modules
            </p>
            <p className="text-2xl font-bold text-yellow-500 tabular-nums">{siloCount}</p>
          </div>
          <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4">
            <p className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-1">
              Contributors
            </p>
            <p className="text-2xl font-bold text-[var(--color-text-primary)] tabular-nums">
              {summary.top_owners.length}
            </p>
          </div>
        </div>
      )}

      {/* Granularity toggle */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-[var(--color-text-secondary)]">View by:</span>
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

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
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

        {/* Contributor chart */}
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
    </div>
  );
}
