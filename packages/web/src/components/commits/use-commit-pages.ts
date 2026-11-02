"use client";

import { useMemo } from "react";
import useSWRInfinite from "swr/infinite";
import type { CommitKind, CommitSort } from "@repowise-dev/ui/commits/commit-table";
import type { HistoryFilters } from "@repowise-dev/types/history";
import { getCommitsPage } from "@/lib/api/git";
import type { CommitResponse, Paginated } from "@/lib/api/types";

const PAGE_SIZE = 50;

/** The list's own refinements. No authorship: agent-vs-human is never a
 *  filter on this page. */
export interface CommitListQuery {
  sort: CommitSort;
  kind: CommitKind;
}

/**
 * The commit list's pages for one query. Pages accumulate through `offset`, so
 * the whole history is reachable past the endpoint's 200-row cap. `initial`
 * is the server's first page for these `filters` at the default query, so an
 * untouched list fetches nothing, including after a filter change re-renders
 * the server with the new first page.
 */
export function useCommitPages(
  repoId: string,
  filters: HistoryFilters,
  query: CommitListQuery,
  initial: Paginated<CommitResponse>,
) {
  const pristine = query.sort === "date" && query.kind === "all";
  const filterKey = JSON.stringify(filters);

  const { data, setSize, isLoading, isValidating } = useSWRInfinite<Paginated<CommitResponse>>(
    (pageIndex, previous) => {
      if (previous && !previous.has_more) return null;
      return `commits:${repoId}:${query.sort}:${query.kind}:${filterKey}:${pageIndex}`;
    },
    (key) => {
      const pageIndex = parseInt(key.split(":").pop() as string, 10);
      return getCommitsPage(repoId, {
        ...filters,
        ...query,
        limit: PAGE_SIZE,
        offset: pageIndex * PAGE_SIZE,
      });
    },
    {
      revalidateOnFocus: false,
      revalidateFirstPage: false,
      keepPreviousData: true,
      fallbackData: pristine ? [initial] : undefined,
      revalidateOnMount: !pristine,
      revalidateIfStale: !pristine,
    },
  );

  const commits = useMemo(
    () => (data ? data.flatMap((p) => p.items) : initial.items),
    [data, initial.items],
  );
  return {
    commits,
    total: data && data.length > 0 ? data[0].total : initial.total,
    hasMore: data ? !!data[data.length - 1]?.has_more : initial.has_more,
    loadingMore: isValidating && !isLoading,
    loadMore: () => void setSize((n) => n + 1),
    // SWRInfinite keeps `size` across key changes, which would refetch every
    // page of a new query; each change starts again from one page.
    reset: () => void setSize(1),
  };
}
