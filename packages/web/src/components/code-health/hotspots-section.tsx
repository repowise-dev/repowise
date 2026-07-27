"use client";

/**
 * Hotspots, as a section under the Code Health map rather than a tab of its own.
 *
 * The tab it replaces rendered a *second* galaxy map — same component, churn
 * lens, 420px — directly beneath the page's 720px one, plus five bordered chart
 * cards at identical weight. Churn is now a lens on the single map, which is
 * where it always belonged, so what survives here is only what the lens cannot
 * say: which hot files have a single owner, and the ranked table itself.
 *
 * Four charts did not survive. The commit-category donut spent four unrelated
 * hues on four categories, which is the exact pattern the colour rule exists to
 * stop. The churn histogram, the risk-distribution chart and the churn-vs-bus-
 * factor scatter all restate columns the table below them already shows, in a
 * form you cannot sort.
 *
 * The scatter was the close call, and it lost on its own data. Its whole point
 * is a top-right danger zone — changes constantly, only one person understands
 * it — but on a real repo almost every hotspot sits at bus factor 1 and the
 * 100th churn percentile, so the danger zone is the entire plot and the shape
 * carries nothing. The table says it better: a `BUS FACTOR` column you can sort
 * and a "Bus factor risk" filter chip that names the set outright.
 */

import { useState } from "react";
import useSWR from "swr";
import { OverviewSection, SectionLink } from "@repowise-dev/ui/overview";
import { HotspotTable } from "@repowise-dev/ui/git/hotspot-table";
import { AiPromptModal, buildHotspotAiPrompt } from "@repowise-dev/ui/health";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { hotspotToFileCard } from "@repowise-dev/ui/shared/file-card";
import type { Hotspot } from "@repowise-dev/types/git";
import Link from "next/link";
import { useFileCardHost } from "@/components/shared/file-card-host";
import { HotspotTopSymbolsHost } from "@/components/symbols/hotspot-top-symbols-host";
import { SymbolDrawerWrapper } from "@/components/symbols/symbol-drawer-wrapper";
import { getHotspotsPage } from "@/lib/api/git";
import type { Paginated, SymbolResponse, HotspotResponse } from "@/lib/api/types";

const PAGE_SIZE = 100;
const PAGE_MAX = 500;

export function HotspotsSection({ repoId }: { repoId: string }) {
  const { showFile, dialog } = useFileCardHost(repoId);
  const [pageLimit, setPageLimit] = useState(PAGE_SIZE);
  const [drawerSymbol, setDrawerSymbol] = useState<SymbolResponse | null>(null);
  const [promptHotspot, setPromptHotspot] = useState<Hotspot | null>(null);

  const {
    data: page,
    isLoading,
    isValidating,
    error,
  } = useSWR<Paginated<HotspotResponse>>(
    `risk-hotspots:${repoId}:${pageLimit}`,
    () => getHotspotsPage(repoId, { limit: pageLimit }),
    { revalidateOnFocus: false, keepPreviousData: true },
  );

  const list = page?.items ?? [];
  const total = page?.total ?? list.length;

  // The section renders nothing at all when a repo has no git history to mine,
  // rather than an empty state explaining an absence the reader cannot act on.
  if (!isLoading && !error && list.length === 0) return null;

  return (
    <OverviewSection
      title="Hotspots"
      description="Ranked by change frequency and prior bug fixes, mined from full git history. The danger is not churn on its own. It is a file that changes constantly and only one person understands."
      action={
        <SectionLink href={`/repos/${repoId}/commits`} LinkComponent={Link}>
          Commit history
        </SectionLink>
      }
    >
      {error && list.length === 0 ? (
        <p className="text-sm text-[var(--color-text-secondary)]">
          Couldn&apos;t load hotspots. Run a sync to mine git history for this repo.
        </p>
      ) : isLoading && list.length === 0 ? (
        <Skeleton className="h-72 w-full rounded-xl" />
      ) : (
        <HotspotTable
          hotspots={list}
          repoId={repoId}
          onSelect={(h) => showFile(hotspotToFileCard(h))}
          onGeneratePrompt={setPromptHotspot}
          total={total}
          hasMore={page?.has_more ?? false}
          loadingMore={isValidating && !isLoading}
          onLoadMore={() => setPageLimit((n) => Math.min(n + PAGE_SIZE, PAGE_MAX))}
          renderExpandedRow={(h) => (
            <HotspotTopSymbolsHost
              repoId={repoId}
              filePath={h.file_path}
              onSelectSymbol={setDrawerSymbol}
            />
          )}
        />
      )}

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
    </OverviewSection>
  );
}
