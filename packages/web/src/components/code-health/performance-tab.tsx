"use client";

import { useCallback, useMemo } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { PerformanceView, type PerformanceViewAdapter } from "@repowise-dev/ui/health";
import { fileEntityPath, symbolEntityPath } from "@repowise-dev/ui/shared/entity";
import {
  getPerformanceOpportunities,
  getPerformanceOpportunity,
  getPerformanceOpportunityFindings,
  listHealthFindings,
} from "@/lib/api/code-health";
import { getRefactoringPlan } from "@/lib/api/refactoring";

/** The queue's own filter state, kept out of the page's `tab` and `lens`. */
const FILTER_PARAM = "perf";

export function PerformanceTab({ repoId }: { repoId: string }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  // Held stable across renders: the queue memoizes its rows against it, and a
  // fresh object every render would defeat that for twenty rows at a time.
  const adapter: PerformanceViewAdapter = useMemo(() => {
    const prefix = `/repos/${repoId}`;
    return {
      cacheKey: repoId,
      listFindings: (options) => listHealthFindings(repoId, options),
      getPerformanceOpportunities: (options) => getPerformanceOpportunities(repoId, options),
      getPerformanceOpportunity: (opportunityId, options) =>
        getPerformanceOpportunity(repoId, opportunityId, options),
      getPerformanceOpportunityFindings: (opportunityId, options) =>
        getPerformanceOpportunityFindings(repoId, opportunityId, options),
      getRefactoringPlan: (planId) => getRefactoringPlan(repoId, planId),
      refactoringPlanHref: (planId) =>
        `/repos/${repoId}/refactoring?type=performance_fix&plan=${encodeURIComponent(planId)}`,
      fileHref: (path) => fileEntityPath(prefix, path),
      symbolHref: (symbolId) => symbolEntityPath(prefix, symbolId),
      navigate: (href) => router.push(href),
    };
  }, [repoId, router]);

  // A filtered queue is worth sharing and worth surviving a reload, so the
  // serialized state rides in one parameter beside the page's own.
  const onFiltersChange = useCallback(
    (search: string) => {
      const sp = new URLSearchParams(searchParams.toString());
      if (search) sp.set(FILTER_PARAM, search);
      else sp.delete(FILTER_PARAM);
      const qs = sp.toString();
      router.replace(qs ? `?${qs}` : "?", { scroll: false });
    },
    [router, searchParams],
  );

  return (
    <PerformanceView
      adapter={adapter}
      initialFilters={searchParams.get(FILTER_PARAM) ?? undefined}
      onFiltersChange={onFiltersChange}
    />
  );
}
