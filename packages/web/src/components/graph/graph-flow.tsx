"use client";

import { useState } from "react";
import {
  GraphFlow as GraphFlowShell,
  type GraphFlowProps as GraphFlowShellProps,
} from "@repowise-dev/ui/graph/graph-flow";
import {
  useModuleGraph,
  useGraph,
  useArchitectureGraph,
  useDeadCodeGraph,
  useHotFilesGraph,
  useCommunities,
  useExecutionFlows,
} from "@/lib/hooks/use-graph";
import { PathFinderPanel } from "./path-finder-panel";
import { GraphCommunityPanel } from "./graph-community-panel";
import type {
  GraphExport,
  ModuleGraph,
  ExecutionFlows,
  CommunitySummaryItem,
} from "@repowise-dev/types/graph";

export interface GraphFlowProps {
  repoId: string;
  onNodeClick?: GraphFlowShellProps["onNodeClick"];
  onNodeViewDocs?: GraphFlowShellProps["onNodeViewDocs"];
}

export function GraphFlow({ repoId, onNodeClick, onNodeViewDocs }: GraphFlowProps) {
  // Track view-mode + drill-down so we can suppress fetches we don't need.
  const [viewMode, setViewMode] = useState<
    "module" | "full" | "architecture" | "dead" | "hotfiles"
  >("module");
  const [modulePath, setModulePath] = useState<string[]>([]);
  const isDrilledDown = modulePath.length > 0;
  const isModuleView = viewMode === "module";

  const { graph: moduleGraph, isLoading: moduleLoading } = useModuleGraph(
    isModuleView && !isDrilledDown ? repoId : null,
  );
  const needsFullGraph = isDrilledDown || viewMode === "full";
  const { graph: fullGraph, isLoading: fullLoading } = useGraph(
    needsFullGraph ? repoId : null,
  );
  const { graph: archGraph, isLoading: archLoading } = useArchitectureGraph(
    viewMode === "architecture" ? repoId : null,
  );
  const { graph: deadGraph, isLoading: deadLoading } = useDeadCodeGraph(
    viewMode === "dead" ? repoId : null,
  );
  const { graph: hotGraph, isLoading: hotLoading } = useHotFilesGraph(
    viewMode === "hotfiles" ? repoId : null,
  );
  const { communities } = useCommunities(repoId);
  const { flows: executionFlowsData } = useExecutionFlows(repoId, {
    top_n: 10,
    max_depth: 6,
  });

  return (
    <GraphFlowShell
      moduleGraph={moduleGraph as ModuleGraph | undefined}
      isLoadingModuleGraph={moduleLoading}
      fullGraph={fullGraph as GraphExport | undefined}
      isLoadingFullGraph={fullLoading}
      architectureGraph={archGraph as GraphExport | undefined}
      isLoadingArchitectureGraph={archLoading}
      deadCodeGraph={deadGraph as GraphExport | undefined}
      isLoadingDeadCodeGraph={deadLoading}
      hotFilesGraph={hotGraph as GraphExport | undefined}
      isLoadingHotFilesGraph={hotLoading}
      communities={communities as CommunitySummaryItem[] | undefined}
      executionFlows={executionFlowsData as ExecutionFlows | undefined}
      onViewModeChange={setViewMode}
      onModulePathChange={setModulePath}
      onNodeClick={onNodeClick}
      onNodeViewDocs={onNodeViewDocs}
      renderPathFinder={(props) => (
        <PathFinderPanel
          repoId={repoId}
          initialFrom={props.initialFrom}
          initialTo={props.initialTo}
          onPathFound={props.onPathFound}
          onClear={props.onClear}
          onClose={props.onClose}
        />
      )}
      renderCommunityPanel={(props) => (
        <GraphCommunityPanel
          repoId={repoId}
          communityId={props.communityId}
          onClose={props.onClose}
        />
      )}
    />
  );
}
