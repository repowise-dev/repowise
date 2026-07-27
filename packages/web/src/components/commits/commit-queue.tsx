"use client";

import { useState } from "react";
import useSWR from "swr";
import { useQueryState } from "nuqs";
import {
  CommitTable,
  type CommitAuthorship,
  type CommitSort,
} from "@repowise-dev/ui/commits/commit-table";
import { getCommitsPage } from "@/lib/api/git";
import type { CommitResponse, Paginated } from "@/lib/api/types";

const PAGE_SIZE = 50;
const MAX_ROWS = 200;

/**
 * The review queue: the one part of this page that genuinely needs client
 * state. Sort, authorship filter and paging all refetch, and selecting a row
 * writes `?commit=` so the detail sheet can be a separate island that shares
 * state through the URL rather than through a common parent — which is what
 * kept the whole page a client component before.
 *
 * The first page is server-rendered and handed in as `initial`, so the table
 * is in the initial HTML and SWR only takes over once the reader changes
 * something.
 */
export function CommitQueue({
  repoId,
  initial,
  total,
}: {
  repoId: string;
  initial: Paginated<CommitResponse>;
  total: number;
}) {
  const [sort, setSort] = useState<CommitSort>("risk");
  const [authorship, setAuthorship] = useState<CommitAuthorship>("all");
  const [limit, setLimit] = useState(PAGE_SIZE);
  const [, setSelectedSha] = useQueryState("commit");

  const pristine = sort === "risk" && authorship === "all" && limit === PAGE_SIZE;

  const { data, isLoading, isValidating } = useSWR<Paginated<CommitResponse>>(
    `commits:${repoId}:${sort}:${authorship}:${limit}`,
    () => getCommitsPage(repoId, { sort, authorship, limit }),
    {
      revalidateOnFocus: false,
      keepPreviousData: true,
      // Nothing to fetch until the reader moves off the server-rendered view.
      fallbackData: pristine ? initial : undefined,
      revalidateOnMount: !pristine,
    },
  );

  const list = data?.items ?? initial.items;

  return (
    <CommitTable
      commits={list}
      sort={sort}
      onSortChange={(s) => {
        setSort(s);
        setLimit(PAGE_SIZE);
      }}
      authorship={authorship}
      onAuthorshipChange={(a) => {
        setAuthorship(a);
        setLimit(PAGE_SIZE);
      }}
      onSelect={(c) => void setSelectedSha(c.sha)}
      total={total}
      hasMore={data?.has_more ?? initial.has_more}
      loadingMore={isValidating && !isLoading}
      onLoadMore={() => setLimit((n) => Math.min(n + PAGE_SIZE, MAX_ROWS))}
    />
  );
}
