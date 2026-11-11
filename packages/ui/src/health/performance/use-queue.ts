"use client";

import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import type { PerformanceOpportunityPage } from "@repowise-dev/types/health";

import type { PerformanceViewAdapter } from "./adapter";
import { capabilitiesOf } from "./capabilities";
import { PAGE_SIZE, serverKey, toQuery, type PerformanceFilterState } from "./query";

/**
 * One page of the queue for the server half of the filter state. The view
 * fields (actionability segment, open area) are not in the key, so choosing
 * one never refetches.
 */
export function usePerformanceQueue(adapter: PerformanceViewAdapter, filters: PerformanceFilterState) {
  const load = adapter.getPerformanceOpportunities;
  // What a response proved this server understands. It is part of the cache key
  // so that learning it re-asks with the spelling the server accepts: a shared
  // link can restore a production or tooling context before any response has
  // been seen. It only ever flips for a server that predates the vocabulary.
  const [collapse, setCollapse] = useState(false);
  const key = `${collapse ? "legacy:" : ""}${serverKey(filters)}`;
  const swr = useSWR<PerformanceOpportunityPage>(
    load ? `performance-opportunities:${adapter.cacheKey}:${key}` : null,
    () => load!(toQuery(filters, { limit: PAGE_SIZE, collapseContexts: collapse })),
    { revalidateOnFocus: false, keepPreviousData: true },
  );
  const capabilities = useMemo(() => capabilitiesOf(adapter, swr.data), [adapter, swr.data]);
  useEffect(() => {
    if (swr.data) setCollapse(!capabilitiesOf(adapter, swr.data).canonicalContexts);
  }, [adapter, swr.data]);
  return { ...swr, capabilities, supported: Boolean(load) };
}
