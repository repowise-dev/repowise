"use client";

/**
 * Triage view — the landing surface for Code Health. An airy proof + overview:
 * the "can you trust this score?" headline, the repo KPI strip, and the master–
 * detail Code Health map with its inspector rail. The dense drill-down (the
 * fix-next queue, performance risks, function-level panels) lives in
 * {@link FindingsView} behind its own tab.
 *
 * Presentation + orchestration only: the host injects data, links, and the
 * file-detail drawer through a {@link CodeHealthAdapter}, so web and hosted
 * render the same view from different backends.
 */

import { useCallback, useRef, useState } from "react";
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

import { HealthKpiCards } from "./kpi-cards";
import { BiomarkerList } from "./biomarker-list";
import {
  CodeHealthMap,
  type CodeHealthMapFile,
  type CodeHealthOverlay,
} from "./code-health-map";
import { DefectAccuracyCard } from "./defect-accuracy-card";
import { FileSpotlight } from "./code-health-controls";
import { type Severity } from "./tokens";
import type { CodeHealthAdapter } from "./code-health-adapter";

export type HealthPillar = "all" | "defect" | "maintainability" | "performance";

export function TriageView({
  adapter,
  trend,
  overlay = "health",
  onOverlayChange,
  mapFiles,
  overlayLoading,
  pillar: controlledPillar,
  onPillarChange,
}: {
  adapter: CodeHealthAdapter;
  /** Trend fetched once by the host — feeds the KPI sparklines. */
  trend?: HealthTrendResponse;
  /** Active map lens, owned by the host so the spine is shared across tabs. */
  overlay?: CodeHealthOverlay;
  onOverlayChange?: (overlay: CodeHealthOverlay) => void;
  /** Map files fetched once by the host (shared across overlays). */
  mapFiles?: HealthFilesResponse;
  /** The active lens's per-file signal is still loading (e.g. churn). */
  overlayLoading?: boolean;
  /**
   * Findings pillar filter. Controlled by the host when it wants to URL-sync
   * the value (so the Overview + KPI tiles can deep-link into a dimension);
   * falls back to local state otherwise.
   */
  pillar?: HealthPillar;
  onPillarChange?: (pillar: HealthPillar) => void;
}) {
  const { cacheKey } = adapter;
  const { data: overview, isLoading, error, mutate } = useSWR<HealthOverviewResponse>(
    `code-health-overview:${cacheKey}`,
    () => adapter.getOverview(25),
    { revalidateOnFocus: false },
  );

  // Severity gates the inspector-rail findings list.
  const [minSeverity, setMinSeverity] = useState<Severity | "all">("all");
  const [selectedFile, setSelectedFile] = useState<string | null>(null);

  // ---- Map-driven UI: rail spotlight + filename dim + search ----
  const [hoverFile, setHoverFile] = useState<CodeHealthMapFile | null>(null);
  const [mapQuery, setMapQuery] = useState("");

  // ---- Pillar filter — controlled by the host (URL-synced) when supplied,
  //      otherwise local. Lets the Overview + KPI tiles deep-link a dimension. ----
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
  const focusPillar = useCallback(
    (next: "defect" | "maintainability" | "performance") => {
      setPillar(next);
      findingsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    },
    [setPillar],
  );

  // Severity + pillar drive the sidebar findings list; omit unset filters
  // rather than passing `undefined` (strict optional props in the shared lib).
  const sidebarFilter: {
    minSeverity?: Severity;
    dimension?: "defect" | "maintainability" | "performance";
  } = {};
  if (minSeverity !== "all") sidebarFilter.minSeverity = minSeverity;
  if (pillar !== "all") sidebarFilter.dimension = pillar;

  const avgSeries = trend?.history?.slice().reverse().map((p) => p.average_health);
  const hotspotSeries = trend?.history?.slice().reverse().map((p) => p.hotspot_health);
  const worstSeries = trend?.history
    ?.slice()
    .reverse()
    .map((p) => p.worst_performer_score ?? 0);

  // KPI trend inputs are all optional in the shared card; include only the
  // ones the host actually fetched (no `undefined` under strict optionals).
  const kpiTrend: {
    averageHistory?: number[];
    hotspotHistory?: number[];
    worstHistory?: number[];
    averageDelta?: number;
    hotspotDelta?: number;
  } = {};
  if (avgSeries) kpiTrend.averageHistory = avgSeries;
  if (hotspotSeries) kpiTrend.hotspotHistory = hotspotSeries;
  if (worstSeries) kpiTrend.worstHistory = worstSeries;
  if (trend?.summary?.average_delta != null)
    kpiTrend.averageDelta = trend.summary.average_delta;
  if (trend?.summary?.hotspot_delta != null)
    kpiTrend.hotspotDelta = trend.summary.hotspot_delta;

  // CodeHealthMap's lens controls are optional; omit when the host doesn't
  // own the lens (hosted before it wires the shared spine).
  const mapExtra: {
    onOverlayChange?: (overlay: CodeHealthOverlay) => void;
    overlayLoading?: boolean;
  } = {};
  if (onOverlayChange) mapExtra.onOverlayChange = onOverlayChange;
  if (overlayLoading !== undefined) mapExtra.overlayLoading = overlayLoading;

  return (
    <div className="space-y-6">
      {isLoading ? (
        <div className="space-y-3">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-28 w-full rounded-xl" />
            ))}
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-20 w-full rounded-lg" />
            ))}
          </div>
        </div>
      ) : error ? (
        <ApiError
          title="Couldn't load health data"
          message={`${toFriendlyMessage(error)} Index this repo if it has not been indexed yet.`}
          onRetry={() => void mutate()}
        />
      ) : overview ? (
        <>
          {/* Proof, up front: the defect-validated headline that sets this score
              apart — a slim one-line banner that expands to the full breakdown. */}
          {overview.defect_accuracy ? (
            <DefectAccuracyCard data={overview.defect_accuracy} collapsible />
          ) : null}

          <HealthKpiCards
            summary={overview.summary}
            distribution={overview.distribution ?? null}
            {...kpiTrend}
            onSelectPillar={focusPillar}
          />

          {/* Hero: the Code Health Map — modules as nested bubbles, files
              sized by lines of code and colored by health. The centerpiece. */}
          <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_320px] gap-6">
            <div className="space-y-2">
              <div className="flex flex-wrap items-baseline gap-2">
                <h2 className="text-base font-semibold text-[var(--color-text-primary)]">
                  Code health map
                </h2>
                <span className="text-xs text-[var(--color-text-tertiary)]">
                  {mapFiles ? `${mapFiles.total.toLocaleString()} files` : "loading…"} · click a module to
                  zoom, a file to open
                </span>
              </div>
              {!mapFiles ? (
                <Skeleton className="w-full rounded-xl" style={{ height: 720 }} />
              ) : (
                <CodeHealthMap
                  files={mapFiles.files}
                  search={mapQuery}
                  selectedPath={selectedFile}
                  onSelectFile={(p) => setSelectedFile(p)}
                  onHoverFile={setHoverFile}
                  minHeight={720}
                  overlay={overlay}
                  {...mapExtra}
                />
              )}
            </div>

            {/* Inspector rail — height-matched to the map (master–detail).
                Search + the file you're inspecting sit on top; the findings
                list scrolls in place so the rail never outgrows the map. */}
            <aside className="flex flex-col gap-3 lg:sticky lg:top-4 lg:h-[756px]">
              <div className="relative">
                <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[var(--color-text-tertiary)]" />
                <input
                  value={mapQuery}
                  onChange={(e) => setMapQuery(e.target.value)}
                  placeholder="Find a file in the map…"
                  className="w-full text-xs pl-7 pr-2 py-1.5 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] focus:outline-none focus:border-[var(--color-border-strong)]"
                />
              </div>

              <FileSpotlight file={hoverFile} onOpen={(p) => setSelectedFile(p)} />

              <div ref={findingsRef} className="flex items-center gap-2 scroll-mt-4">
                <h2 className="text-sm font-medium uppercase tracking-wider text-[var(--color-text-tertiary)] mr-auto">
                  Findings
                </h2>
                <select
                  value={pillar}
                  onChange={(e) => setPillar(e.target.value as HealthPillar)}
                  aria-label="Health pillar"
                  className="text-xs px-2 py-1 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)]"
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
                  className="text-xs px-2 py-1 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)]"
                >
                  <option value="all">All severities</option>
                  <option value="low">Low+</option>
                  <option value="medium">Medium+</option>
                  <option value="high">High+</option>
                  <option value="critical">Critical</option>
                </select>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto pr-1 lg:pb-0 pb-1">
                <BiomarkerList
                  findings={overview.top_findings}
                  compact
                  {...sidebarFilter}
                  onSelect={(f) => setSelectedFile(f.file_path)}
                />
              </div>
            </aside>
          </div>
        </>
      ) : null}

      {adapter.renderFileDrawer({
        filePath: selectedFile,
        onClose: () => setSelectedFile(null),
      })}
    </div>
  );
}
