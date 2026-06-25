"use client";

/**
 * Findings host — binds the shared {@link FindingsView} (the fix-next queue,
 * performance risks, and function-level panels) to web's `/api` client,
 * `/repos/:id` routing, and the file-detail drawer. The composition itself
 * lives in `@repowise-dev/ui/health`; this file only injects the app-specific
 * pieces so web and hosted render the same view.
 */

import { useRouter } from "next/navigation";
import {
  FindingsView,
  type CodeHealthAdapter,
} from "@repowise-dev/ui/health";
import { fileEntityPath, symbolEntityPath } from "@repowise-dev/ui/shared/entity";
import {
  getHealthOverview,
  getRefactoringTargets,
  listHealthFiles,
  listHealthFindings,
  getHealthCoverage,
  updateFindingStatus,
} from "@/lib/api/code-health";
import { HealthFileDrawerHost } from "@/components/health/health-file-drawer-host";

export function FindingsTab({ repoId: id }: { repoId: string }) {
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
    symbolHref: (symbolId) => symbolEntityPath(prefix, symbolId),
    navigate: (href) => router.push(href),
    renderFileDrawer: ({ filePath, onClose }) => (
      <HealthFileDrawerHost repoId={id} filePath={filePath} onClose={onClose} />
    ),
  };

  return <FindingsView adapter={adapter} />;
}
