"use client";

import useSWR from "swr";
import { DependencyHeatmap } from "@repowise-dev/ui/dashboard/dependency-heatmap";
import { ModuleOverviewGrid } from "@repowise-dev/ui/dashboard/module-overview-grid";
import { OwnershipTreemap } from "@repowise-dev/ui/dashboard/ownership-treemap";
import { ExecutionFlowsPanel } from "@repowise-dev/ui/dashboard/execution-flows-panel";
import { BusFactorPanel } from "@repowise-dev/ui/git/bus-factor-panel";
import { EmptyState } from "@repowise-dev/ui/shared";
import { Card, CardContent, CardHeader, CardTitle } from "@repowise-dev/ui/ui/card";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { CommunitySummaryGridWrapper } from "@/components/dashboard/community-summary-grid-wrapper";
import { useCommunities, useExecutionFlows, useModuleGraph } from "@/lib/hooks/use-graph";
import { getHotspotsPage, getOwnership } from "@/lib/api/git";

const SWR_OPTS = { revalidateOnFocus: false, revalidateOnReconnect: false };

function PanelSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <Card>
      <CardContent className="space-y-2 pt-4">
        {Array.from({ length: rows }).map((_, i) => (
          <Skeleton key={i} className="h-5 w-full" />
        ))}
      </CardContent>
    </Card>
  );
}

interface StructureTabProps {
  repoId: string;
}

/** "How is this built" — module map, dependency heatmap, communities,
 *  execution flows, plus ownership treemap and bus factor fed by the full
 *  ownership/hotspot datasets (not the summary slice). */
export function StructureTab({ repoId }: StructureTabProps) {
  const { graph: moduleGraph, isLoading: modulesLoading } = useModuleGraph(repoId);
  const { communities, isLoading: communitiesLoading } = useCommunities(repoId);
  const { flows, isLoading: flowsLoading } = useExecutionFlows(repoId, {
    top_n: 5,
    max_depth: 5,
  });
  const { data: ownership, isLoading: ownershipLoading } = useSWR(
    `overview-ownership:${repoId}`,
    () => getOwnership(repoId, "module"),
    SWR_OPTS,
  );
  const { data: hotspotsPage, isLoading: hotspotsLoading } = useSWR(
    `overview-hotspots:${repoId}`,
    () => getHotspotsPage(repoId, { limit: 50 }),
    SWR_OPTS,
  );

  const hasModules = !!moduleGraph && moduleGraph.nodes.length > 0;

  return (
    <div className="space-y-4">
      {modulesLoading ? (
        <PanelSkeleton rows={4} />
      ) : hasModules ? (
        <ModuleOverviewGrid nodes={moduleGraph.nodes} edges={moduleGraph.edges} repoId={repoId} />
      ) : (
        <EmptyState
          title="No module graph yet"
          description="Module structure appears once indexing completes. Run repowise update to refresh."
        />
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {modulesLoading ? (
          <PanelSkeleton />
        ) : hasModules ? (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium">Module Dependencies</CardTitle>
            </CardHeader>
            <CardContent>
              <DependencyHeatmap moduleGraph={moduleGraph} />
            </CardContent>
          </Card>
        ) : null}
        {communitiesLoading ? (
          <PanelSkeleton />
        ) : communities && communities.length > 0 ? (
          <CommunitySummaryGridWrapper communities={communities} repoId={repoId} />
        ) : null}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {flowsLoading ? (
          <PanelSkeleton />
        ) : flows && flows.flows.length > 0 ? (
          <ExecutionFlowsPanel flows={flows.flows} repoId={repoId} />
        ) : null}
        {hotspotsLoading ? (
          <PanelSkeleton />
        ) : hotspotsPage && hotspotsPage.items.length > 0 ? (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium">Bus Factor</CardTitle>
            </CardHeader>
            <CardContent>
              <BusFactorPanel hotspots={hotspotsPage.items} />
            </CardContent>
          </Card>
        ) : null}
      </div>

      {ownershipLoading ? (
        <PanelSkeleton rows={8} />
      ) : ownership && ownership.length > 0 ? (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">Ownership Map</CardTitle>
          </CardHeader>
          <CardContent>
            <OwnershipTreemap entries={ownership} />
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
