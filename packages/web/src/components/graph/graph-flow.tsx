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

type ViewMode = "module" | "full" | "architecture" | "dead" | "hotfiles" | "unified";

export interface GraphFlowProps {
  repoId: string;
  initialViewMode?: ViewMode;
  initialSelectedNode?: string | null;
  onNodeClick?: GraphFlowShellProps["onNodeClick"];
  onNodeViewDocs?: GraphFlowShellProps["onNodeViewDocs"];
  /** Fired when the community detail panel opens (legend click).
   *  Page uses this to dismiss the doc panel so the right rail stays
   *  to a single surface. */
  onCommunityPanelOpen?: (communityId: number) => void;
}

export function GraphFlow({
  repoId,
  initialViewMode,
  initialSelectedNode,
  onNodeClick,
  onNodeViewDocs,
  onCommunityPanelOpen,
}: GraphFlowProps) {
  const [viewMode, setViewMode] = useState<ViewMode>(initialViewMode ?? "module");
  const [modulePath, setModulePath] = useState<string[]>([]);
  const [hasExpandedModules, setHasExpandedModules] = useState(false);
  const isDrilledDown = modulePath.length > 0;
  const isModuleView = viewMode === "module";

  const { graph: moduleGraph, isLoading: moduleLoading } = useModuleGraph(
    isModuleView ? repoId : null,
  );
  const needsFullGraph = isDrilledDown || viewMode === "full" || viewMode === "unified" || hasExpandedModules;
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
      initialViewMode={initialViewMode}
      initialSelectedNode={initialSelectedNode}
      onViewModeChange={setViewMode}
      onModulePathChange={setModulePath}
      onExpandedModulesChange={(expanded) => setHasExpandedModules(expanded.size > 0)}
      onNodeClick={onNodeClick}
      onNodeViewDocs={onNodeViewDocs}
      onCommunityPanelOpen={onCommunityPanelOpen}
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
