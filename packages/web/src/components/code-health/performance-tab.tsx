"use client";

import { useRouter } from "next/navigation";
import { PerformanceView, type PerformanceViewAdapter } from "@repowise-dev/ui/health";
import { fileEntityPath, symbolEntityPath } from "@repowise-dev/ui/shared/entity";
import {
  getPerformanceOpportunities,
  getPerformanceOpportunityFindings,
  listHealthFindings,
} from "@/lib/api/code-health";
import { getRefactoringPlan } from "@/lib/api/refactoring";

export function PerformanceTab({ repoId }: { repoId: string }) {
  const router = useRouter();
  const prefix = `/repos/${repoId}`;
  const adapter: PerformanceViewAdapter = {
    cacheKey: repoId,
    listFindings: (options) => listHealthFindings(repoId, options),
    getPerformanceOpportunities: (options) => getPerformanceOpportunities(repoId, options),
    getPerformanceOpportunityFindings: (opportunityId, options) =>
      getPerformanceOpportunityFindings(repoId, opportunityId, options),
    getRefactoringPlan: (planId) => getRefactoringPlan(repoId, planId),
    refactoringPlanHref: (planId) =>
      `/repos/${repoId}/refactoring?type=performance_fix&plan=${encodeURIComponent(planId)}`,
    fileHref: (path) => fileEntityPath(prefix, path),
    symbolHref: (symbolId) => symbolEntityPath(prefix, symbolId),
    navigate: (href) => router.push(href),
  };

  return <PerformanceView adapter={adapter} />;
}
