"use client";

import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";
import { ShieldAlert } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@repowise-dev/ui/ui/tabs";
import { HeatmapTab } from "@/components/risk/heatmap-tab";
import { HotspotsTab } from "@/components/risk/hotspots-tab";
import { DeadCodeTab } from "@/components/risk/dead-code-tab";
import { ImpactTab } from "@/components/risk/impact-tab";
import { RiskSummaryStrip } from "@/components/risk/risk-summary-strip";

const TABS = ["heatmap", "hotspots", "dead-code", "impact"] as const;
type TabId = (typeof TABS)[number];

export default function RiskPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const repoId = params.id;

  const rawTab = searchParams.get("tab") as TabId | null;
  const activeTab: TabId = rawTab && TABS.includes(rawTab) ? rawTab : "heatmap";

  const setTab = useCallback(
    (next: string) => {
      const sp = new URLSearchParams(searchParams.toString());
      if (next === "heatmap") sp.delete("tab");
      else sp.set("tab", next);
      const qs = sp.toString();
      router.replace(qs ? `?${qs}` : "?", { scroll: false });
    },
    [router, searchParams],
  );

  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-[1600px]">
      <div>
        <h1 className="text-xl font-semibold text-[var(--color-text-primary)] mb-1 flex items-center gap-2">
          <ShieldAlert className="h-5 w-5 text-orange-500" />
          Risk
        </h1>
        <p className="text-sm text-[var(--color-text-secondary)]">
          Where the risk is concentrated — ownership silos, churn hotspots, dead code, and PR
          blast radius — in one place.
        </p>
      </div>

      <RiskSummaryStrip repoId={repoId} />

      <Tabs value={activeTab} onValueChange={setTab} className="space-y-4">
        <TabsList className="h-auto flex-wrap">
          <TabsTrigger value="heatmap">Heatmap</TabsTrigger>
          <TabsTrigger value="hotspots">Hotspots</TabsTrigger>
          <TabsTrigger value="dead-code">Dead code</TabsTrigger>
          <TabsTrigger value="impact">Impact analyzer</TabsTrigger>
        </TabsList>

        <TabsContent value="heatmap" className="space-y-6">
          <HeatmapTab repoId={repoId} />
        </TabsContent>
        <TabsContent value="hotspots" className="space-y-6">
          <HotspotsTab repoId={repoId} />
        </TabsContent>
        <TabsContent value="dead-code" className="space-y-6">
          <DeadCodeTab repoId={repoId} />
        </TabsContent>
        <TabsContent value="impact" className="space-y-6">
          <ImpactTab repoId={repoId} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
