"use client";

/**
 * Code Health's landing surface, on the section design language.
 *
 * The shape is: one lede that leads with the defect score and says in prose
 * what it means, then the galaxy map as the page's spine with its inspector
 * beside it, then the host's hotspot and trend sections. Grouping is hairlines
 * and vertical rhythm, not boxes.
 *
 * The map's lens switcher and key live around the canvas rather than on it
 * (`chrome="none"`), so the reader is not looking at the field through a stack
 * of glass panels.
 *
 * The rail is an inspector. It used to hold the repository's top findings
 * permanently, which made it a second findings list: it never described the
 * object under the cursor and it never changed when the lens did. It now shows
 * the selection when there is one and the field's ranked list otherwise, and
 * both follow the active lens. The findings queue lives on its own tab.
 *
 * Presentation + orchestration only: the host injects data, links, and the
 * file-detail drawer through a {@link CodeHealthAdapter}, so web and hosted
 * render the same view from different backends.
 */

import { useMemo, useState } from "react";
import type { ReactNode } from "react";
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
import { MapFieldList, MapInspector } from "./map/inspector";
import type { CodeHealthAdapter } from "./code-health-adapter";

export type HealthPillar = "all" | "defect" | "maintainability" | "performance";

/** Map height. The inspector is height-matched so the rail never outgrows the
 *  field it is inspecting. */
const MAP_HEIGHT = 720;

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
}) {
  const { cacheKey } = adapter;
  const { data: overview, isLoading, error, mutate } = useSWR<HealthOverviewResponse>(
    `code-health-overview:${cacheKey}`,
    () => adapter.getOverview(25),
    { revalidateOnFocus: false },
  );

  const [localSelected, setLocalSelected] = useState<string | null>(null);
  const selected = selectedPath !== undefined ? selectedPath : localSelected;
  const setSelected = (path: string | null) => {
    if (onSelectPath) onSelectPath(path);
    else setLocalSelected(path);
  };

  const [mapQuery, setMapQuery] = useState("");

  const files = mapFeed?.files as CodeHealthMapFile[] | undefined;
  const scope = useMemo(() => (mapFeed ? scopeFromFeed(mapFeed) : undefined), [mapFeed]);
  const selectedFile = useMemo(
    () => (selected ? files?.find((f) => f.file_path === selected) ?? null : null),
    [files, selected],
  );

  // CodeHealthMap's optional props, omitted rather than passed as `undefined`
  // (strict optional props in the shared lib).
  const mapExtra: {
    lenses?: CodeHealthOverlay[];
    overlayLoading?: boolean;
    scope?: MapScope;
    highlightPaths?: string[];
  } = {};
  if (lenses) mapExtra.lenses = lenses;
  if (overlayLoading !== undefined) mapExtra.overlayLoading = overlayLoading;
  if (scope) mapExtra.scope = scope;
  if (highlightPaths?.length) mapExtra.highlightPaths = highlightPaths;

  if (isLoading) {
    return (
      <div className="flex flex-col gap-6">
        {/* Shapes and widths match the real layout. A skeleton that does not
            causes a reflow when content lands, which reads as slower than
            showing nothing. */}
        <Skeleton className="h-12 w-40 rounded-lg" />
        <Skeleton className="h-20 w-full max-w-[54ch] rounded-lg" />
        <Skeleton className="h-[74px] w-full" />
        <Skeleton className="w-full rounded-xl" style={{ height: MAP_HEIGHT }} />
      </div>
    );
  }

  if (error) {
    return (
      <ApiError
        title="Couldn't load health data"
        message={`${toFriendlyMessage(error)} Index this repo if it has not been indexed yet.`}
        onRetry={() => void mutate()}
      />
    );
  }

  if (!overview) return null;

  return (
    <div className="flex flex-col gap-6 sm:gap-8">
      <CodeHealthLede
        summary={overview.summary}
        accuracy={overview.defect_accuracy ?? null}
        distribution={overview.distribution ?? null}
      />

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
            {!files ? (
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

          {/* Inspector, height-matched to the map. Separated by space, not by
              a rule: a vertical hairline down the side of the canvas would
              turn the map into a trench. */}
          <aside className="flex flex-col gap-3 lg:sticky lg:top-4 lg:h-[772px]">
            <div className="relative">
              <Search className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--color-text-tertiary)]" />
              <input
                value={mapQuery}
                onChange={(e) => setMapQuery(e.target.value)}
                placeholder="Find a file in the map…"
                aria-label="Find a file in the map"
                className="w-full rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] py-1.5 pl-7 pr-2 text-xs focus:border-[var(--color-border-hover)] focus:outline-none"
              />
            </div>

            {selectedFile ? (
              <MapInspector
                file={selectedFile}
                overlay={overlay}
                onOpen={(p) => setSelected(p)}
                onClose={() => setSelected(null)}
              />
            ) : selected ? (
              <p className="rounded-lg border border-dashed border-[var(--color-border-default)] p-3 text-xs text-[var(--color-text-tertiary)]">
                <span className="font-mono">{selected}</span> is not in the drawn field, so
                there is no node to inspect. Its details still open below.
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

      {hotspotsSlot}
      {trendSlot}

      {adapter.renderFileDrawer({
        filePath: selected,
        onClose: () => setSelected(null),
        lens: overlay,
      })}
    </div>
  );
}
