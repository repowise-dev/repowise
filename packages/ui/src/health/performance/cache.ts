"use client";

import { useCallback } from "react";
import useSWR, { useSWRConfig } from "swr";
import type { PerformanceOpportunity, PerformanceOpportunityDetail } from "@repowise-dev/types/health";
import type { RefactoringPlan } from "@repowise-dev/types/refactoring";

import { performancePlanDetail } from "../../refactoring/types";
import type { PerformanceViewAdapter } from "./adapter";

/**
 * One cache entry per cause and per plan, whoever asks: the drawer, a deep
 * link, a hover prefetch and the agent prompt all read the same key, so a
 * cause is fetched once however it was reached.
 */

const EVIDENCE_LIMIT = 8;

export const detailKey = (cacheKey: string, id: string) => `performance-opportunity:${cacheKey}:${id}`;
export const planKey = (cacheKey: string, id: string) => `performance-plan:${cacheKey}:${id}`;

/** Read once, never revalidated: an index's causes change only on a new index. */
const ONCE = { revalidateOnFocus: false, revalidateIfStale: false } as const;

const inflight = new Map<string, Promise<unknown>>();

/**
 * One request per key at a time, whoever starts it: a hover prefetch, the
 * drawer's own fetch and a prompt build all join the same promise.
 */
function fetchOnce<T>(key: string, fetcher: () => Promise<T>): Promise<T> {
  const pending = inflight.get(key) as Promise<T> | undefined;
  if (pending) return pending;
  const request = fetcher().finally(() => inflight.delete(key));
  inflight.set(key, request);
  return request;
}

/** Keys whose prefetch failed this session, so later hovers do not retry them. */
const failed = new Set<string>();

export function useOpportunityDetail(
  adapter: PerformanceViewAdapter,
  id: string | null,
): { data: PerformanceOpportunityDetail | undefined; error: unknown } {
  const fetchDetail = adapter.getPerformanceOpportunity;
  const key = id && fetchDetail ? detailKey(adapter.cacheKey, id) : null;
  const { data, error } = useSWR<PerformanceOpportunityDetail>(
    key,
    () => fetchOnce(key!, () => fetchDetail!(id!, { evidenceLimit: EVIDENCE_LIMIT })),
    ONCE,
  );
  return { data, error };
}

export function usePlan(adapter: PerformanceViewAdapter, planId: string | null) {
  const fetchPlan = adapter.getRefactoringPlan;
  const key = planId && fetchPlan ? planKey(adapter.cacheKey, planId) : null;
  return useSWR<RefactoringPlan>(key, () => fetchOnce(key!, () => fetchPlan!(planId!)), ONCE);
}


/**
 * Fetch into the shared cache outside a render: for a prefetch on hover, or a
 * prompt built on click. Returns what is cached when it is, and joins a fetch
 * already in flight rather than starting a second.
 */
export function useCachedLoader() {
  const { cache, mutate } = useSWRConfig();
  return useCallback(
    <T,>(key: string, fetcher: () => Promise<T>): Promise<T> => {
      const hit = cache.get(key)?.data as T | undefined;
      if (hit !== undefined) return Promise.resolve(hit);
      return fetchOnce(key, fetcher).then(
        (value) => mutate(key, value, { revalidate: false }) as Promise<T>,
      );
    },
    [cache, mutate],
  );
}

/**
 * Warm one cause's detail on hover or focus. A key that failed once is not
 * retried by later hovers; opening the cause still asks again.
 */
export function usePrefetchDetail(adapter: PerformanceViewAdapter) {
  const load = useDetailLoader(adapter);
  return useCallback(
    (id: string) => {
      const key = detailKey(adapter.cacheKey, id);
      if (failed.has(key)) return;
      load(id).catch(() => failed.add(key));
    },
    [adapter, load],
  );
}

/** Load one cause's detail into the shared key; null when the host cannot. */
export function useDetailLoader(adapter: PerformanceViewAdapter) {
  const load = useCachedLoader();
  return useCallback(
    (id: string): Promise<PerformanceOpportunityDetail | null> => {
      const fetchDetail = adapter.getPerformanceOpportunity;
      if (!fetchDetail) return Promise.resolve(null);
      return load(detailKey(adapter.cacheKey, id), () =>
        fetchDetail(id, { evidenceLimit: EVIDENCE_LIMIT }),
      );
    },
    [adapter, load],
  );
}

/**
 * Whether a stored plan names this exact cause. A reindexed or reused row
 * must never be handed over as this one's fix, so the plan link and the
 * agent prompt both wait on this verdict.
 */
export function planMatches(plan: RefactoringPlan | undefined | null, opportunity: PerformanceOpportunity): boolean {
  return Boolean(
    plan &&
      plan.id === opportunity.plan_id &&
      plan.refactoring_type === "performance_fix" &&
      performancePlanDetail(plan).opportunityId === opportunity.opportunity_id,
  );
}

/** Load the stored plan into the shared key, returning it only when it matches. */
export function useVerifiedPlanLoader(adapter: PerformanceViewAdapter) {
  const load = useCachedLoader();
  return useCallback(
    async (opportunity: PerformanceOpportunity): Promise<RefactoringPlan | null> => {
      const fetchPlan = adapter.getRefactoringPlan;
      const id = opportunity.plan_status === "available" ? opportunity.plan_id : null;
      if (!id || !fetchPlan) return null;
      const plan = await load(planKey(adapter.cacheKey, id), () => fetchPlan(id));
      return planMatches(plan, opportunity) ? plan : null;
    },
    [adapter, load],
  );
}
