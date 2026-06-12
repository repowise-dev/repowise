"use client";

/**
 * Code Health — `/repos/[id]/code-health`.
 *
 * One "what do I fix next" section. Phase 1 of the UX overhaul mounts the
 * former Health overview as the Triage tab and the former Risk tabs
 * alongside it; Phase 3 consolidates the duplicated views and vocabulary.
 */

import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@repowise-dev/ui/ui/tabs";
import { TriageTab } from "@/components/code-health/triage-tab";
import { HeatmapTab } from "@/components/risk/heatmap-tab";
import { HotspotsTab } from "@/components/risk/hotspots-tab";
import { DeadCodeTab } from "@/components/risk/dead-code-tab";
import { ImpactTab } from "@/components/risk/impact-tab";
import { ModulesTab } from "@/components/risk/modules-tab";
import { SecurityTab } from "@/components/risk/security-tab";
import { RiskSummaryStrip } from "@/components/risk/risk-summary-strip";

const TABS = [
  "triage",
  "heatmap",
  "hotspots",
  "modules",
  "dead-code",
  "impact",
  "security",
] as const;
type TabId = (typeof TABS)[number];

export default function CodeHealthPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const repoId = params.id;

  const rawTab = searchParams.get("tab") as TabId | null;
  const activeTab: TabId = rawTab && TABS.includes(rawTab) ? rawTab : "triage";

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
      <Tabs value={activeTab} onValueChange={setTab} className="space-y-4">
        <TabsList className="h-auto flex-wrap">
          <TabsTrigger value="triage">Triage</TabsTrigger>
          <TabsTrigger value="heatmap">Heatmap</TabsTrigger>
          <TabsTrigger value="hotspots">Hotspots</TabsTrigger>
          <TabsTrigger value="modules">Modules</TabsTrigger>
          <TabsTrigger value="dead-code">Dead code</TabsTrigger>
          <TabsTrigger value="impact">Impact analyzer</TabsTrigger>
          <TabsTrigger value="security">Security</TabsTrigger>
        </TabsList>

        {activeTab !== "triage" && <RiskSummaryStrip repoId={repoId} />}

        <TabsContent value="triage" className="space-y-6">
          <TriageTab repoId={repoId} />
        </TabsContent>
        <TabsContent value="heatmap" className="space-y-6">
          <HeatmapTab repoId={repoId} />
        </TabsContent>
        <TabsContent value="hotspots" className="space-y-6">
          <HotspotsTab repoId={repoId} />
        </TabsContent>
        <TabsContent value="modules" className="space-y-6">
          <ModulesTab repoId={repoId} />
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
      </Tabs>
    </div>
  );
}
