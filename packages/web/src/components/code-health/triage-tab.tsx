"use client";

/**
 * Triage host — binds the shared {@link TriageView} to web's `/api` client,
 * `/repos/:id` routing, and the file-detail drawer. The composition itself
 * lives in `@repowise-dev/ui/health`; this file only injects the app-specific
 * pieces so web and hosted render the same view.
 *
 * The map is the areas map: the hierarchy and each area's files load as the
 * reader zooms (`/areas/tree`, `/health/map?area=`), the rail reads
 * `/areas/{path}`, and the selected area lives in `?area=` so a link opens it
 * and the browser's back button climbs back out.
 */

import { useCallback, useMemo, type ReactNode } from "react";
import useSWR from "swr";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  TriageView,
  type CodeHealthAdapter,
  type CodeHealthMapFile,
  type CodeHealthOverlay,
} from "@repowise-dev/ui/health";
import { fileEntityPath, symbolEntityPath } from "@repowise-dev/ui/shared/entity";
import {
  getChurnComplexity,
  getHealthMap,
  getHealthOverview,
  getHealthWorkQueue,
  listHealthFindings,
  getHealthCoverage,
  updateFindingStatus,
  type ChurnComplexityResponse,
  type HealthTrendResponse,
} from "@/lib/api/code-health";
import { getAreaDetail, getAreaTree } from "@/lib/api/areas";
import type { HealthCounts, HealthScope } from "@repowise-dev/types/health";
import { HealthFileDrawerHost } from "@/components/health/health-file-drawer-host";
import { FixFirstSection } from "@/components/code-health/fix-first-section";
import { useAreaRailLabels } from "./area-rail-labels";
import { useIncludeParam } from "./use-include-param";

/**
 * Churn points pulled for the churn lens, joined onto the area-loaded rows by
 * path. Large enough to cover every churned file in a repository this size;
 * fetched only while the churn lens is on.
 */
const CHURN_POINT_LIMIT = 5000;

/** The selected area in `?area=`, pushed so back and forward walk the levels. */
function useAreaParam() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const area = searchParams.get("area") ?? "";
  const setArea = useCallback(
    (next: string) => {
      // Re-selecting the open area is not a step to go back over.
      if (next === area) return;
      const sp = new URLSearchParams(searchParams.toString());
      if (next) sp.set("area", next);
      else sp.delete("area");
      // A file belongs to the level it was opened from.
      sp.delete("file");
      const qs = sp.toString();
      router.push(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [router, pathname, searchParams, area],
  );
  return [area, setArea] as const;
}

/** Churn percentiles by path, fetched only while the churn lens is on. */
function useChurnExtras(repoId: string, wanted: boolean) {
  const { data, isLoading } = useSWR<ChurnComplexityResponse>(
    wanted ? `health-churn-complexity:${repoId}` : null,
    () => getChurnComplexity(repoId, { limit: CHURN_POINT_LIMIT }),
    { revalidateOnFocus: false, keepPreviousData: true },
  );
  const extras = useMemo(
    () =>
      data
        ? new Map<string, Partial<CodeHealthMapFile>>(
            data.points.map((p) => [p.file_path, { churn_percentile: p.churn_percentile }]),
          )
        : undefined,
    [data],
  );
  return { extras, loading: wanted && isLoading && !data };
}

export function TriageTab({
  repoId: id,
  trend,
  overlay = "health",
  onOverlayChange,
  lenses,
  primaryLenses,
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
  /** Lenses offered in the switcher. */
  lenses?: CodeHealthOverlay[];
  /** Lenses shown as segments; the rest sit behind "More". */
  primaryLenses?: CodeHealthOverlay[];
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
  const [area, setArea] = useAreaParam();
  const [include, setInclude] = useIncludeParam();
  // The map holds tests, docs examples, build, vendored and generated files
  // out by default (the server names the set); `include` brings some back.
  const hold = { hold: "map" as const, include };
  const railLabels = useAreaRailLabels();
  const churn = useChurnExtras(id, overlay === "churn");

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
    getHealthWorkQueue: (opts) =>
      getHealthWorkQueue(id, { ...opts, ...(scope ? { scope } : {}), ...(counts ? { counts } : {}) }),
    updateFindingStatus: (findingId, status) =>
      updateFindingStatus(id, findingId, status),
    getCoverage: (opts) => getHealthCoverage(id, opts),
    getAreaTree: (root, depth) => getAreaTree(id, { root, depth, ...hold }),
    getAreaDetail: (path) => getAreaDetail(id, path, hold),
    // The area's complete list: the server's cap is the most it serves.
    getAreaFiles: (path) =>
      getHealthMap(id, {
        area: path,
        cap: 8000,
        ...hold,
        ...(scope ? { scope } : {}),
        ...(counts ? { counts } : {}),
      }),
    // No area links yet: neither the Findings tab nor the Contributors page
    // reads `?area=`, and a link must not claim a scope its page drops.
    fileHref: (path) => fileEntityPath(prefix, path),
    symbolHref: (symbolId) => symbolEntityPath(prefix, symbolId),
    navigate: (href) => router.push(href),
    renderFileDrawer: ({ filePath, onClose, lens, inline }) => (
      <HealthFileDrawerHost
        repoId={id}
        filePath={filePath}
        onClose={onClose}
        counts={counts}
        {...(lens ? { lens } : {})}
        {...(inline ? { inline } : {})}
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
      primaryLenses={primaryLenses}
      overlayLoading={churn.loading}
      selectedPath={selectedPath}
      onSelectPath={onSelectPath}
      highlightPaths={highlightPaths}
      hotspotsSlot={hotspotsSlot}
      trendSlot={trendSlot}
      leadSlot={<FixFirstSection repoId={id} />}
      area={area}
      onAreaChange={setArea}
      fileExtras={churn.extras}
      railLabels={railLabels}
      include={include}
      onIncludeChange={setInclude}
    />
  );
}
