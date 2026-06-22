"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import {
  GraphFlow as GraphFlowShell,
  type GraphFlowProps as GraphFlowShellProps,
} from "@repowise-dev/ui/graph/graph-flow";
import {
  useModuleGraph,
  useGraph,
  useArchitectureGraph,
  useArchitectureCommunityGraph,
  useDeadCodeGraph,
  useHotFilesGraph,
  useCommunities,
  useCommunitySlices,
  useExecutionFlows,
} from "@/lib/hooks/use-graph";
import { useRepo } from "@/lib/hooks/use-repo";
import { PathFinderPanel } from "./path-finder-panel";
import { GraphCommunityPanel } from "./graph-community-panel";
import type {
  GraphExport,
  ModuleGraph,
  ExecutionFlows,
  CommunitySummaryItem,
  ArchitectureGraph,
  CommunitySlice,
} from "@repowise-dev/types/graph";

type ViewMode = "module" | "full" | "architecture" | "dead" | "hotfiles" | "unified";

export interface GraphFlowProps {
  repoId: string;
  repoName?: string;
  initialViewMode?: ViewMode;
  initialColorMode?: GraphFlowShellProps["initialColorMode"];
  /** Controlled node color mode — the page URL-syncs it and passes it down. */
  colorMode?: GraphFlowShellProps["colorMode"];
  initialSelectedNode?: string | null;
  onNodeClick?: GraphFlowShellProps["onNodeClick"];
  onNodeViewDocs?: GraphFlowShellProps["onNodeViewDocs"];
  /** Fired when the community detail panel opens (legend click).
   *  Page uses this to dismiss the doc panel so the right rail stays
   *  to a single surface. */
  onCommunityPanelOpen?: (communityId: number) => void;
  /** Fired whenever the live scope (viewMode) changes. The page uses this to
   *  track the current scope so it can conditionally fetch the capped full
   *  graph (and gate the truncation banner) only for scopes that render it. */
  onViewModeChange?: (mode: ViewMode) => void;
  /** Fired when the node color mode changes so the page can sync the URL. */
  onColorModeChange?: GraphFlowShellProps["onColorModeChange"];
  /** Restricts the in-canvas scope cluster (Explore omits the constellation). */
  availableScopes?: GraphFlowShellProps["availableScopes"];
}

export function GraphFlow({
  repoId,
  repoName,
  initialViewMode,
  initialColorMode,
  colorMode,
  initialSelectedNode,
  onNodeClick,
  onNodeViewDocs,
  onCommunityPanelOpen,
  onViewModeChange,
  onColorModeChange,
  availableScopes,
}: GraphFlowProps) {
  const router = useRouter();
  // Constellation (Knowledge Graph) is the default scope.
  const [viewMode, setViewMode] = useState<ViewMode>(initialViewMode ?? "architecture");
  const [modulePath, setModulePath] = useState<string[]>([]);
  const [hasExpandedModules, setHasExpandedModules] = useState(false);
  // Currently-expanded constellation hubs (community ids). Drives the slice
  // fetch; the shell owns the actual expand/collapse interaction state.
  const [expandedHubs, setExpandedHubs] = useState<number[]>([]);
  const isDrilledDown = modulePath.length > 0;
  const isModuleView = viewMode === "module";

  const { graph: moduleGraph, isLoading: moduleLoading } = useModuleGraph(
    isModuleView ? repoId : null,
  );
  const needsFullGraph = isDrilledDown || viewMode === "full" || viewMode === "unified" || hasExpandedModules;
  const { graph: fullGraph, isLoading: fullLoading } = useGraph(
    needsFullGraph ? repoId : null,
  );
  const { graph: archGraph, isLoading: archLoading } = useArchitectureGraph(null);
  // Constellation community super-graph — only fetched for the radial scope.
  const { graph: constellationGraph, isLoading: constellationLoading } =
    useArchitectureCommunityGraph(viewMode === "architecture" ? repoId : null);
  const { graph: deadGraph, isLoading: deadLoading } = useDeadCodeGraph(
    viewMode === "dead" ? repoId : null,
  );
  const { graph: hotGraph, isLoading: hotLoading } = useHotFilesGraph(
    viewMode === "hotfiles" ? repoId : null,
  );
  // Member slices for expanded hubs — only fetched in the constellation scope
  // and only while at least one hub is open (conditional SWR inside the hook).
  const { slices: constellationSlices } = useCommunitySlices(
    viewMode === "architecture" ? repoId : null,
    expandedHubs,
  );
  const { repo } = useRepo(repoId);
  const resolvedRepoName = repoName ?? repo?.name;
  const { communities } = useCommunities(repoId);
  // Execution flows only highlight a file-level trace, so defer the fetch until
  // a full/file graph is actually needed (keeps the modules mount to
  // /communities + /modules only).
  const { flows: executionFlowsData } = useExecutionFlows(
    needsFullGraph ? repoId : null,
    {
      top_n: 10,
      max_depth: 6,
    },
  );

  return (
    <GraphFlowShell
      moduleGraph={moduleGraph as ModuleGraph | undefined}
      isLoadingModuleGraph={moduleLoading}
      fullGraph={fullGraph as GraphExport | undefined}
      isLoadingFullGraph={fullLoading}
      architectureGraph={archGraph as GraphExport | undefined}
      isLoadingArchitectureGraph={archLoading}
      constellationGraph={constellationGraph as ArchitectureGraph | undefined}
      isLoadingConstellationGraph={constellationLoading}
      constellationSlices={constellationSlices as Map<number, CommunitySlice> | undefined}
      onExpandedHubsChange={setExpandedHubs}
      {...(resolvedRepoName ? { repoName: resolvedRepoName } : {})}
      deadCodeGraph={deadGraph as GraphExport | undefined}
      isLoadingDeadCodeGraph={deadLoading}
      hotFilesGraph={hotGraph as GraphExport | undefined}
      isLoadingHotFilesGraph={hotLoading}
      communities={communities as CommunitySummaryItem[] | undefined}
      executionFlows={executionFlowsData as ExecutionFlows | undefined}
      initialViewMode={initialViewMode}
      initialColorMode={initialColorMode}
      colorMode={colorMode}
      initialSelectedNode={initialSelectedNode}
      onViewModeChange={(mode) => {
        setViewMode(mode);
        onViewModeChange?.(mode);
      }}
      onColorModeChange={onColorModeChange}
      availableScopes={availableScopes}
      onModulePathChange={setModulePath}
      onExpandedModulesChange={(expanded) => setHasExpandedModules(expanded.size > 0)}
      onNodeClick={onNodeClick}
      onNodeViewDocs={onNodeViewDocs}
      onNodeViewSymbols={(nodeId) =>
        router.push(
          `/repos/${repoId}/architecture?view=symbols&file=${encodeURIComponent(nodeId)}`,
        )
      }
      fileHrefFor={(nodeId) => fileEntityPath(`/repos/${repoId}`, nodeId)}
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
          onExpandOnCanvas={props.onExpandOnCanvas}
        />
      )}
    />
  );
}
