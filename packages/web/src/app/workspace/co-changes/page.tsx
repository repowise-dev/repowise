"use client";

import { useState, useMemo } from "react";
import { GitMerge, Filter, ArrowLeft, LayoutList, Columns } from "lucide-react";
import { useWorkspaceCoChanges, useWorkspace } from "@/lib/hooks/use-workspace";
import { CoChangeTable } from "@repowise-dev/ui/workspace/co-change-table";
import { RepoPairTable, type RepoPairSummary } from "@repowise-dev/ui/workspace/repo-pair-table";
import { Card, CardContent, CardHeader, CardTitle } from "@repowise-dev/ui/ui/card";
import { StatCard } from "@repowise-dev/ui/shared/stat-card";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { Button } from "@repowise-dev/ui/ui/button";

export default function CoChangesPage() {
  const { workspace } = useWorkspace();
  const [repo, setRepo] = useState("");
  const [viewMode, setViewMode] = useState<"pairs" | "flat">("pairs");
  const [selectedPairId, setSelectedPairId] = useState<string | null>(null);

  const { data, isLoading } = useWorkspaceCoChanges({
    repo: repo || undefined,
    limit: 100,
  });

  const repos = workspace?.repos ?? [];

  const selectClass =
    "rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-1.5 text-sm text-[var(--color-text-primary)] outline-none focus:border-[var(--color-accent-primary)]";

  const repoPairs = useMemo(() => {
    if (!data?.co_changes) return [];
    const map = new Map<string, RepoPairSummary>();
    for (const cc of data.co_changes) {
      const sorted = [cc.source_repo, cc.target_repo].sort();
      const id = `${sorted[0]}↔${sorted[1]}`;
      
      if (!map.has(id)) {
        map.set(id, {
          id,
          repo1: sorted[0],
          repo2: sorted[1],
          filePairCount: 0,
          maxStrength: 0,
          lastDate: "",
        });
      }
      const summary = map.get(id)!;
      summary.filePairCount += 1;
      if (cc.strength > summary.maxStrength) {
        summary.maxStrength = cc.strength;
      }
      if (!summary.lastDate || cc.last_date > summary.lastDate) {
        summary.lastDate = cc.last_date;
      }
    }
    return Array.from(map.values()).sort((a, b) => b.maxStrength - a.maxStrength);
  }, [data?.co_changes]);

  const displayedCoChanges = useMemo(() => {
    if (!data?.co_changes) return [];
    if (viewMode === "pairs" && selectedPairId) {
      return data.co_changes.filter((cc) => {
        const sorted = [cc.source_repo, cc.target_repo].sort();
        return `${sorted[0]}↔${sorted[1]}` === selectedPairId;
      });
    }
    return data.co_changes;
  }, [data?.co_changes, viewMode, selectedPairId]);

  return (
    <div className="p-5 sm:p-8 space-y-6 max-w-[1200px]">
      {/* Header */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <div className="flex items-center gap-2.5">
            <GitMerge className="h-6 w-6 text-[var(--color-accent-primary)]" />
            <h1 className="text-2xl font-semibold text-[var(--color-text-primary)]">
              Co-Changes
            </h1>
          </div>
          <div className="flex items-center rounded-md border border-[var(--color-border-default)] p-1 bg-[var(--color-bg-surface)]">
            <button
              onClick={() => { setViewMode("pairs"); setSelectedPairId(null); }}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded transition-colors ${
                viewMode === "pairs"
                  ? "bg-[var(--color-bg-elevated)] text-[var(--color-text-primary)] shadow-sm"
                  : "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
              }`}
            >
              <Columns className="h-4 w-4" />
              Repo Pairs
            </button>
            <button
              onClick={() => { setViewMode("flat"); setSelectedPairId(null); }}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded transition-colors ${
                viewMode === "flat"
                  ? "bg-[var(--color-bg-elevated)] text-[var(--color-text-primary)] shadow-sm"
                  : "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
              }`}
            >
              <LayoutList className="h-4 w-4" />
              Flat List
            </button>
          </div>
        </div>
        <p className="text-sm text-[var(--color-text-secondary)] mt-2">
          Files the same author committed across repositories within a short time
          window. This is a temporal work-pattern hint from git history, not a
          verified technical dependency, so treat it as a starting point for
          inspection rather than proof of coupling.
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
          value={data?.co_changes ? repoPairs.length : "—"}
          icon={<GitMerge className="h-4 w-4 text-[var(--color-accent-secondary)]" />}
        />
        <StatCard
          label="Avg Strength"
          value={
            data?.co_changes && data.co_changes.length > 0
              ? Math.round(
                  (data.co_changes.reduce((sum, cc) => sum + cc.strength, 0) /
                    data.co_changes.length) *
                    10,
                ) / 10
              : "—"
          }
          icon={<GitMerge className="h-4 w-4 text-[var(--color-accent-primary)]" />}
        />
      </div>

      {/* Filters */}
      <div className="flex items-center justify-between">
        {viewMode === "pairs" && selectedPairId ? (
          <Button 
            variant="outline" 
            size="sm" 
            onClick={() => setSelectedPairId(null)}
            className="flex items-center gap-1.5"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to Repo Pairs
          </Button>
        ) : (
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
        )}
      </div>

      {/* Table */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">
            {viewMode === "pairs" && !selectedPairId 
              ? `Repo Pairs (${repoPairs.length})` 
              : `Co-Change Pairs (${displayedCoChanges.length})`}
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-0">
          {isLoading ? (
            <div className="space-y-3 py-4">
              {Array.from({ length: 8 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : viewMode === "pairs" && !selectedPairId ? (
            <RepoPairTable 
              repoPairs={repoPairs} 
              onSelectPair={setSelectedPairId} 
              selectedPairId={selectedPairId}
            />
          ) : (
            <CoChangeTable coChanges={displayedCoChanges} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
