"use client";

import { useCallback, useMemo } from "react";
import useSWRInfinite from "swr/infinite";
import { parseAsString, parseAsStringLiteral, useQueryStates } from "nuqs";
import type { HotspotFilter, HotspotOrder, HotspotSort } from "@repowise-dev/types/git";
import { getHotspotsPage, type HotspotPage } from "@/lib/api/git";
import { useIncludeParam } from "./use-include-param";

/** Rows per page; "Load more" appends the next offset, with no ceiling. */
export const HOTSPOT_PAGE_SIZE = 100;

const FILTERS = ["all", "bus_factor_1", "accelerating", "bug_magnet"] as const satisfies readonly HotspotFilter[];
const SORTS = ["score", "commits", "fixes", "churn"] as const satisfies readonly HotspotSort[];
const ORDERS = ["desc", "asc"] as const satisfies readonly HotspotOrder[];

/** Search, filter and sort live in the URL (`hq`, `hfilter`, `hsort`, `horder`). */
function useHotspotParams() {
  return useQueryStates(
    {
      hq: parseAsString.withDefault(""),
      hfilter: parseAsStringLiteral(FILTERS).withDefault("all"),
      hsort: parseAsStringLiteral(SORTS).withDefault("score"),
      horder: parseAsStringLiteral(ORDERS).withDefault("desc"),
    },
    { history: "replace", scroll: false },
  );
}

/** The loaded pages as one list: rows appended, counts from the first page. */
export function summarizePages(data: HotspotPage[] | undefined) {
  const pages = data ?? [];
  const first = pages[0];
  return {
    items: pages.flatMap((p) => p.items),
    total: first ? first.total : 0,
    facets: first?.facets,
    hasMore: pages.length > 0 && pages[pages.length - 1]!.has_more,
    loaded: pages.length,
  };
}

/**
 * The hotspot list in the map's scope (`hold=map` plus `?include=`), paged by
 * offset. The server searches, filters, sorts and counts; this only appends.
 */
export function useHotspotList(repoId: string) {
  const [params, setParams] = useHotspotParams();
  const [include] = useIncludeParam();
  const query = useMemo(
    () => ({
      q: params.hq,
      filter: params.hfilter,
      sort: params.hsort,
      order: params.horder,
      hold: "map" as const,
      include,
    }),
    [params, include],
  );
  const keyBase = JSON.stringify(query);

  const { data, error, size, setSize, isLoading, isValidating } = useSWRInfinite<HotspotPage>(
    (pageIndex, previous: HotspotPage | null) =>
      previous && !previous.has_more ? null : ["hotspots", repoId, keyBase, pageIndex],
    ([, , , pageIndex]: [string, string, string, number]) =>
      getHotspotsPage(repoId, {
        ...query,
        limit: HOTSPOT_PAGE_SIZE,
        offset: pageIndex * HOTSPOT_PAGE_SIZE,
      }),
    { revalidateOnFocus: false, revalidateFirstPage: false, keepPreviousData: true },
  );

  const pages = useMemo(() => summarizePages(data), [data]);
  const loadMore = useCallback(() => setSize(size + 1), [setSize, size]);

  return {
    ...pages,
    isLoading,
    loadingMore: isValidating && !isLoading && size > pages.loaded,
    error,
    query: params.hq,
    filter: params.hfilter,
    sort: { key: params.hsort, order: params.horder },
    setQuery: (hq: string) => setParams({ hq: hq || null }),
    setFilter: (f: HotspotFilter) => setParams({ hfilter: f === "all" ? null : f }),
    setSort: (s: { key: HotspotSort; order: HotspotOrder }) =>
      setParams({
        hsort: s.key === "score" ? null : s.key,
        horder: s.order === "desc" ? null : s.order,
      }),
    loadMore,
  };
}
