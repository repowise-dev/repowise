"use client";

import { useMemo } from "react";
import { Users, Search } from "lucide-react";
import type { OwnerListEntry } from "@repowise-dev/types/owners";
import { Input } from "../ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../ui/select";
import { Skeleton } from "../ui/skeleton";
import { EmptyState } from "../shared/empty-state";
import { ResultsFooter } from "../shared/results-footer";
import { OwnerCard } from "./owner-card";
import { OwnershipDistributionBar } from "./ownership-distribution-bar";

export type OwnerSortKey =
  | "files_owned"
  | "hotspots_owned"
  | "commit_count_90d"
  | "dead_code_lines_owned"
  | "bus_factor_risk_files";

const SORT_LABELS: Record<OwnerSortKey, string> = {
  files_owned: "Files owned",
  hotspots_owned: "Hotspots owned",
  commit_count_90d: "Recent commits",
  dead_code_lines_owned: "Dead-code burden",
  bus_factor_risk_files: "Bus-factor risk",
};

export interface OwnerDirectoryFilters {
  q: string;
  sort: OwnerSortKey;
}

export interface OwnerDirectoryProps {
  owners: OwnerListEntry[];
  isLoading: boolean;
  isValidating: boolean;
  total: number;
  hasMore: boolean;
  filters: OwnerDirectoryFilters;
  onFiltersChange: (next: OwnerDirectoryFilters) => void;
  onLoadMore: () => void;
  onSelect: (owner: OwnerListEntry) => void;
}

export function OwnerDirectory({
  owners,
  isLoading,
  isValidating,
  total,
  hasMore,
  filters,
  onFiltersChange,
  onLoadMore,
  onSelect,
}: OwnerDirectoryProps) {
  // Spotlight metrics — drives the strip above the directory grid.
  const headline = useMemo(() => {
    const totalFiles = owners.reduce((s, o) => s + o.files_owned, 0);
    const siloOwners = owners.filter((o) => o.silo_modules > 0).length;
    const busRiskOwners = owners.filter((o) => o.bus_factor_risk_files > 0).length;
    const totalDeadLines = owners.reduce((s, o) => s + o.dead_code_lines_owned, 0);
    return { totalFiles, siloOwners, busRiskOwners, totalDeadLines };
  }, [owners]);

  return (
    <div className="space-y-5">
      {owners.length > 0 && (
        <OwnershipDistributionBar
          owners={owners}
          totalContributors={total}
          onSelect={onSelect}
        />
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Headline label="Contributors" value={total} />
        <Headline label="Silo owners" value={headline.siloOwners} tone="warn" />
        <Headline label="Bus-factor risk" value={headline.busRiskOwners} tone="danger" />
        <Headline label="Dead lines owned" value={headline.totalDeadLines} />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[220px]">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-[var(--color-text-tertiary)]" />
          <Input
            value={filters.q}
            onChange={(e) => onFiltersChange({ ...filters, q: e.target.value })}
            placeholder="Filter by name or email…"
            className="pl-8"
          />
        </div>
        <Select
          value={filters.sort}
          onValueChange={(v) => onFiltersChange({ ...filters, sort: v as OwnerSortKey })}
        >
          <SelectTrigger className="w-[200px]">
            <SelectValue placeholder="Sort by" />
          </SelectTrigger>
          <SelectContent>
            {Object.entries(SORT_LABELS).map(([k, label]) => (
              <SelectItem key={k} value={k}>
                {label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {isLoading && owners.length === 0 ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-32 w-full" />
          ))}
        </div>
      ) : owners.length === 0 ? (
        <EmptyState
          icon={<Users className="h-6 w-6" />}
          title="No contributors match"
          description="Adjust the filter or wait for the initial git index to finish."
        />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {owners.map((o) => (
            <OwnerCard key={o.key} owner={o} onSelect={onSelect} />
          ))}
        </div>
      )}

      <ResultsFooter
        shown={owners.length}
        total={total}
        hasMore={hasMore}
        loading={isValidating && !isLoading}
        onLoadMore={onLoadMore}
        noun="contributor"
      />
    </div>
  );
}

function Headline({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: "warn" | "danger";
}) {
  const color =
    tone === "danger" ? "text-[var(--color-error)]" : tone === "warn" ? "text-[var(--color-warning)]" : "text-[var(--color-text-primary)]";
  return (
    <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-3">
      <div className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
        {label}
      </div>
      <div className={`mt-1 text-2xl font-bold tabular-nums ${color}`}>{value}</div>
    </div>
  );
}
