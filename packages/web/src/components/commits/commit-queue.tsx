"use client";

import { useState, useTransition } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQueryState } from "nuqs";
import { useTranslations } from "next-intl";
import {
  CommitKindControl,
  CommitList,
  CommitSearch,
  CommitSortControl,
} from "@repowise-dev/ui/commits/commit-table";
import {
  HistoryFilters,
  type HealthImpact,
  type HistoryFilterValue,
} from "@repowise-dev/ui/commits/history-filters";
import type { HistoryFilters as ApiHistoryFilters } from "@repowise-dev/types/history";
import type { CommitResponse, Paginated } from "@/lib/api/types";
import { commitFiltersQuery } from "@/lib/commit-filters";
import { useHealthDeltaLabels, useHistoryFilterLabels } from "./history-labels";
import { useCommitFilterChips } from "./commit-filter-chips";
import { useCommitPages, type CommitListQuery } from "./use-commit-pages";

/**
 * The commit list: impact, hotspots and window in one row; author, area,
 * search, review priority and sort behind "More filters", each active one a
 * removable chip; then every commit led by what it did to code health.
 *
 * The history filters are URL state and server-driven: a change writes the
 * query string and the page re-renders on the server, so the headline, the
 * wins and this list's first page arrive together. `apiFilters` is that
 * server's reading of the URL, passed down so later pages ask for the same
 * range. Search, review priority and sort stay local refinements. Selecting a
 * row writes `?commit=` for the detail sheet, a separate island.
 */
export function CommitQueue({
  repoId,
  initial,
  counts,
  filters,
  apiFilters,
  impactCounts,
  narrowed,
  impactPending,
}: {
  repoId: string;
  initial: Paginated<CommitResponse>;
  /** Repo-wide review-priority counts; only meaningful when nothing narrows the list. */
  counts?: { all: number; high: number; fixes: number } | undefined;
  filters: HistoryFilterValue;
  apiFilters: ApiHistoryFilters;
  /** Per-impact counts under the other filters, from the period summary. */
  impactCounts?: Record<HealthImpact, number> | undefined;
  narrowed: boolean;
  /** Most of the window is not analysed yet; the impact options wait. */
  impactPending?: boolean | undefined;
}) {
  const [query, setQuery] = useState<CommitListQuery>({ sort: "date", kind: "all" });
  const [search, setSearch] = useState("");
  const [, setSelectedSha] = useQueryState("commit");
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [pending, startTransition] = useTransition();
  const t = useTranslations("views.commits");
  const healthLabels = useHealthDeltaLabels();
  const filterLabels = useHistoryFilterLabels();
  const pages = useCommitPages(repoId, apiFilters, query, initial);

  const refine = (patch: Partial<CommitListQuery>) => {
    setQuery((q) => ({ ...q, ...patch }));
    pages.reset();
  };
  const applyFilters = (patch: Partial<HistoryFilterValue>) => {
    const next = commitFiltersQuery(new URLSearchParams(searchParams.toString()), patch);
    pages.reset();
    startTransition(() => router.replace(next ? `${pathname}?${next}` : pathname, { scroll: false }));
  };
  const { chips, clearAll } = useCommitFilterChips({ filters, search, query, applyFilters, setSearch, refine });

  return (
    <div className="space-y-4">
      <HistoryFilters
        value={filters}
        onChange={applyFilters}
        counts={impactCounts}
        labels={filterLabels}
        chips={chips}
        onClearAll={clearAll}
        impactPending={impactPending}
        more={
          <>
            <CommitSearch value={search} onChange={setSearch} />
            <CommitKindControl value={query.kind} onChange={(kind) => refine({ kind })} counts={counts} />
            <CommitSortControl value={query.sort} onChange={(sort) => refine({ sort })} />
          </>
        }
      />
      <div aria-busy={pending} className={pending ? "opacity-60 transition-opacity" : undefined}>
        <CommitList
          commits={pages.commits}
          search={search}
          narrowed={narrowed || query.kind !== "all"}
          onSelect={(c) => void setSelectedSha(c.sha)}
          total={pages.total}
          hasMore={pages.hasMore}
          loadingMore={pages.loadingMore}
          onLoadMore={pages.loadMore}
          healthLabels={healthLabels}
          hotspotLabel={t("hotspotMark")}
        />
      </div>
    </div>
  );
}
