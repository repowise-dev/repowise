"use client";

import { useMemo, useState } from "react";
import useSWRInfinite from "swr/infinite";
import { useQueryState } from "nuqs";
import {
  CommitTable,
  type CommitAuthorship,
  type CommitKind,
  type CommitSort,
} from "@repowise-dev/ui/commits/commit-table";
import { getCommitsPage } from "@/lib/api/git";
import type { CommitResponse, Paginated } from "@/lib/api/types";

const PAGE_SIZE = 50;

/**
 * The review queue: the one part of this page that genuinely needs client
 * state. Sort, the filters and paging all refetch, and selecting a row writes
 * `?commit=` so the detail sheet can be a separate island that shares state
 * through the URL rather than through a common parent.
 *
 * Pages accumulate through `offset` rather than by growing `limit`, so the
 * whole history is reachable: the endpoint caps a single page at 200, which
 * used to be the ceiling on everything the queue could ever show.
 */
export function CommitQueue({
  repoId,
  initial,
  total,
  counts,
}: {
  repoId: string;
  initial: Paginated<CommitResponse>;
  total: number;
  /** Repo-wide chip counts, from the stats endpoint the page already fetches. */
  counts?: { all: number; high: number; fixes: number } | undefined;
}) {
  const [sort, setSort] = useState<CommitSort>("date");
  const [authorship, setAuthorship] = useState<CommitAuthorship>("all");
  const [kind, setKind] = useState<CommitKind>("all");
  const [, setSelectedSha] = useQueryState("commit");

  const pristine = sort === "date" && authorship === "all" && kind === "all";

  const { data, size, setSize, isLoading, isValidating } = useSWRInfinite<
    Paginated<CommitResponse>
  >(
    (pageIndex, previous) => {
      if (previous && !previous.has_more) return null;
      return `commits:${repoId}:${sort}:${authorship}:${kind}:${pageIndex}`;
    },
    (key) => {
      const pageIndex = parseInt(key.split(":").pop() as string, 10);
      return getCommitsPage(repoId, {
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
      // The first page is server-rendered, so an untouched queue fetches nothing.
      fallbackData: pristine ? [initial] : undefined,
      revalidateOnMount: !pristine,
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

  return (
    <CommitTable
      commits={list}
      sort={sort}
      onSortChange={(s) => {
        setSort(s);
        reset();
      }}
      authorship={authorship}
      onAuthorshipChange={(a) => {
        setAuthorship(a);
        reset();
      }}
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
    />
  );
}
