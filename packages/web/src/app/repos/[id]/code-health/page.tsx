"use client";

/**
 * Code Health — `/repos/[id]/code-health`.
 *
 * One living map, a lede that says what the score means, and a thin drill-down.
 * The galaxy map is the page spine; a lens switcher recolors the same field
 * (health / maintainability / performance / churn) so the cross-tab redundancy
 * collapses.
 *
 * Performance has its own causal-opportunity tab; raw observations remain its
 * evidence rather than competing with the ranked finding queue. Hotspots is
 * gone: it rendered a *second* galaxy map
 * on the churn lens directly under this page's, so churn became a lens here and
 * what the lens cannot say — bus factor, the ranked table — became a section
 * under the map. Blast radius keeps its `impact` tab id (existing file-card and
 * symbol-drawer deep links point at it) but is labelled for what it is.
 *
 * Tabs carry their count where one is cheap to get, so a clean repo says so
 * before you spend a click. Findings rides on the overview request the page
 * already makes and dead code dedupes onto the key its own tab uses. Security
 * has no count endpoint (only a findings list), and blast radius is a tool you
 * operate rather than a pile you read, so neither is numbered.
 */

import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useMemo, useState } from "react";
import useSWR, { useSWRConfig } from "swr";
import { HeartPulse, RotateCw } from "lucide-react";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { ViewTabs } from "@repowise-dev/ui/shared/view-tabs";
import { OverviewSection } from "@repowise-dev/ui/overview";
import { Button } from "@repowise-dev/ui/ui/button";
import { formatDateTime } from "@repowise-dev/ui/lib/format";
import type { CodeHealthOverlay } from "@repowise-dev/ui/health";
import type { DeadCodeSummary } from "@repowise-dev/types/dead-code";
import { TriageTab } from "@/components/code-health/triage-tab";
import { HotspotsSection } from "@/components/code-health/hotspots-section";
import { FindingsTab } from "@/components/code-health/findings-tab";
import { PerformanceTab } from "@/components/code-health/performance-tab";
import { CoverageTab } from "@/components/code-health/coverage-tab";
import { TrendSection } from "@/components/code-health/trend-tab";
import { DeadCodeTab } from "@/components/risk/dead-code-tab";
import { ImpactTab } from "@/components/risk/impact-tab";
import { SecurityTab } from "@/components/risk/security-tab";
import { getDeadCodeSummary } from "@/lib/api/dead-code";
import {
  getChurnComplexity,
  getHealthCoverage,
  getHealthOverview,
  getHealthTrend,
  listHealthFiles,
  type ChurnComplexityResponse,
  type HealthCoverageResponse,
  type HealthOverviewResponse,
  type HealthTrendResponse,
  type HealthFilesResponse,
} from "@/lib/api/code-health";

const TABS = [
  "triage",
  "performance",
  "findings",
  "coverage",
  "dead-code",
  "security",
  "impact",
] as const;
type TabId = (typeof TABS)[number];

const TAB_LABELS: Record<TabId, string> = {
  triage: "Overview",
  performance: "Performance",
  findings: "Findings",
  coverage: "Tests",
  "dead-code": "Dead code",
  security: "Security",
  impact: "Blast radius",
};

/**
 * Legacy tab ids → their new home. `heatmap` was the old churn tab and
 * `hotspots` its successor; both now land on the map, whose churn lens replaced
 * them. `modules` folded into the map's hub layer, `trend` into the section
 * under it.
 */
const TAB_ALIASES: Record<string, TabId> = {
  heatmap: "triage",
  hotspots: "triage",
  modules: "triage",
  trend: "triage",
};

/**
 * Lenses on the map. The three co-equal health signals ride on the map payload
 * itself; churn arrives on its own request and is joined in below, which is why
 * it is listed here rather than in the map component's default.
 */
const OVERLAYS: CodeHealthOverlay[] = ["health", "maintainability", "performance", "churn"];

/** Files pulled for the map: biggest first, capped so the galaxy stays legible. */
const MAP_FILE_LIMIT = 2000;
/**
 * Churn points pulled for the churn lens. Deliberately larger than
 * `MAP_FILE_LIMIT`: the map ranks by NLOC and churn-complexity ranks by
 * `commit_count × max_ccn`, so the two windows do not contain the same files.
 * At the old default of 300 this repo joined churn onto 296 of the 1,935 mapped
 * files that have it, and the other ~1,700 nodes rendered as the legend's
 * "no data" swatch for data that exists. Matching the map's 2,000 would still
 * miss 445. 5,000 covers every churned file here with headroom; a repo that
 * exceeds it degrades back to the same partial join rather than breaking.
 *
 * Costs ~469 KB uncompressed on this repo (3,011 points), on a request that
 * only fires once the churn lens is selected. The hosted route still caps at
 * 1,000, so porting this page there needs that ceiling raised first or the
 * request 422s.
 */
const CHURN_POINT_LIMIT = 5000;

export default function CodeHealthPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const repoId = params.id;

  const rawTab = searchParams.get("tab");
  const aliased = rawTab ? TAB_ALIASES[rawTab] : undefined;
  const activeTab: TabId =
    aliased ??
    (rawTab && (TABS as readonly string[]).includes(rawTab) ? (rawTab as TabId) : "triage");

  const rawLens = searchParams.get("lens");
  const overlay: CodeHealthOverlay = (OVERLAYS as readonly string[]).includes(rawLens ?? "")
    ? (rawLens as CodeHealthOverlay)
    : "health";

  // Shares the SWR key with TriageView — the meta line and the findings count
  // cost no extra request.
  const { data: overview } = useSWR<HealthOverviewResponse>(
    `code-health-overview:${repoId}`,
    () => getHealthOverview(repoId, 25),
    { revalidateOnFocus: false },
  );
  const meta = overview?.meta;

  // Refresh revalidates every SWR key for this repo (overview + each tab's own
  // keys), so the button works on whichever tab is active.
  const { mutate: mutateAll } = useSWRConfig();
  const [refreshing, setRefreshing] = useState(false);
  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await mutateAll((key) => typeof key === "string" && key.includes(repoId), undefined, {
        revalidate: true,
      });
    } finally {
      setRefreshing(false);
    }
  }, [mutateAll, repoId]);

  // Trend fetched ONCE here, fed to the folded Trend section — no second fetch.
  const {
    data: trend,
    isLoading: trendLoading,
    error: trendError,
  } = useSWR<HealthTrendResponse>(`code-health-trend:${repoId}`, () => getHealthTrend(repoId, 20), {
    revalidateOnFocus: false,
  });

  // Every file (NLOC-first) for the map — one big pull, shared across overlays
  // so switching the lens never refetches. `fields: "summary"` because the map
  // and the spotlight read none of the lead/detail keys: at 2,000 rows the full
  // shape was 1.06 MB, about 432 KB of it keys nobody here touches.
  const { data: mapFiles } = useSWR<HealthFilesResponse>(
    `code-health-map-files:${repoId}`,
    () =>
      listHealthFiles(repoId, {
        limit: MAP_FILE_LIMIT,
        sort: "nloc",
        order: "desc",
        fields: "summary",
      }),
    { revalidateOnFocus: false },
  );

  // Churn percentiles for the churn lens. Fetched only once that lens is
  // selected: it is a second request, and the other three lenses color from
  // fields already on the map payload.
  const churnWanted = overlay === "churn";
  const { data: churn, isLoading: churnLoading } = useSWR<ChurnComplexityResponse>(
    churnWanted ? `health-churn-complexity:${repoId}` : null,
    () => getChurnComplexity(repoId, { limit: CHURN_POINT_LIMIT }),
    { revalidateOnFocus: false, keepPreviousData: true },
  );

  // Join churn onto the map rows by path. Until it lands every node would be
  // neutral, so the legend is told it is loading rather than letting an
  // all-grey field read as "no churn anywhere".
  const mapFilesWithChurn: HealthFilesResponse | undefined = useMemo(() => {
    if (!mapFiles || !churn) return mapFiles;
    const byPath = new Map(churn.points.map((p) => [p.file_path, p.churn_percentile]));
    return {
      ...mapFiles,
      files: mapFiles.files.map((file) => ({
        ...file,
        churn_percentile: byPath.get(file.file_path) ?? null,
      })),
    };
  }, [mapFiles, churn]);

  // ---- Tab counts ----
  // Dead code uses the same key + fetcher as its own tab, so this is a prefetch
  // rather than a duplicate request.
  const { data: deadCode } = useSWR<DeadCodeSummary>(
    `dead-code-summary:${repoId}`,
    () => getDeadCodeSummary(repoId),
    { revalidateOnFocus: false },
  );
  // Coverage's own tab pulls 5,000 file rows and the module rollup; this badge
  // reads one number off `summary`, so it declines both. `limit` and
  // `module_limit` cap different blocks — one file row and no modules is 0.7 KB
  // against the tab's 442 KB, and `modules_total` still reports the real count
  // to anyone who asks for it.
  const { data: coverage } = useSWR<HealthCoverageResponse>(
    `code-health-coverage-summary:${repoId}`,
    () =>
      getHealthCoverage(repoId, {
        limit: 1,
        module_limit: 0,
        // And declines the graph fallback, which would read every call edge in
        // the repo to answer a question this badge cannot render anyway: the
        // inferred basis has no percentage to show.
        include_inferred: false,
      }),
    { revalidateOnFocus: false },
  );
  const coveragePct = coverage?.summary.line_coverage_pct;

  const badges: Partial<Record<TabId, number | string>> = {};
  if (overview) badges.findings = overview.summary.open_findings;
  if (deadCode) badges["dead-code"] = deadCode.total_findings;
  if (coveragePct != null) badges.coverage = `${Math.round(coveragePct)}%`;

  const setTab = useCallback(
    (next: string) => {
      const sp = new URLSearchParams(searchParams.toString());
      if (next === "triage") sp.delete("tab");
      else sp.set("tab", next);
      const qs = sp.toString();
      router.replace(qs ? `?${qs}` : "?", { scroll: false });
    },
    [router, searchParams],
  );

  const setOverlay = useCallback(
    (next: CodeHealthOverlay) => {
      const sp = new URLSearchParams(searchParams.toString());
      if (next === "health") sp.delete("lens");
      else sp.set("lens", next);
      const qs = sp.toString();
      router.replace(qs ? `?${qs}` : "?", { scroll: false });
    },
    [router, searchParams],
  );

  return (
    <PageShell
      title="Code health"
      icon={<HeartPulse className="h-5 w-5 text-[var(--color-success)]" />}
      // No description: the lede below opens with what the score is built from,
      // and a header that says it first only says it twice.
      //
      // "wide" rather than the style's usual 1280 — a deliberate divergence. At
      // 1280 the map keeps ~900px beside its 320px inspector; the extra width
      // goes entirely to the field, which is this page's whole subject.
      maxWidth="wide"
      actions={
        <Button size="sm" variant="outline" onClick={refresh} disabled={refreshing}>
          <RotateCw className={`mr-1.5 h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />{" "}
          {refreshing ? "Refreshing…" : "Refresh"}
        </Button>
      }
    >
      {meta ? (
        <p className="-mt-3 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
          {meta.last_indexed_at
            ? `Indexed ${formatDateTime(meta.last_indexed_at)}`
            : "Not indexed yet"}
          {meta.head_commit ? ` · ${meta.head_commit.slice(0, 8)}` : ""}
          {` · ${meta.snapshot_count} snapshot${meta.snapshot_count === 1 ? "" : "s"}`}
        </p>
      ) : null}

      <ViewTabs
        tabs={TABS.map((id) => ({
          id,
          label: TAB_LABELS[id],
          ...(badges[id] !== undefined ? { badge: badges[id] } : {}),
        }))}
        value={activeTab}
        onValueChange={setTab}
      >
        {activeTab === "triage" && (
          <TriageTab
            repoId={repoId}
            trend={trend}
            overlay={overlay}
            onOverlayChange={setOverlay}
            lenses={OVERLAYS}
            mapFiles={mapFilesWithChurn}
            overlayLoading={churnWanted && churnLoading && !churn}
            hotspotsSlot={<HotspotsSection repoId={repoId} />}
            trendSlot={
              <OverviewSection
                title="Health trend"
                description="How the scores have moved across indexed snapshots."
              >
                <TrendSection data={trend} isLoading={trendLoading} error={trendError} />
              </OverviewSection>
            }
          />
        )}
        {activeTab === "findings" && <FindingsTab repoId={repoId} />}
        {activeTab === "performance" && <PerformanceTab repoId={repoId} />}
        {activeTab === "coverage" && <CoverageTab repoId={repoId} />}
        {activeTab === "dead-code" && <DeadCodeTab repoId={repoId} />}
        {activeTab === "security" && <SecurityTab repoId={repoId} />}
        {activeTab === "impact" && <ImpactTab repoId={repoId} />}
      </ViewTabs>
    </PageShell>
  );
}
