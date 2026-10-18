"use client";

import { useMemo, useState, useTransition } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import useSWRInfinite from "swr/infinite";
import { useQueryState } from "nuqs";
import { useTranslations } from "next-intl";
import {
  CommitTable,
  type CommitAuthorship,
  type CommitKind,
  type CommitSort,
} from "@repowise-dev/ui/commits/commit-table";
import {
  HistoryFilters,
  type HealthImpact,
  type HistoryFilterValue,
} from "@repowise-dev/ui/commits/history-filters";
import type { HistoryFilters as ApiHistoryFilters } from "@repowise-dev/types/history";
import { getCommitsPage } from "@/lib/api/git";
import type { CommitResponse, Paginated } from "@/lib/api/types";
import { commitFiltersQuery } from "@/lib/commit-filters";
import { useHealthDeltaLabels, useHistoryFilterLabels } from "./history-labels";

const PAGE_SIZE = 50;

/**
 * The commit list: the history filters, then every commit led by what it did
 * to code health.
 *
 * The history filters are URL state and server-driven: a change writes the
 * query string and the page re-renders on the server, so the headline, the
 * wins and this list's first page arrive together for the same filters.
 * `apiFilters` is that server's reading of the URL (its clock picks the
 * window), passed down so every later page asks for the same range.
 *
 * Sort, review priority and authorship stay local refinements of the list.
 * Pages accumulate through `offset`; selecting a row writes `?commit=` for the
 * detail sheet, a separate island.
 */
export function CommitQueue({
  repoId,
  initial,
  total,
  counts,
  filters,
  apiFilters,
  impactCounts,
  narrowed,
  attributionEnabled = true,
}: {
  repoId: string;
  initial: Paginated<CommitResponse>;
  total: number;
  /** Repo-wide chip counts; only meaningful when nothing narrows the list. */
  counts?: { all: number; high: number; fixes: number } | undefined;
  filters: HistoryFilterValue;
  apiFilters: ApiHistoryFilters;
  /** Per-impact counts under the other filters, from the period summary. */
  impactCounts?: Record<HealthImpact, number> | undefined;
  narrowed: boolean;
  /** False when AI attribution is off for the repo: no authorship filter. */
  attributionEnabled?: boolean;
}) {
  const [sort, setSort] = useState<CommitSort>("date");
  const [authorship, setAuthorship] = useState<CommitAuthorship>("all");
  const [kind, setKind] = useState<CommitKind>("all");
  const [, setSelectedSha] = useQueryState("commit");
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [pending, startTransition] = useTransition();
  const t = useTranslations("views.commits");
  const healthLabels = useHealthDeltaLabels();
  const filterLabels = useHistoryFilterLabels();

  const pristine = sort === "date" && authorship === "all" && kind === "all";
  const filterKey = JSON.stringify(apiFilters);

  const { data, setSize, isLoading, isValidating } = useSWRInfinite<Paginated<CommitResponse>>(
    (pageIndex, previous) => {
      if (previous && !previous.has_more) return null;
      return `commits:${repoId}:${sort}:${authorship}:${kind}:${filterKey}:${pageIndex}`;
    },
    (key) => {
      const pageIndex = parseInt(key.split(":").pop() as string, 10);
      return getCommitsPage(repoId, {
        ...apiFilters,
        sort,
        authorship,
        kind,
        limit: PAGE_SIZE,
        offset: pageIndex * PAGE_SIZE,
      });
    },
    {
      revalidateOnFocus: false,
      revalidateFirstPage: false,
      keepPreviousData: true,
      // The first page is server-rendered for these filters, so an untouched
      // list fetches nothing.
      fallbackData: pristine ? [initial] : undefined,
      revalidateOnMount: !pristine,
      // A filter change re-renders the server with that filter's first page,
      // so the new key's fallback is already fresh.
      revalidateIfStale: !pristine,
    },
  );

  const list = useMemo(
    () => (data ? data.flatMap((p) => p.items) : initial.items),
    [data, initial.items],
  );
  const loadedTotal = data && data.length > 0 ? data[0].total : total;
  const hasMore = data ? !!data[data.length - 1]?.has_more : initial.has_more;

  // Every filter change resets to one page; SWRInfinite keeps `size` across
  // key changes otherwise, which would refetch every page of the new filter.
  const reset = () => void setSize(1);

  const applyFilters = (patch: Partial<HistoryFilterValue>) => {
    const query = commitFiltersQuery(new URLSearchParams(searchParams.toString()), patch);
    reset();
    startTransition(() => router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false }));
  };

  return (
    <div className="space-y-4">
      <HistoryFilters
        value={filters}
        onChange={applyFilters}
        counts={impactCounts}
        labels={filterLabels}
      />
      <div aria-busy={pending} className={pending ? "opacity-60 transition-opacity" : undefined}>
        <CommitTable
          commits={list}
          sort={sort}
          onSortChange={(s) => {
            setSort(s);
            reset();
          }}
          authorship={authorship}
          onAuthorshipChange={
            attributionEnabled
              ? (a) => {
                  setAuthorship(a);
                  reset();
                }
              : undefined
          }
          kind={kind}
          onKindChange={(k) => {
            setKind(k);
            reset();
          }}
          counts={counts}
          onSelect={(c) => void setSelectedSha(c.sha)}
          total={loadedTotal}
          hasMore={hasMore}
          loadingMore={isValidating && !isLoading}
          onLoadMore={() => void setSize((n) => n + 1)}
          healthLabels={healthLabels}
          hotspotLabel={t("hotspotMark")}
          narrowed={narrowed}
        />
      </div>
    </div>
  );
}
