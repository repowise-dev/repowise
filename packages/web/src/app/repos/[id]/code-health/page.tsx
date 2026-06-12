"use client";

/**
 * Code Health — `/repos/[id]/code-health`.
 *
 * One "what do I fix next" section: Triage (the ranked fix-next queue),
 * Hotspots & Churn (the only place called "hotspots"), Modules, Coverage,
 * Dead code, Impact, Security, and Trend — one chrome, one tab row,
 * URL-synced via `?tab=`.
 */

import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";
import useSWR from "swr";
import { HeartPulse, RotateCw } from "lucide-react";
import {
  Tabs,
  TabsContent,
  ScrollableTabsList,
  TabsTrigger,
} from "@repowise-dev/ui/ui/tabs";
import { Button } from "@repowise-dev/ui/ui/button";
import { TriageTab } from "@/components/code-health/triage-tab";
import { CoverageTab } from "@/components/code-health/coverage-tab";
import { TrendTab } from "@/components/code-health/trend-tab";
import { HotspotsTab } from "@/components/risk/hotspots-tab";
import { DeadCodeTab } from "@/components/risk/dead-code-tab";
import { ImpactTab } from "@/components/risk/impact-tab";
import { ModulesTab } from "@/components/risk/modules-tab";
import { SecurityTab } from "@/components/risk/security-tab";
import { RiskSummaryStrip } from "@/components/risk/risk-summary-strip";
import {
  getHealthOverview,
  type HealthOverviewResponse,
} from "@/lib/api/code-health";

const TABS = [
  "triage",
  "hotspots",
  "modules",
  "coverage",
  "dead-code",
  "impact",
  "security",
  "trend",
] as const;
type TabId = (typeof TABS)[number];

/** Legacy tab ids from the old /risk page → their new home. */
const TAB_ALIASES: Record<string, TabId> = {
  heatmap: "hotspots",
};

/** Tabs that came from the Risk page and share the summary strip. */
const STRIP_TABS = new Set<TabId>(["hotspots", "modules", "dead-code", "impact", "security"]);

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

  // Shares the SWR key with TriageTab — the meta line costs no extra request.
  const { data: overview, mutate } = useSWR<HealthOverviewResponse>(
    `code-health-overview:${repoId}`,
    () => getHealthOverview(repoId, 25),
    { revalidateOnFocus: false },
  );
  const meta = overview?.meta;

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

  return (
    <div className="p-4 sm:p-6 space-y-4 max-w-[1600px]">
      <header className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1 space-y-1">
          <h1 className="flex items-center gap-2 text-lg font-semibold text-[var(--color-text-primary)]">
            <HeartPulse className="h-5 w-5 text-[var(--color-success)]" />
            Code Health
          </h1>
          <p className="text-sm text-[var(--color-text-secondary)]">
            Per-file health scores from complexity, duplication, coverage, churn,
            and ownership biomarkers — ranked into a fix-next queue.
          </p>
          {meta ? (
            <p className="text-xs text-[var(--color-text-tertiary)]">
              {meta.last_indexed_at
                ? `Indexed ${new Date(meta.last_indexed_at).toLocaleString()}`
                : "Not indexed yet"}
              {meta.head_commit ? ` · ${meta.head_commit.slice(0, 8)}` : ""}
              {` · ${meta.snapshot_count} snapshot${meta.snapshot_count === 1 ? "" : "s"}`}
            </p>
          ) : null}
        </div>
        <Button size="sm" variant="outline" onClick={() => mutate()}>
          <RotateCw className="h-3.5 w-3.5 mr-1.5" /> Refresh
        </Button>
      </header>

      <Tabs value={activeTab} onValueChange={setTab} className="space-y-4">
        <ScrollableTabsList>
          <TabsTrigger value="triage">Triage</TabsTrigger>
          <TabsTrigger value="hotspots">Hotspots &amp; churn</TabsTrigger>
          <TabsTrigger value="modules">Modules</TabsTrigger>
          <TabsTrigger value="coverage">Coverage</TabsTrigger>
          <TabsTrigger value="dead-code">Dead code</TabsTrigger>
          <TabsTrigger value="impact">Impact</TabsTrigger>
          <TabsTrigger value="security">Security</TabsTrigger>
          <TabsTrigger value="trend">Trend</TabsTrigger>
        </ScrollableTabsList>

        {STRIP_TABS.has(activeTab) && <RiskSummaryStrip repoId={repoId} />}

        <TabsContent value="triage" className="space-y-6">
          <TriageTab repoId={repoId} />
        </TabsContent>
        <TabsContent value="hotspots" className="space-y-6">
          <HotspotsTab repoId={repoId} />
        </TabsContent>
        <TabsContent value="modules" className="space-y-6">
          <ModulesTab repoId={repoId} />
        </TabsContent>
        <TabsContent value="coverage" className="space-y-6">
          <CoverageTab repoId={repoId} />
        </TabsContent>
        <TabsContent value="dead-code" className="space-y-6">
          <DeadCodeTab repoId={repoId} />
        </TabsContent>
        <TabsContent value="impact" className="space-y-6">
          <ImpactTab repoId={repoId} />
        </TabsContent>
        <TabsContent value="security" className="space-y-6">
          <SecurityTab repoId={repoId} />
        </TabsContent>
        <TabsContent value="trend" className="space-y-6">
          <TrendTab repoId={repoId} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
