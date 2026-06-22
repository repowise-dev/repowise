"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import { useFileCardHost } from "@/components/shared/file-card-host";
import { HotspotTopSymbolsHost } from "@/components/symbols/hotspot-top-symbols-host";
import { SymbolDrawerWrapper } from "@/components/symbols/symbol-drawer-wrapper";
import type { Paginated, SymbolResponse } from "@/lib/api/types";
import { hotspotToFileCard } from "@repowise-dev/ui/shared/file-card";
import { HotspotTable } from "@repowise-dev/ui/git/hotspot-table";
import { HotspotTrendStrip } from "@repowise-dev/ui/git/hotspot-trend-strip";
import { ChurnHistogram } from "@repowise-dev/ui/git/churn-histogram";
import { CommitCategoryDonut } from "@repowise-dev/ui/git/commit-category-donut";
import { RiskDistributionChart } from "@repowise-dev/ui/git/risk-distribution-chart";
import { ChurnVsBusFactorScatter } from "@repowise-dev/ui/git/churn-vs-bus-factor-scatter";
import {
  CodeHealthMap,
  type CodeHealthOverlay,
} from "@repowise-dev/ui/health/code-health-map";
import { AiPromptModal, buildHotspotAiPrompt } from "@repowise-dev/ui/health";
import type { Hotspot } from "@repowise-dev/types/git";
import { Card, CardContent, CardHeader, CardTitle } from "@repowise-dev/ui/ui/card";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { getHotspotsPage } from "@/lib/api/git";
import { getChurnComplexity, listHealthFiles } from "@/lib/api/code-health";
import type { ChurnComplexityResponse, HealthFilesResponse } from "@/lib/api/code-health";
import type { HotspotResponse } from "@/lib/api/types";

const PAGE_SIZE = 100;

export function HotspotsTab({ repoId }: { repoId: string }) {
  const { showFile, dialog } = useFileCardHost(repoId);
  const [pageLimit, setPageLimit] = useState(PAGE_SIZE);
  const [drawerSymbol, setDrawerSymbol] = useState<SymbolResponse | null>(null);
  const [promptHotspot, setPromptHotspot] = useState<Hotspot | null>(null);
  // The galaxy map opens on the churn lens here — this is the hotspots surface,
  // so "what changes most" is the first read; the switcher still offers
  // health/coverage for cross-reference.
  const [overlay, setOverlay] = useState<CodeHealthOverlay>("churn");
  const { data: churnComplexity, isLoading: loadingChurn } =
    useSWR<ChurnComplexityResponse>(
      `health-churn-complexity:${repoId}`,
      () => getChurnComplexity(repoId),
      { revalidateOnFocus: false, keepPreviousData: true },
    );

  // Same payload + SWR key the triage page uses for its galaxy map, so the two
  // surfaces dedupe onto one request instead of double-fetching.
  const { data: mapFiles } = useSWR<HealthFilesResponse>(
    `code-health-map-files:${repoId}`,
    () => listHealthFiles(repoId, { limit: 2000, sort: "nloc", order: "desc" }),
    { revalidateOnFocus: false },
  );

  // The churn lens recolors by `churn_percentile`, which rides on the separate
  // churn request. Until it lands every node is neutral — flag that so the map
  // reads as "loading" rather than "no data".
  const overlayLoading = overlay === "churn" && loadingChurn && !churnComplexity;

  // Join churn percentiles onto the map files (by path) so the churn lens colors
  // real data; recomputes only when either source changes.
  const mapFilesWithChurn: HealthFilesResponse | undefined = useMemo(() => {
    if (!mapFiles) return undefined;
    if (!churnComplexity) return mapFiles;
    const byPath = new Map(
      churnComplexity.points.map((p) => [p.file_path, p.churn_percentile]),
    );
    return {
      ...mapFiles,
      files: mapFiles.files.map((file) => ({
        ...file,
        churn_percentile: byPath.get(file.file_path) ?? null,
      })),
    };
  }, [mapFiles, churnComplexity]);
  const {
    data: hotspotsPage,
    isLoading: loadingHotspots,
    isValidating: validatingHotspots,
    error: hotspotsError,
  } = useSWR<Paginated<HotspotResponse>>(
    `risk-hotspots:${repoId}:${pageLimit}`,
    () => getHotspotsPage(repoId, { limit: pageLimit }),
    { revalidateOnFocus: false, keepPreviousData: true },
  );
  const list = hotspotsPage?.items ?? [];
  const total = hotspotsPage?.total ?? list.length;
  const hasMore = hotspotsPage?.has_more ?? false;
  const aggregatedCategories: Record<string, number> = {};
  for (const h of list) {
    for (const [cat, count] of Object.entries(h.commit_categories || {})) {
      aggregatedCategories[cat] = (aggregatedCategories[cat] || 0) + (count as number);
    }
  }

  if (loadingHotspots && list.length === 0) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-72 w-full" />
      </div>
    );
  }

  if (hotspotsError && list.length === 0) {
    return (
      <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-4 text-sm text-[var(--color-text-secondary)]">
        Couldn&apos;t load hotspots. The data may not be ready yet — try running a sync first.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {list.length > 0 && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Churn Distribution</CardTitle>
            </CardHeader>
            <CardContent className="pt-0">
              <ChurnHistogram hotspots={list} />
            </CardContent>
          </Card>

          {Object.keys(aggregatedCategories).length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">Commit Types</CardTitle>
              </CardHeader>
              <CardContent className="pt-0 flex items-center justify-center">
                <CommitCategoryDonut categories={aggregatedCategories} />
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {list.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Code health map</CardTitle>
            <p className="text-xs text-[var(--color-text-tertiary)]">
              Files clustered into module galaxies — dot size is NLOC, color is the
              chosen lens. On churn, the brightest nodes are your hottest files;
              switch lenses to cross-reference health or coverage. Click a file to
              inspect it.
            </p>
          </CardHeader>
          <CardContent className="pt-0">
            {!mapFilesWithChurn ? (
              <Skeleton className="h-[420px] w-full" />
            ) : (
              <CodeHealthMap
                files={mapFilesWithChurn.files}
                overlay={overlay}
                onOverlayChange={setOverlay}
                overlayLoading={overlayLoading}
                minHeight={420}
                onSelectFile={(path) => {
                  const hit = list.find((h) => h.file_path === path);
                  showFile(hit ? hotspotToFileCard(hit) : { file_path: path });
                }}
              />
            )}
          </CardContent>
        </Card>
      )}

      {list.length > 0 && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Risk Distribution</CardTitle>
              <p className="text-xs text-[var(--color-text-tertiary)]">
                Composite risk score: churn (40%) + bus factor (35%) + trend (25%)
              </p>
            </CardHeader>
            <CardContent className="pt-0">
              <RiskDistributionChart hotspots={list} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Churn × bus factor</CardTitle>
              <p className="text-xs text-[var(--color-text-tertiary)]">
                Top-right is the danger zone: high churn and a single owner. Bubble
                size reflects 90-day commit count.
              </p>
            </CardHeader>
            <CardContent className="pt-0">
              <ChurnVsBusFactorScatter
                hotspots={list}
                onSelect={(path) => {
                  const hit = list.find((h) => h.file_path === path);
                  if (hit) showFile(hotspotToFileCard(hit));
                }}
              />
            </CardContent>
          </Card>
        </div>
      )}

      {list.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Hotspot trend</CardTitle>
            <p className="text-xs text-[var(--color-text-tertiary)]">
              Top files by churn. Heating arrows mark files where the last 30 days are
              outpacing the 90-day baseline.
            </p>
          </CardHeader>
          <CardContent className="pt-0">
            <HotspotTrendStrip hotspots={list} />
          </CardContent>
        </Card>
      )}

      <HotspotTable
        hotspots={list}
        repoId={repoId}
        onSelect={(h) => showFile(hotspotToFileCard(h))}
        onGeneratePrompt={setPromptHotspot}
        total={total}
        hasMore={hasMore}
        loadingMore={validatingHotspots && !loadingHotspots}
        onLoadMore={() => setPageLimit((n) => Math.min(n + PAGE_SIZE, 500))}
        renderExpandedRow={(h) => (
          <HotspotTopSymbolsHost
            repoId={repoId}
            filePath={h.file_path}
            onSelectSymbol={setDrawerSymbol}
          />
        )}
      />
      {dialog}
      <SymbolDrawerWrapper
        symbol={drawerSymbol}
        repoId={repoId}
        onClose={() => setDrawerSymbol(null)}
      />
      <AiPromptModal
        open={promptHotspot !== null}
        onOpenChange={(o) => !o && setPromptHotspot(null)}
        getPrompt={
          promptHotspot
            ? (flavor) =>
                buildHotspotAiPrompt({
                  hotspot: {
                    file_path: promptHotspot.file_path,
                    churn_percentile: promptHotspot.churn_percentile,
                    commit_count_90d: promptHotspot.commit_count_90d,
                    commit_count_30d: promptHotspot.commit_count_30d,
                    bus_factor: promptHotspot.bus_factor,
                    contributor_count: promptHotspot.contributor_count,
                    primary_owner: promptHotspot.primary_owner,
                    lines_added_90d: promptHotspot.lines_added_90d,
                    lines_deleted_90d: promptHotspot.lines_deleted_90d,
                    temporal_hotspot_score: promptHotspot.temporal_hotspot_score,
                    change_entropy_pct: promptHotspot.change_entropy_pct,
                    prior_defect_count: promptHotspot.prior_defect_count,
                  },
                  flavor,
                })
            : null
        }
        filePath={promptHotspot?.file_path}
        title="AI stabilization prompt"
        description="A ready-to-paste prompt that has your AI agent diagnose why this file churns and propose changes that make it cheaper to maintain."
      />
    </div>
  );
}
