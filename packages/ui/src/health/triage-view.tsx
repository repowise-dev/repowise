"use client";

/**
 * Code Health's landing surface, on the section design language.
 *
 * The shape is: one lede that leads with the defect score and says in prose
 * what it means, then the galaxy map as the page's spine with its inspector
 * beside it, then the host's hotspot and trend sections. Grouping is hairlines
 * and vertical rhythm, not boxes — the previous version stacked a collapsible
 * accuracy banner, three bordered signal tiles, a bordered stat strip and a
 * bordered map, then floated three more glass panels on top of the map itself,
 * which is chrome sitting on the one thing the reader came to look at.
 *
 * The map's lens switcher and legend now live around the canvas rather than on
 * it (`chrome="none"`), which is also what let the floating panels go: one of
 * them painted `--color-bg-glass`, a token still pinned to the graphite the
 * dark ramp moved off in July, so those panels were rendering a surface colour
 * that exists nowhere else in the app.
 *
 * Presentation + orchestration only: the host injects data, links, and the
 * file-detail drawer through a {@link CodeHealthAdapter}, so web and hosted
 * render the same view from different backends.
 */

import { useCallback, useRef, useState } from "react";
import type { ReactNode } from "react";
import useSWR from "swr";
import { Search } from "lucide-react";
import type {
  HealthFilesResponse,
  HealthOverviewResponse,
  HealthTrendResponse,
} from "@repowise-dev/types/health";

import { Skeleton } from "../ui/skeleton";
import { ApiError } from "../shared/api-error";
import { toFriendlyMessage } from "../lib/errors";
import { OverviewSection } from "../overview/section";

import { CodeHealthLede } from "./code-health-lede";
import { BiomarkerList } from "./biomarker-list";
import {
  CodeHealthMap,
  MapLegend,
  MapLensSwitcher,
  type CodeHealthMapFile,
  type CodeHealthOverlay,
} from "./code-health-map";
import { FileSpotlight } from "./code-health-controls";
import { type Severity } from "./tokens";
import type { CodeHealthAdapter } from "./code-health-adapter";

export type HealthPillar = "all" | "defect" | "maintainability" | "performance";

/** Map height. The inspector is height-matched to it so the rail never outgrows
 *  the field it is inspecting. */
const MAP_HEIGHT = 720;

export function TriageView({
  adapter,
  trend: _trend,
  overlay = "health",
  onOverlayChange,
  lenses,
  mapFiles,
  overlayLoading,
  pillar: controlledPillar,
  onPillarChange,
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
  /** Map files fetched once by the host (shared across overlays). */
  mapFiles?: HealthFilesResponse;
  /** The active lens's per-file signal is still loading (e.g. churn). */
  overlayLoading?: boolean;
  /**
   * Findings pillar filter. Controlled by the host when it wants to URL-sync
   * the value; falls back to local state otherwise.
   */
  pillar?: HealthPillar;
  onPillarChange?: (pillar: HealthPillar) => void;
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

  // Severity gates the inspector findings list.
  const [minSeverity, setMinSeverity] = useState<Severity | "all">("all");
  const [selectedFile, setSelectedFile] = useState<string | null>(null);

  // ---- Map-driven UI: inspector spotlight + filename dim + search ----
  const [hoverFile, setHoverFile] = useState<CodeHealthMapFile | null>(null);
  const [mapQuery, setMapQuery] = useState("");

  const [pillarState, setPillarState] = useState<HealthPillar>("all");
  const pillar = controlledPillar ?? pillarState;
  const findingsRef = useRef<HTMLDivElement | null>(null);
  const setPillar = useCallback(
    (next: HealthPillar) => {
      if (onPillarChange) onPillarChange(next);
      else setPillarState(next);
    },
    [onPillarChange],
  );

  // Severity + pillar drive the findings list; omit unset filters rather than
  // passing `undefined` (strict optional props in the shared lib).
  const sidebarFilter: {
    minSeverity?: Severity;
    dimension?: "defect" | "maintainability" | "performance";
  } = {};
  if (minSeverity !== "all") sidebarFilter.minSeverity = minSeverity;
  if (pillar !== "all") sidebarFilter.dimension = pillar;

  // CodeHealthMap's optional props, omitted rather than passed as `undefined`.
  const mapExtra: {
    lenses?: CodeHealthOverlay[];
    overlayLoading?: boolean;
  } = {};
  if (lenses) mapExtra.lenses = lenses;
  if (overlayLoading !== undefined) mapExtra.overlayLoading = overlayLoading;

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
        description="Every file as a node, clustered into module galaxies and sized by lines of code. The lens recolors the same field rather than redrawing it. Click a galaxy to zoom, a file to inspect it."
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
            {!mapFiles ? (
              <Skeleton className="w-full rounded-xl" style={{ height: MAP_HEIGHT }} />
            ) : (
              <CodeHealthMap
                files={mapFiles.files}
                search={mapQuery}
                selectedPath={selectedFile}
                onSelectFile={(p) => setSelectedFile(p)}
                onHoverFile={setHoverFile}
                minHeight={MAP_HEIGHT}
                overlay={overlay}
                chrome="none"
                {...mapExtra}
              />
            )}
            <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
              <MapLegend overlay={overlay} loading={overlayLoading ?? false} />
              {mapFiles && (
                <span className="font-mono text-[10px] uppercase tracking-[0.12em] tabular-nums text-[var(--color-text-tertiary)]">
                  {mapFiles.total.toLocaleString()} files
                </span>
              )}
            </div>
          </div>

          {/* Inspector — height-matched to the map. Separated by space, not by
              a rule: a vertical hairline down the side of the canvas would turn
              the map into a trench. */}
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

            <FileSpotlight file={hoverFile} onOpen={(p) => setSelectedFile(p)} />

            <div
              ref={findingsRef}
              className="flex flex-wrap items-center gap-2 border-t border-[var(--color-border-default)] pt-3"
            >
              <h3 className="mr-auto font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
                Findings
              </h3>
              <select
                value={pillar}
                onChange={(e) => setPillar(e.target.value as HealthPillar)}
                aria-label="Health pillar"
                className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2 py-1 text-xs"
              >
                <option value="all">All pillars</option>
                <option value="defect">Defect risk</option>
                <option value="maintainability">Maintainability</option>
                <option value="performance">Performance</option>
              </select>
              <select
                value={minSeverity}
                onChange={(e) => setMinSeverity(e.target.value as Severity | "all")}
                aria-label="Minimum severity"
                className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2 py-1 text-xs"
              >
                <option value="all">All severities</option>
                <option value="low">Low+</option>
                <option value="medium">Medium+</option>
                <option value="high">High+</option>
                <option value="critical">Critical</option>
              </select>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto pb-1 pr-1 lg:pb-0">
              <BiomarkerList
                findings={overview.top_findings}
                compact
                {...sidebarFilter}
                onSelect={(f) => setSelectedFile(f.file_path)}
              />
            </div>
          </aside>
        </div>
      </OverviewSection>

      {hotspotsSlot}
      {trendSlot}

      {adapter.renderFileDrawer({
        filePath: selectedFile,
        onClose: () => setSelectedFile(null),
      })}
    </div>
  );
}
