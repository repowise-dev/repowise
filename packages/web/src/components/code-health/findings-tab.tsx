"use client";

/**
 * Findings host — binds the shared {@link FindingsView} (the fix-next queue,
 * and function-level panels) to web's `/api` client,
 * `/repos/:id` routing, and the file-detail drawer. The composition itself
 * lives in `@repowise-dev/ui/health`; this file only injects the app-specific
 * pieces so web and hosted render the same view.
 */

import { useRouter } from "next/navigation";
import { FindingsView, type CodeHealthAdapter } from "@repowise-dev/ui/health";
import { fileEntityPath, symbolEntityPath } from "@repowise-dev/ui/shared/entity";
import {
  getHealthOverview,
  getHealthWorkQueue,
  listHealthFindings,
  getHealthCoverage,
  updateFindingStatus,
} from "@/lib/api/code-health";
import { HealthFileDrawerHost } from "@/components/health/health-file-drawer-host";
import {
  getFileOpportunity,
  refactoringOpportunityHref,
} from "@/lib/api/file-opportunity";
import type { HealthCounts, HealthScope } from "@repowise-dev/types/health";

export function FindingsTab({
  repoId: id,
  scope,
  counts,
}: {
  repoId: string;
  /** Which half of the repository every figure here describes. */
  scope?: HealthScope;
  /** Whether those figures count change history or code shape alone. */
  counts?: HealthCounts;
}) {
  const router = useRouter();

  const prefix = `/repos/${id}`;
  const adapter: CodeHealthAdapter = {
    cacheKey: `${id}${scope && scope !== "all" ? `:${scope}` : ""}${
      counts && counts !== "everything" ? `:${counts}` : ""
    }`,
    getOverview: (limit) => getHealthOverview(id, limit, scope, counts),
    listFindings: (opts) =>
      listHealthFindings(id, { ...opts, ...(scope ? { scope } : {}), ...(counts ? { counts } : {}) }),
    getFileOpportunity: (filePath) => getFileOpportunity(id, filePath),
    refactoringOpportunityHref: (opportunityId) =>
      refactoringOpportunityHref(id, opportunityId),
    getHealthWorkQueue: (opts) =>
      getHealthWorkQueue(id, { ...opts, ...(scope ? { scope } : {}), ...(counts ? { counts } : {}) }),
    updateFindingStatus: (findingId, status) =>
      updateFindingStatus(id, findingId, status),
    getCoverage: (opts) => getHealthCoverage(id, opts),
    fileHref: (path) => fileEntityPath(prefix, path),
    symbolHref: (symbolId) => symbolEntityPath(prefix, symbolId),
    navigate: (href) => router.push(href),
    renderFileDrawer: ({ filePath, onClose }) => (
      <HealthFileDrawerHost
        repoId={id}
        filePath={filePath}
        onClose={onClose}
        counts={counts}
      />
    ),
  };

  return <FindingsView adapter={adapter} />;
}
