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
import type { HealthCounts, HealthScope } from "@repowise-dev/types/health";
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
  counts,
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
  /** Whether those figures count change history or code shape alone. */
  counts?: HealthCounts;
}) {
  const router = useRouter();

  const prefix = `/repos/${id}`;
  // Scope and counts ride in the cache key as well as the query: the views key
  // their SWR off it, so a re-read has to make a different key or the first
  // population stays on screen under the second one's label. The suffixes
  // compose in the same fixed order the page uses, or the two keys diverge and
  // the overview is fetched twice.
  const adapter: CodeHealthAdapter = {
    cacheKey: `${id}${scope && scope !== "all" ? `:${scope}` : ""}${
      counts && counts !== "everything" ? `:${counts}` : ""
    }`,
    getOverview: (limit) => getHealthOverview(id, limit, scope, counts),
    listFindings: (opts) =>
      listHealthFindings(id, { ...opts, ...(scope ? { scope } : {}), ...(counts ? { counts } : {}) }),
    listFiles: (opts) => listHealthFiles(id, { ...opts, ...(scope ? { scope } : {}), ...(counts ? { counts } : {}) }),
    getHealthWorkQueue: (opts) =>
      getHealthWorkQueue(id, { ...opts, ...(scope ? { scope } : {}), ...(counts ? { counts } : {}) }),
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
        counts={counts}
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
