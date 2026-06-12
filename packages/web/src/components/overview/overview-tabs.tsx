"use client";

import { parseAsStringLiteral, useQueryState } from "nuqs";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@repowise-dev/ui/ui/tabs";
import { ErrorBoundary } from "@repowise-dev/ui/shared";
import { PulseTab } from "./pulse-tab";
import { StructureTab } from "./structure-tab";
import type { Hotspot } from "@repowise-dev/types/git";
import type { OverviewDecisionSlim } from "@repowise-dev/types/overview";

const TAB_VALUES = ["pulse", "structure"] as const;

interface OverviewTabsProps {
  repoId: string;
  hotspots: Hotspot[];
  hotspotTotal: number;
  decisions: OverviewDecisionSlim[];
}

/** Two purposeful, URL-synced tabs: Pulse (activity) and Structure (how
 *  it's built). Tab content fetches its own data and streams in behind
 *  skeletons; the summary slices arrive as props from the server payload. */
export function OverviewTabs({ repoId, hotspots, hotspotTotal, decisions }: OverviewTabsProps) {
  const [tab, setTab] = useQueryState(
    "tab",
    parseAsStringLiteral(TAB_VALUES).withDefault("pulse"),
  );

  return (
    <Tabs value={tab} onValueChange={(v) => setTab(v as (typeof TAB_VALUES)[number])}>
      <TabsList>
        <TabsTrigger value="pulse">Pulse</TabsTrigger>
        <TabsTrigger value="structure">Structure</TabsTrigger>
      </TabsList>
      <TabsContent value="pulse" className="mt-4">
        <ErrorBoundary title="Couldn't load activity">
          <PulseTab
            repoId={repoId}
            hotspots={hotspots}
            hotspotTotal={hotspotTotal}
            decisions={decisions}
          />
        </ErrorBoundary>
      </TabsContent>
      <TabsContent value="structure" className="mt-4">
        <ErrorBoundary title="Couldn't load structure">
          <StructureTab repoId={repoId} />
        </ErrorBoundary>
      </TabsContent>
    </Tabs>
  );
}
