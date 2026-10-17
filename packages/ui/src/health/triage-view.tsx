"use client";

/**
 * Code Health's landing surface, on the section design language.
 *
 * The shape is: one lede that leads with the defect score and says in prose
 * what it means, Fix first under it, then the galaxy map as the page's spine
 * with its search and ranked list beside it, then the host's hotspot and trend
 * sections. Grouping is hairlines and vertical rhythm, not boxes.
 *
 * The map's lens switcher and key live around the canvas rather than on it
 * (`chrome="none"`), so the reader is not looking at the field through a stack
 * of glass panels.
 *
 * Selection has one surface: the file drawer. It is non-modal, so the map stays
 * live behind it and picking another node swaps the drawer in place. The rail
 * used to carry an inspector card for the same file as well, which said less
 * than the drawer beside it and offered an "open" that opened nothing new.
 *
 * Presentation + orchestration only: the host injects data, links, and the
 * file-detail drawer through a {@link CodeHealthAdapter}, so web and hosted
 * render the same view from different backends.
 */

import { useCallback, useMemo, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import useSWR from "swr";
import { Search } from "lucide-react";
import type {
  HealthMapFeed,
  HealthOverviewResponse,
  HealthTrendResponse,
} from "@repowise-dev/types/health";

import { Skeleton } from "../ui/skeleton";
import { ApiError } from "../shared/api-error";
import { toFriendlyMessage } from "../lib/errors";
import { OverviewSection } from "../overview/section";

import { CodeHealthLede } from "./code-health-lede";
import {
  CodeHealthMap,
  MapLegend,
  MapLensSwitcher,
  type CodeHealthMapFile,
  type CodeHealthOverlay,
  type MapScope,
} from "./code-health-map";
import { MapFieldList } from "./map/inspector";
import type { CodeHealthAdapter } from "./code-health-adapter";

export type HealthPillar = "all" | "defect" | "maintainability" | "performance";

/** Map height. The rail is height-matched so it never outgrows the field it lists. */
const MAP_HEIGHT = 720;
/** The legend row under the map plus the gap above it, so the rail ends where the legend does. */
const LEGEND_ROW = 52;

/** Read the server's own selection totals into the shape the map states. */
export function scopeFromFeed(feed: HealthMapFeed): MapScope {
  return {
    shown: feed.shown,
    eligible: feed.eligible_total,
    repository: feed.repository_total,
    cap: feed.cap,
    omitted: {
      files: feed.omitted.files,
      performanceFiles: feed.omitted.performance_files,
      opportunities: feed.omitted.opportunities,
      observations: feed.omitted.observations,
    },
    ...(feed.selection.active_missing.length
      ? { missing: feed.selection.active_missing }
      : {}),
    ...(feed.omitted.held !== undefined
      ? { held: feed.omitted.held, includeHeld: feed.selection.include_held ?? false }
      : {}),
    ...(feed.selection.query
      ? {
          query: {
            text: feed.selection.query,
            matches: feed.selection.query_matches ?? 0,
            shown: feed.selection.query_shown ?? 0,
          },
        }
      : {}),
  };
}

export function TriageView({
  adapter,
  trend: _trend,
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
  leadSlot,
  onDrawMore,
  drawMoreStep,
  onIncludeHeldChange,
  onFindUndrawn,
  mapPending,
  mapError,
  onRetryMap,
}: {
  adapter: CodeHealthAdapter;
  /** Trend fetched once by the host. Consumed by `trendSlot`; accepted here so
   *  the host's existing call site keeps type-checking. */
  trend?: HealthTrendResponse;
  /** Active map lens, owned by the host so the spine is shared across tabs. */
  overlay?: CodeHealthOverlay;
  onOverlayChange?: (overlay: CodeHealthOverlay) => void;
  /** Lenses offered in the switcher. Hosts that join churn in pass it here. */
  lenses?: CodeHealthOverlay[];
  /** The bounded field, fetched once by the host and shared across lenses. */
  mapFeed?: HealthMapFeed;
  /** The active lens's per-file signal is still loading (e.g. churn). */
  overlayLoading?: boolean;
  /**
   * Selected file. Controlled by the host when it wants the selection in the
   * URL, so a link can open the map on one file; local otherwise.
   */
  selectedPath?: string | null;
  onSelectPath?: (path: string | null) => void;
  /** Extra paths to mark, for a link into one opportunity's files. */
  highlightPaths?: string[];
  /**
   * Sections the host composes and hands in, rather than props this view
   * fetches for itself. Hotspots needs git history and trend needs a second
   * endpoint; a host without either passes nothing and the section does not
   * render, instead of an empty state pitching data that will never arrive.
   */
  hotspotsSlot?: ReactNode;
  trendSlot?: ReactNode;
  /**
   * Fix first, under the score. Mounted once, in the same place while the
   * overview loads, fails or lands, so its open state and scope survive. It
   * renders collapsed by default (`FixFirstList`), so the headline keeps the
   * first screen.
   */
  leadSlot?: ReactNode;
  /** Map controls the host backs with a refetch: see {@link CodeHealthMap}. */
  onDrawMore?: () => void;
  drawMoreStep?: number;
  onIncludeHeldChange?: (include: boolean) => void;
  onFindUndrawn?: (query: string) => void;
  /** A refetch of the field is in flight. */
  mapPending?: boolean;
  /** The field's request failed; the map shows it with a retry. */
  mapError?: unknown;
  onRetryMap?: () => void;
}) {
  const { cacheKey } = adapter;
  const { data: overview, isLoading, error, mutate } = useSWR<HealthOverviewResponse>(
    `code-health-overview:${cacheKey}`,
    () => adapter.getOverview(25),
    { revalidateOnFocus: false },
  );

  const [localSelected, setLocalSelected] = useState<string | null>(null);
  const selected = selectedPath !== undefined ? selectedPath : localSelected;
  // Stable across renders, so the memoized map layers do not reconcile on
  // every selection just because the host handed in a fresh callback.
  const onSelectRef = useRef(onSelectPath);
  onSelectRef.current = onSelectPath;
  const setSelected = useCallback((path: string | null) => {
    const select = onSelectRef.current;
    if (select) select(path);
    else setLocalSelected(path);
  }, []);
  const closeDrawer = useCallback(() => setSelected(null), [setSelected]);

  const [mapQuery, setMapQuery] = useState("");

  const files = mapFeed?.files as CodeHealthMapFile[] | undefined;
  const scope = useMemo(() => (mapFeed ? scopeFromFeed(mapFeed) : undefined), [mapFeed]);
  // Only "is it drawn" is needed now that the drawer is the inspector.
  const selectedDrawn = useMemo(
    () => (selected ? (files?.some((f) => f.file_path === selected) ?? false) : false),
    [files, selected],
  );

  // CodeHealthMap's optional props, omitted rather than passed as `undefined`
  // (strict optional props in the shared lib). Memoized so the map sees the
  // same object until one of them actually changes.
  const mapExtra = useMemo(() => {
    const extra: {
      lenses?: CodeHealthOverlay[];
      overlayLoading?: boolean;
      scope?: MapScope;
      highlightPaths?: string[];
      onDrawMore?: () => void;
      drawMoreStep?: number;
      onIncludeHeldChange?: (include: boolean) => void;
      onFindUndrawn?: (query: string) => void;
      pending?: boolean;
    } = {};
    if (lenses) extra.lenses = lenses;
    if (overlayLoading !== undefined) extra.overlayLoading = overlayLoading;
    if (scope) extra.scope = scope;
    if (highlightPaths?.length) extra.highlightPaths = highlightPaths;
    if (onDrawMore) extra.onDrawMore = onDrawMore;
    if (drawMoreStep !== undefined) extra.drawMoreStep = drawMoreStep;
    if (onIncludeHeldChange) extra.onIncludeHeldChange = onIncludeHeldChange;
    if (onFindUndrawn) extra.onFindUndrawn = onFindUndrawn;
    if (mapPending !== undefined) extra.pending = mapPending;
    return extra;
  }, [
    lenses,
    overlayLoading,
    scope,
    highlightPaths,
    onDrawMore,
    drawMoreStep,
    onIncludeHeldChange,
    onFindUndrawn,
    mapPending,
  ]);

  // One node per region, so the children below keep their positions (and
  // `leadSlot` its state) whichever way the overview request goes.
  const lede = isLoading ? (
    // Shapes and widths match the real layout. A skeleton that does not causes
    // a reflow when content lands, which reads as slower than showing nothing.
    <div className="flex flex-col gap-6">
      <Skeleton className="h-12 w-40 rounded-lg" />
      <Skeleton className="h-20 w-full max-w-[54ch] rounded-lg" />
      <Skeleton className="h-[74px] w-full" />
    </div>
  ) : error ? (
    <ApiError
      title="Couldn't load health data"
      message={`${toFriendlyMessage(error)} Index this repo if it has not been indexed yet.`}
      onRetry={() => void mutate()}
    />
  ) : overview ? (
    <CodeHealthLede
      summary={overview.summary}
      accuracy={overview.defect_accuracy ?? null}
      distribution={overview.distribution ?? null}
      pillar={overlay === "maintainability" ? "maintainability" : "health"}
    />
  ) : null;

  const loaded = !isLoading && !error && Boolean(overview);

  return (
    <div className="flex flex-col gap-6 sm:gap-8">
      {lede}
      {/* Fix first has its own request, so it never waits on the overview. */}
      {leadSlot}

      {isLoading ? (
        <Skeleton className="w-full rounded-xl" style={{ height: MAP_HEIGHT }} />
      ) : loaded ? (
        <OverviewSection
          title="Code health map"
          description="Every file as a node, clustered into module galaxies and sized by lines of code. The lens re-marks the same field rather than redrawing it. Click a galaxy to zoom, a file to inspect it."
          action={
            onOverlayChange ? (
              <MapLensSwitcher
                overlay={overlay}
                onOverlayChange={onOverlayChange}
                {...(lenses ? { lenses } : {})}
              />
            ) : undefined
          }
        >
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
            <div className="flex flex-col gap-2.5">
              {!files && mapError ? (
                // A failed field says so, rather than leaving the skeleton up.
                <div className="flex items-center justify-center" style={{ minHeight: MAP_HEIGHT }}>
                  <ApiError
                    title="Couldn't load the map"
                    message={toFriendlyMessage(mapError)}
                    {...(onRetryMap ? { onRetry: onRetryMap } : {})}
                  />
                </div>
              ) : !files ? (
                <Skeleton className="w-full rounded-xl" style={{ height: MAP_HEIGHT }} />
              ) : (
                <CodeHealthMap
                  files={files}
                  search={mapQuery}
                  selectedPath={selected}
                  onSelectFile={setSelected}
                  minHeight={MAP_HEIGHT}
                  overlay={overlay}
                  chrome="none"
                  {...mapExtra}
                />
              )}
              <MapLegend overlay={overlay} loading={overlayLoading ?? false} />
            </div>

            {/* The rail, height-matched to the map and its legend. Separated
                by space, not by a rule: a vertical hairline down the side of
                the canvas would turn the map into a trench. */}
            <aside
              className="flex flex-col gap-3 lg:sticky lg:top-4 lg:h-[var(--rail-h)]"
              style={{ "--rail-h": `${MAP_HEIGHT + LEGEND_ROW}px` } as CSSProperties}
            >
              <div className="relative">
                <Search
                  aria-hidden="true"
                  className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--color-text-tertiary)]"
                />
                <input
                  type="search"
                  value={mapQuery}
                  onChange={(e) => setMapQuery(e.target.value)}
                  placeholder="Find a file in the map…"
                  aria-label="Find a file in the map"
                  className="w-full rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] py-1.5 pl-7 pr-2 text-xs text-[var(--color-text-primary)] placeholder:text-[var(--color-text-tertiary)] hover:border-[var(--color-border-hover)] focus-visible:border-[var(--color-accent-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
                />
              </div>

              {selected && files && !selectedDrawn ? (
                <p className="text-xs text-[var(--color-text-tertiary)]">
                  <span className="break-all font-mono">{selected}</span> is not in the drawn
                  field, so no node is marked. Its details are open in the file panel.
                </p>
              ) : null}

              {files ? (
                <div className="min-h-0 flex-1 overflow-hidden border-t border-[var(--color-border-default)] pt-3">
                  <MapFieldList
                    files={files}
                    overlay={overlay}
                    selectedPath={selected}
                    onSelectFile={setSelected}
                    {...(scope ? { scope } : {})}
                  />
                </div>
              ) : null}
            </aside>
          </div>
        </OverviewSection>
      ) : null}

      {loaded ? hotspotsSlot : null}
      {loaded ? trendSlot : null}

      {/* Outside the loaded branch, so a file linked by URL opens even while
          the overview is still on its way. */}
      {adapter.renderFileDrawer({
        filePath: selected,
        onClose: closeDrawer,
        lens: overlay,
      })}
    </div>
  );
}
