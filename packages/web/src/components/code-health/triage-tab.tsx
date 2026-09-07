"use client";

/**
 * Triage host — binds the shared {@link TriageView} to web's `/api` client,
 * `/repos/:id` routing, and the file-detail drawer. The composition itself
 * lives in `@repowise-dev/ui/health`; this file only injects the app-specific
 * pieces so web and hosted render the same view.
 */

import type { ReactNode } from "react";
import { useRouter } from "next/navigation";
import {
  TriageView,
  type CodeHealthAdapter,
  type CodeHealthOverlay,
} from "@repowise-dev/ui/health";
import { fileEntityPath, symbolEntityPath } from "@repowise-dev/ui/shared/entity";
import {
  getHealthOverview,
  getHealthWorkQueue,
  listHealthFiles,
  listHealthFindings,
  getHealthCoverage,
  updateFindingStatus,
  type HealthTrendResponse,
  type HealthMapFeed,
} from "@/lib/api/code-health";
import type { HealthScope } from "@repowise-dev/types/health";
import { HealthFileDrawerHost } from "@/components/health/health-file-drawer-host";

export function TriageTab({
  repoId: id,
  trend,
  overlay = "health",
  onOverlayChange,
  lenses,
  mapFeed,
  overlayLoading,
  selectedPath,
  onSelectPath,
  highlightPaths,
  hotspotsSlot,
  trendSlot,
  scope,
}: {
  repoId: string;
  /** Trend fetched once at the page level. */
  trend?: HealthTrendResponse;
  /** Active map lens, owned by the page so the spine is shared across tabs. */
  overlay?: CodeHealthOverlay;
  onOverlayChange?: (overlay: CodeHealthOverlay) => void;
  /** Lenses offered in the switcher, including any the page joined in. */
  lenses?: CodeHealthOverlay[];
  /** The bounded field, fetched once at the page level and shared by lenses. */
  mapFeed?: HealthMapFeed;
  /** The active lens's per-file signal is still loading (e.g. churn). */
  overlayLoading?: boolean;
  /** Selection, URL-synced by the page so a link can open one file. */
  selectedPath?: string | null;
  onSelectPath?: (path: string | null) => void;
  /** Extra paths to mark, for a link into one opportunity's files. */
  highlightPaths?: string[];
  /** Sections composed by the page and rendered under the map. */
  hotspotsSlot?: ReactNode;
  trendSlot?: ReactNode;
  /** Which half of the repository every figure here describes. */
  scope?: HealthScope;
}) {
  const router = useRouter();

  const prefix = `/repos/${id}`;
  // Scope rides in the cache key as well as the query: the views key their SWR
  // off it, so narrowing has to make a different key or the first population
  // stays on screen under the second one's label.
  const adapter: CodeHealthAdapter = {
    cacheKey: scope && scope !== "all" ? `${id}:${scope}` : id,
    getOverview: (limit) => getHealthOverview(id, limit, scope),
    listFindings: (opts) =>
      listHealthFindings(id, { ...opts, ...(scope ? { scope } : {}) }),
    listFiles: (opts) => listHealthFiles(id, { ...opts, ...(scope ? { scope } : {}) }),
    getHealthWorkQueue: (opts) =>
      getHealthWorkQueue(id, { ...opts, ...(scope ? { scope } : {}) }),
    updateFindingStatus: (findingId, status) =>
      updateFindingStatus(id, findingId, status),
    getCoverage: (opts) => getHealthCoverage(id, opts),
    fileHref: (path) => fileEntityPath(prefix, path),
    symbolHref: (symbolId) => symbolEntityPath(prefix, symbolId),
    navigate: (href) => router.push(href),
    renderFileDrawer: ({ filePath, onClose, lens }) => (
      <HealthFileDrawerHost
        repoId={id}
        filePath={filePath}
        onClose={onClose}
        {...(lens ? { lens } : {})}
      />
    ),
  };

  return (
    <TriageView
      adapter={adapter}
      trend={trend}
      overlay={overlay}
      onOverlayChange={onOverlayChange}
      lenses={lenses}
      mapFeed={mapFeed}
      overlayLoading={overlayLoading}
      selectedPath={selectedPath}
      onSelectPath={onSelectPath}
      highlightPaths={highlightPaths}
      hotspotsSlot={hotspotsSlot}
      trendSlot={trendSlot}
    />
  );
}
