"use client";

/**
 * Triage host — binds the shared {@link TriageView} to web's `/api` client,
 * `/repos/:id` routing, and the file-detail drawer. The composition itself
 * lives in `@repowise-dev/ui/health`; this file only injects the app-specific
 * pieces so web and hosted render the same view.
 */

import { useCallback } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  TriageView,
  type CodeHealthAdapter,
  type CodeHealthOverlay,
  type HealthPillar,
} from "@repowise-dev/ui/health";
import { fileEntityPath, symbolEntityPath } from "@repowise-dev/ui/shared/entity";
import {
  getHealthOverview,
  getRefactoringTargets,
  listHealthFiles,
  listHealthFindings,
  getHealthCoverage,
  updateFindingStatus,
  type HealthTrendResponse,
  type HealthFilesResponse,
} from "@/lib/api/code-health";
import { HealthFileDrawerHost } from "@/components/health/health-file-drawer-host";

export function TriageTab({
  repoId: id,
  trend,
  overlay = "health",
  onOverlayChange,
  mapFiles,
  overlayLoading,
}: {
  repoId: string;
  /** Trend fetched once at the page level — feeds the KPI sparklines. */
  trend?: HealthTrendResponse;
  /** Active map lens, owned by the page so the spine is shared across tabs. */
  overlay?: CodeHealthOverlay;
  onOverlayChange?: (overlay: CodeHealthOverlay) => void;
  /** Map files fetched once at the page level (shared across overlays). */
  mapFiles?: HealthFilesResponse;
  /** The active lens's per-file signal is still loading (e.g. churn). */
  overlayLoading?: boolean;
}) {
  const router = useRouter();
  const searchParams = useSearchParams();

  // Pillar is URL-synced (?pillar=) so the Overview + KPI tiles can deep-link
  // straight into a single dimension's findings.
  const rawPillar = searchParams.get("pillar");
  const pillar: HealthPillar =
    rawPillar === "defect" ||
    rawPillar === "maintainability" ||
    rawPillar === "performance"
      ? rawPillar
      : "all";
  const onPillarChange = useCallback(
    (next: HealthPillar) => {
      const sp = new URLSearchParams(searchParams.toString());
      if (next === "all") sp.delete("pillar");
      else sp.set("pillar", next);
      const qs = sp.toString();
      router.replace(qs ? `?${qs}` : "?", { scroll: false });
    },
    [router, searchParams],
  );

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

  return (
    <TriageView
      adapter={adapter}
      trend={trend}
      overlay={overlay}
      onOverlayChange={onOverlayChange}
      mapFiles={mapFiles}
      overlayLoading={overlayLoading}
      pillar={pillar}
      onPillarChange={onPillarChange}
    />
  );
}
