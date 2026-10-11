"use client";

import { Folder } from "lucide-react";
import type { ModuleHealthSummary } from "@repowise-dev/types/modules";
import { EmptyState } from "../shared/empty-state";
import { Skeleton } from "../ui/skeleton";
import { ResultsFooter } from "../shared/results-footer";
import { ModuleHealthCard } from "./module-health-card";

interface Props {
  modules: ModuleHealthSummary[];
  isLoading: boolean;
  total: number;
  hasMore: boolean;
  loadingMore?: boolean | undefined;
  onLoadMore?: (() => void) | undefined;
  onSelect?: ((m: ModuleHealthSummary) => void) | undefined;
}

/**
 * Grid of {@link ModuleHealthCard}s with empty / loading / pagination
 * states. Server returns lowest-health-first by default; we leave ordering
 * to the parent so this stays presentational.
 */
export function ModuleHealthList({
  modules,
  isLoading,
  total,
  hasMore,
  loadingMore,
  onLoadMore,
  onSelect,
}: Props) {
  if (isLoading && modules.length === 0) {
    return (
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-44 w-full" />
        ))}
      </div>
    );
  }

  if (modules.length === 0) {
    return (
      <EmptyState
        icon={<Folder className="h-6 w-6" />}
        title="No module data yet"
        description="Module health rolls up after the initial git + dead-code scan completes."
      />
    );
  }

  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {modules.map((m) => (
          <ModuleHealthCard key={m.module_path} module={m} onClick={onSelect} />
        ))}
      </div>
      <ResultsFooter
        shown={modules.length}
        total={total}
        hasMore={hasMore}
        loading={Boolean(loadingMore)}
        onLoadMore={onLoadMore ?? (() => {})}
        noun="module"
      />
    </div>
  );
}
