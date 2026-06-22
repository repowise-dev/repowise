"use client";

/**
 * Coverage host — binds the shared {@link CoverageView} to web's `/api` client
 * and `/repos/:id` routing. The composition lives in `@repowise-dev/ui/health`;
 * this file only injects the app-specific data fetchers and navigation.
 */

import { useRouter } from "next/navigation";
import { CoverageView, type CodeHealthAdapter } from "@repowise-dev/ui/health";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import {
  getHealthCoverage,
  getHealthOverview,
  getRefactoringTargets,
  listHealthFiles,
  listHealthFindings,
  updateFindingStatus,
} from "@/lib/api/code-health";

export function CoverageTab({ repoId: id }: { repoId: string }) {
  const router = useRouter();
  const prefix = `/repos/${id}`;

  const adapter: CodeHealthAdapter = {
    cacheKey: id,
    getOverview: (limit) => getHealthOverview(id, limit),
    listFindings: (opts) => listHealthFindings(id, opts),
    listFiles: (opts) => listHealthFiles(id, opts),
    getRefactoringTargets: (opts) => getRefactoringTargets(id, opts),
    updateFindingStatus: (findingId, status) =>
      updateFindingStatus(id, findingId, status),
    getCoverage: (opts) => getHealthCoverage(id, opts),
    fileHref: (path) => fileEntityPath(prefix, path),
    navigate: (href) => router.push(href),
    renderFileDrawer: () => null,
  };

  return <CoverageView adapter={adapter} />;
}
