"use client";

/**
 * Code Health — `/repos/[id]/code-health`.
 *
 * One living map + a thin drill-down. The galaxy map is the page spine; a lens
 * switcher recolors the same field (health / coverage / churn — the three lenses
 * backed by per-file map data) so the cross-tab redundancy collapses. Dead-code
 * and security have no per-file map signal and stay on their own tabs. Surviving
 * tabs — Triage
 * (the map spine), Hotspots, Coverage, Dead code, Impact, Security — are
 * URL-synced via `?tab=`; the active lens via `?lens=`. Modules folded into the
 * map's hub layer and Trend into a compact section.
 */

import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";
import useSWR from "swr";
import { HeartPulse, RotateCw } from "lucide-react";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { ViewTabs } from "@repowise-dev/ui/shared/view-tabs";
import { CollapsibleSection } from "@repowise-dev/ui/shared/collapsible-section";
import { Button } from "@repowise-dev/ui/ui/button";
import type { CodeHealthOverlay } from "@repowise-dev/ui/health";
import { TriageTab } from "@/components/code-health/triage-tab";
import { FindingsTab } from "@/components/code-health/findings-tab";
import { CoverageTab } from "@/components/code-health/coverage-tab";
import { TrendSection } from "@/components/code-health/trend-tab";
import { HotspotsTab } from "@/components/risk/hotspots-tab";
import { DeadCodeTab } from "@/components/risk/dead-code-tab";
import { ImpactTab } from "@/components/risk/impact-tab";
import { SecurityTab } from "@/components/risk/security-tab";
import {
  getHealthOverview,
  getHealthTrend,
  listHealthFiles,
  type HealthOverviewResponse,
  type HealthTrendResponse,
  type HealthFilesResponse,
} from "@/lib/api/code-health";

const TABS = ["triage", "findings", "hotspots", "coverage", "dead-code", "impact", "security"] as const;
type TabId = (typeof TABS)[number];

const TAB_LABELS: Record<TabId, string> = {
  triage: "Overview",
  findings: "Findings",
  hotspots: "Hotspots",
  coverage: "Coverage",
  "dead-code": "Dead code",
  impact: "Impact",
  security: "Security",
};

/**
 * Legacy tab ids → their new home. `heatmap` was the old churn tab; `modules`
 * folded into the map's hub layer; `trend` folded into the compact section that
 * lives under Triage.
 */
const TAB_ALIASES: Record<string, TabId> = {
  heatmap: "hotspots",
  modules: "triage",
  trend: "triage",
};

/**
 * Lenses selectable on the map: the three co-equal health signals (defect /
 * maintainability / performance), all backed by a per-file score in the map
 * payload. An unrecognized `?lens=` (including the retired `coverage`/`churn`/
 * `dead-code`/`security` values, which live on their own tabs) falls back to
 * `health` so a stale URL never renders an all-grey map.
 */
const OVERLAYS: CodeHealthOverlay[] = ["health", "maintainability", "performance"];

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

  // Shares the SWR key with TriageTab — the meta line costs no extra request.
  const { data: overview, mutate } = useSWR<HealthOverviewResponse>(
    `code-health-overview:${repoId}`,
    () => getHealthOverview(repoId, 25),
    { revalidateOnFocus: false },
  );
  const meta = overview?.meta;

  // Trend fetched ONCE here, fed to both the KPI sparklines (Triage) and the
  // folded Trend section — no second fetch, no second SWR key.
  const { data: trend, isLoading: trendLoading, error: trendError } =
    useSWR<HealthTrendResponse>(
      `code-health-trend:${repoId}`,
      () => getHealthTrend(repoId, 20),
      { revalidateOnFocus: false },
    );

  // Every file (NLOC-first) for the circle-packing map — one big pull, shared
  // across overlays so switching the lens never refetches.
  const { data: mapFiles } = useSWR<HealthFilesResponse>(
    `code-health-map-files:${repoId}`,
    () => listHealthFiles(repoId, { limit: 2000, sort: "nloc", order: "desc" }),
    { revalidateOnFocus: false },
  );

  // The maintainability + performance lenses recolor by per-file scores that
  // already ride along on the map payload (the `/files` rows carry
  // `maintainability_score` / `performance_score`), so no second fetch or merge
  // is needed — the lens switch is a pure recolor.

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
      title="Code Health"
      icon={<HeartPulse className="h-5 w-5 text-[var(--color-success)]" />}
      description="Per-file health scores from complexity, duplication, coverage, churn, and ownership markers — ranked into a fix-next queue."
      maxWidth="wide"
      actions={
        <Button size="sm" variant="outline" onClick={() => mutate()}>
          <RotateCw className="h-3.5 w-3.5 mr-1.5" /> Refresh
        </Button>
      }
    >
      {meta ? (
        <p className="-mt-3 text-xs text-[var(--color-text-tertiary)]">
          {meta.last_indexed_at
            ? `Indexed ${new Date(meta.last_indexed_at).toLocaleString()}`
            : "Not indexed yet"}
          {meta.head_commit ? ` · ${meta.head_commit.slice(0, 8)}` : ""}
          {` · ${meta.snapshot_count} snapshot${meta.snapshot_count === 1 ? "" : "s"}`}
        </p>
      ) : null}

      <ViewTabs
        tabs={TABS.map((id) => ({ id, label: TAB_LABELS[id] }))}
        value={activeTab}
        onValueChange={setTab}
      >
        {activeTab === "triage" && (
          <div className="space-y-6">
            <TriageTab
              repoId={repoId}
              trend={trend}
              overlay={overlay}
              onOverlayChange={setOverlay}
              mapFiles={mapFiles}
            />
            <CollapsibleSection title="Health trend" defaultOpen={false}>
              <TrendSection data={trend} isLoading={trendLoading} error={trendError} />
            </CollapsibleSection>
          </div>
        )}
        {activeTab === "findings" && <FindingsTab repoId={repoId} />}
        {activeTab === "hotspots" && <HotspotsTab repoId={repoId} />}
        {activeTab === "coverage" && <CoverageTab repoId={repoId} />}
        {activeTab === "dead-code" && <DeadCodeTab repoId={repoId} />}
        {activeTab === "impact" && <ImpactTab repoId={repoId} />}
        {activeTab === "security" && <SecurityTab repoId={repoId} />}
      </ViewTabs>
    </PageShell>
  );
}
