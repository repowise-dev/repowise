"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import {
  GraphFlow as GraphFlowShell,
  type GraphFlowProps as GraphFlowShellProps,
} from "@repowise-dev/ui/graph/graph-flow";
import {
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
  ExecutionFlows,
  CommunitySummaryItem,
  ArchitectureGraph,
  CommunitySlice,
} from "@repowise-dev/types/graph";

type ViewMode = "full" | "architecture" | "dead" | "hotfiles" | "unified";

export interface GraphFlowProps {
  repoId: string;
  repoName?: string;
  initialViewMode?: ViewMode;
  /** Controlled scope — the page owns it via `?view=`. */
  viewMode?: ViewMode;
  /** Controlled module filter — the page owns it via `?module=`. */
  activeModule?: string | null;
  /** Node cap for the full-graph fetch, stepped up by the truncation banner.
   *  Must be the SAME value the banner is reporting: this and the banner's own
   *  fetch share an SWR key, so a mismatch means the caption describes a
   *  payload the canvas never received. It described one for a while — "Load
   *  more" raised the banner's limit and nothing else, so the sentence said
   *  3,000 over a canvas still drawing 1,500. */
  graphLimit?: number | undefined;
  onModuleGroupsChange?: GraphFlowShellProps["onModuleGroupsChange"];
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
}

export function GraphFlow({
  repoId,
  repoName,
  initialViewMode,
  viewMode: controlledViewMode,
  activeModule,
  graphLimit,
  onModuleGroupsChange,
  initialColorMode,
  colorMode,
  initialSelectedNode,
  onNodeClick,
  onNodeViewDocs,
  onCommunityPanelOpen,
  onViewModeChange,
  onColorModeChange,
}: GraphFlowProps) {
  const router = useRouter();
  // Constellation (Knowledge Graph) is the default scope.
  const [viewModeState, setViewModeState] = useState<ViewMode>(
    initialViewMode ?? "architecture",
  );
  const viewMode = controlledViewMode ?? viewModeState;
  // Currently-expanded constellation hubs (community ids). Drives the slice
  // fetch; the shell owns the actual expand/collapse interaction state.
  const [expandedHubs, setExpandedHubs] = useState<number[]>([]);

  const needsFullGraph = viewMode === "full" || viewMode === "unified";
  const { graph: fullGraph, isLoading: fullLoading } = useGraph(
    needsFullGraph ? repoId : null,
    graphLimit,
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
  // Flow traces highlight file-level nodes; the constellation has none, so it
  // skips the fetch. Dead/hot render file graphs, so flows work there too.
  const { flows: executionFlowsData } = useExecutionFlows(
    viewMode !== "architecture" ? repoId : null,
    {
      top_n: 10,
      max_depth: 6,
    },
  );

  return (
    <GraphFlowShell
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
      viewMode={controlledViewMode}
      activeModule={activeModule}
      onModuleGroupsChange={onModuleGroupsChange}
      initialColorMode={initialColorMode}
      colorMode={colorMode}
      initialSelectedNode={initialSelectedNode}
      onViewModeChange={(mode) => {
        setViewModeState(mode);
        onViewModeChange?.(mode);
      }}
      onColorModeChange={onColorModeChange}
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
