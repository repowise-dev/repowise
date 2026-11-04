"use client";

import { useCallback, useMemo } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  PerformanceView,
  writePerformanceFilters,
  type PerformanceFilterState,
  type PerformanceViewAdapter,
} from "@repowise-dev/ui/health";
import { fileEntityPath, symbolEntityPath } from "@repowise-dev/ui/shared/entity";
import {
  getPerformanceOpportunities,
  getPerformanceOpportunity,
  getPerformanceOpportunityFindings,
  listHealthFindings,
} from "@/lib/api/code-health";
import { getRefactoringPlan, getRefactoringPlanPrompt } from "@/lib/api/refactoring";
import { getFileContent } from "@/lib/api/files";
import { getRelatedWork, relatedWorkHref } from "@/lib/api/related-work";

/** Shared with the map, which uses the same name to pin a cause's files. */
const OPPORTUNITY_PARAM = "opportunity";

/**
 * The URL as it is now, read at call time. Closing over `useSearchParams`
 * would mint new callbacks on every URL change, and the queue's rows are
 * memoized against them.
 */
function currentParams(): URLSearchParams {
  return new URLSearchParams(window.location.search);
}

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
      getRefactoringPlanPrompt: async (planId, flavor) =>
        (await getRefactoringPlanPrompt(repoId, planId, { flavor })).text,
      refactoringPlanHref: (planId) =>
        `/repos/${repoId}/refactoring?type=performance_fix&plan=${encodeURIComponent(planId)}`,
      // Onto the one galaxy, on the performance lens, with the cause's files
      // pinned. The map guarantees them a node rather than hoping the size
      // ranking happened to include them.
      mapHref: (opportunityId, filePath) =>
        `/repos/${repoId}/code-health?lens=performance&opportunity=${encodeURIComponent(opportunityId)}&file=${encodeURIComponent(filePath)}`,
      fileHref: (path) => fileEntityPath(prefix, path),
      symbolHref: (symbolId) => symbolEntityPath(prefix, symbolId),
      readSource: (path) => getFileContent(repoId, path),
      getRelatedWork: (filePaths) => getRelatedWork(repoId, filePaths),
      relatedWorkHref: relatedWorkHref(repoId),
      navigate: (href) => router.push(href),
    };
  }, [repoId, router]);

  // Every view is a URL: the tab's state rides flat beside the page's own
  // parameters (`ctx`, `sort`, `page`, `area`, ...), and a link minted with
  // the older `perf` blob is still read and rewritten on the first change.
  const onFiltersChange = useCallback(
    (_search: string, state: PerformanceFilterState) => {
      const sp = currentParams();
      writePerformanceFilters(state, sp);
      const qs = sp.toString();
      router.replace(qs ? `?${qs}` : "?", { scroll: false });
    },
    [router],
  );

  // The open cause rides in the URL beside the filters, so the link the map
  // and the file drawer mint opens it, and so an inspected cause is itself a
  // link worth sending to somebody.
  const onOpenOpportunityChange = useCallback(
    (opportunityId: string | null) => {
      const sp = currentParams();
      if (opportunityId) sp.set(OPPORTUNITY_PARAM, opportunityId);
      else sp.delete(OPPORTUNITY_PARAM);
      const qs = sp.toString();
      router.replace(qs ? `?${qs}` : "?", { scroll: false });
    },
    [router],
  );

  return (
    <PerformanceView
      adapter={adapter}
      initialFilters={searchParams.toString()}
      onFiltersChange={onFiltersChange}
      openOpportunityId={searchParams.get(OPPORTUNITY_PARAM)}
      onOpenOpportunityChange={onOpenOpportunityChange}
    />
  );
}
