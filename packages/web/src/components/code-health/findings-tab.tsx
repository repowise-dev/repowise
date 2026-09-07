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
  listHealthFiles,
  listHealthFindings,
  getHealthCoverage,
  updateFindingStatus,
} from "@/lib/api/code-health";
import { HealthFileDrawerHost } from "@/components/health/health-file-drawer-host";
import {
  getFileOpportunity,
  refactoringOpportunityHref,
} from "@/lib/api/file-opportunity";
import type { HealthScope } from "@repowise-dev/types/health";

export function FindingsTab({
  repoId: id,
  scope,
}: {
  repoId: string;
  /** Which half of the repository every figure here describes. */
  scope?: HealthScope;
}) {
  const router = useRouter();

  const prefix = `/repos/${id}`;
  const adapter: CodeHealthAdapter = {
    cacheKey: scope && scope !== "all" ? `${id}:${scope}` : id,
    getOverview: (limit) => getHealthOverview(id, limit, scope),
    listFindings: (opts) =>
      listHealthFindings(id, { ...opts, ...(scope ? { scope } : {}) }),
    getFileOpportunity: (filePath) => getFileOpportunity(id, filePath),
    refactoringOpportunityHref: (opportunityId) =>
      refactoringOpportunityHref(id, opportunityId),
    listFiles: (opts) => listHealthFiles(id, { ...opts, ...(scope ? { scope } : {}) }),
    getHealthWorkQueue: (opts) =>
      getHealthWorkQueue(id, { ...opts, ...(scope ? { scope } : {}) }),
    updateFindingStatus: (findingId, status) =>
      updateFindingStatus(id, findingId, status),
    getCoverage: (opts) => getHealthCoverage(id, opts),
    fileHref: (path) => fileEntityPath(prefix, path),
    symbolHref: (symbolId) => symbolEntityPath(prefix, symbolId),
    navigate: (href) => router.push(href),
    renderFileDrawer: ({ filePath, onClose }) => (
      <HealthFileDrawerHost repoId={id} filePath={filePath} onClose={onClose} />
    ),
  };

  return <FindingsView adapter={adapter} />;
}
