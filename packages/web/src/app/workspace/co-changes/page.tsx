"use client";

import { useState } from "react";
import { GitMerge, Filter } from "lucide-react";
import { useWorkspaceCoChanges, useWorkspace } from "@/lib/hooks/use-workspace";
import { CoChangeTable } from "@/components/workspace/co-change-table";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatCard } from "@/components/shared/stat-card";
import { Skeleton } from "@/components/ui/skeleton";

export default function CoChangesPage() {
  const { workspace } = useWorkspace();
  const [repo, setRepo] = useState("");

  const { data, isLoading } = useWorkspaceCoChanges({
    repo: repo || undefined,
    limit: 100,
  });

  const repos = workspace?.repos ?? [];

  const selectClass =
    "rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-1.5 text-sm text-[var(--color-text-primary)] outline-none focus:border-[var(--color-accent-primary)]";

  return (
    <div className="p-5 sm:p-8 space-y-6 max-w-[1200px]">
      {/* Header */}
      <div>
        <div className="flex items-center gap-2.5 mb-1">
          <GitMerge className="h-6 w-6 text-[var(--color-accent-primary)]" />
          <h1 className="text-2xl font-semibold text-[var(--color-text-primary)]">
            Co-Changes
          </h1>
        </div>
        <p className="text-sm text-[var(--color-text-secondary)]">
          Files across repositories that frequently change together — implicit coupling detected from git history.
        </p>
      </div>

      {/* Summary */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
        <StatCard
          label="Total Co-Change Pairs"
          value={data?.total ?? "—"}
          icon={<GitMerge className="h-4 w-4 text-[var(--color-accent-primary)]" />}
        />
        <StatCard
          label="Repo Pairs"
          value={
            data?.co_changes
              ? new Set(
                  data.co_changes.map((cc) =>
                    [cc.source_repo, cc.target_repo].sort().join("↔"),
                  ),
                ).size
              : "—"
          }
          icon={<GitMerge className="h-4 w-4 text-purple-400" />}
        />
        <StatCard
          label="Avg Strength"
          value={
            data?.co_changes && data.co_changes.length > 0
              ? `${Math.round(
                  (data.co_changes.reduce((sum, cc) => sum + cc.strength, 0) /
                    data.co_changes.length) *
                    100,
                )}%`
              : "—"
          }
          icon={<GitMerge className="h-4 w-4 text-orange-400" />}
        />
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3">
        <Filter className="h-4 w-4 text-[var(--color-text-tertiary)]" />
        <span className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
          Filter
        </span>
        <select
          value={repo}
          onChange={(e) => setRepo(e.target.value)}
          className={selectClass}
        >
          <option value="">All Repos</option>
          {repos.map((r) => (
            <option key={r.alias} value={r.alias}>
              {r.alias}
            </option>
          ))}
        </select>
      </div>

      {/* Table */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">
            Co-Change Pairs ({data?.co_changes?.length ?? 0})
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-0">
          {isLoading ? (
            <div className="space-y-3 py-4">
              {Array.from({ length: 8 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : (
            <CoChangeTable coChanges={data?.co_changes ?? []} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
