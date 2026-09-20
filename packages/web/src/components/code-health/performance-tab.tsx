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
/** Shared with the map, which uses the same name to pin a cause's files. */
const OPPORTUNITY_PARAM = "opportunity";

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
      // Onto the one galaxy, on the performance lens, with the cause's files
      // pinned. The map guarantees them a node rather than hoping the size
      // ranking happened to include them.
      mapHref: (opportunityId, filePath) =>
        `/repos/${repoId}/code-health?lens=performance&opportunity=${encodeURIComponent(opportunityId)}&file=${encodeURIComponent(filePath)}`,
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

  // The open cause rides in the URL beside the filters, so the link the map
  // and the file drawer mint opens it, and so an inspected cause is itself a
  // link worth sending to somebody.
  const onOpenOpportunityChange = useCallback(
    (opportunityId: string | null) => {
      const sp = new URLSearchParams(searchParams.toString());
      if (opportunityId) sp.set(OPPORTUNITY_PARAM, opportunityId);
      else sp.delete(OPPORTUNITY_PARAM);
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
      openOpportunityId={searchParams.get(OPPORTUNITY_PARAM)}
      onOpenOpportunityChange={onOpenOpportunityChange}
    />
  );
}
