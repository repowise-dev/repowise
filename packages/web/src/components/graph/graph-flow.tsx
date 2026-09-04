"use client";

import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import {
  GraphFlow as GraphFlowShell,
  type GraphFlowProps as GraphFlowShellProps,
} from "@repowise-dev/ui/graph/graph-flow";
import {
  useGraph,
  useArchitectureCommunityGraph,
  useDeadCodeGraph,
  useHotFilesGraph,
  useCommunities,
  useCommunitySlice,
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
  GraphPopulation,
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
  /** Controlled drill-down — the page owns it via `?community=`. */
  activeCommunity?: number | null;
  onActiveCommunityChange?: (communityId: number | null) => void;
  /** Which non-production files the community views count — the page owns it
   *  via `?show=`. */
  population?: GraphPopulation | undefined;
  onPopulationChange?: ((next: GraphPopulation) => void) | undefined;
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
  onSelectedNodeChange?: GraphFlowShellProps["onSelectedNodeChange"];
  /** Fired whenever the live scope (viewMode) changes. The page uses this to
   *  track the current scope so it can conditionally fetch the capped full
   *  graph (and gate the truncation banner) only for scopes that render it. */
  onViewModeChange?: (mode: ViewMode) => void;
  /** Fired when the node color mode changes so the page can sync the URL. */
  onColorModeChange?: GraphFlowShellProps["onColorModeChange"];
  /** Header/rail slots, forwarded to the shared shell. */
  description?: GraphFlowShellProps["description"];
  headerActions?: GraphFlowShellProps["headerActions"];
  banner?: GraphFlowShellProps["banner"];
  rail?: GraphFlowShellProps["rail"];
}

export function GraphFlow({
  repoId,
  repoName,
  initialViewMode,
  viewMode: controlledViewMode,
  activeModule,
  activeCommunity,
  onActiveCommunityChange,
  population,
  onPopulationChange,
  graphLimit,
  onModuleGroupsChange,
  initialColorMode,
  colorMode,
  initialSelectedNode,
  onNodeClick,
  onNodeViewDocs,
  onCommunityPanelOpen,
  onSelectedNodeChange,
  onViewModeChange,
  onColorModeChange,
  description,
  headerActions,
  banner,
  rail,
}: GraphFlowProps) {
  const router = useRouter();
  // Constellation (Knowledge Graph) is the default scope.
  const [viewModeState, setViewModeState] = useState<ViewMode>(
    initialViewMode ?? "architecture",
  );
  const viewMode = controlledViewMode ?? viewModeState;
  // Latched on first open of the flows panel. It starts closed, so this keeps a
  // trace fetch nobody asked for off every file-scope render.
  const [flowsRequested, setFlowsRequested] = useState(false);
  const handleFlowsVisibilityChange = useCallback((visible: boolean) => {
    if (visible) setFlowsRequested(true);
  }, []);

  const needsFullGraph = viewMode === "full" || viewMode === "unified";
  const { graph: fullGraph, isLoading: fullLoading } = useGraph(
    needsFullGraph ? repoId : null,
    graphLimit,
  );
  // Constellation community super-graph — only fetched for the radial scope.
  const { graph: constellationGraph, isLoading: constellationLoading } =
    useArchitectureCommunityGraph(viewMode === "architecture" ? repoId : null, population);
  const { graph: deadGraph, isLoading: deadLoading } = useDeadCodeGraph(
    viewMode === "dead" ? repoId : null,
  );
  const { graph: hotGraph, isLoading: hotLoading } = useHotFilesGraph(
    viewMode === "hotfiles" ? repoId : null,
  );
  // The entered community's own sub-graph. Conditional inside the hook, so no
  // fetch happens until somebody drills in, and none survives leaving.
  const { slice: communitySlice, isLoading: sliceLoading } = useCommunitySlice(
    viewMode !== "architecture" && activeCommunity != null ? repoId : null,
    activeCommunity ?? null,
    population,
  );
  const { repo } = useRepo(repoId);
  const resolvedRepoName = repoName ?? repo?.name;
  const { communities } = useCommunities(repoId, population);
  // File-level only (the constellation has no file nodes), and only once the
  // panel that reads them has been opened.
  const { flows: executionFlowsData } = useExecutionFlows(
    flowsRequested && viewMode !== "architecture" ? repoId : null,
    {
      top_n: 10,
      max_depth: 6,
    },
  );

  return (
    <GraphFlowShell
      description={description}
      headerActions={headerActions}
      banner={banner}
      rail={rail}
      fullGraph={fullGraph as GraphExport | undefined}
      isLoadingFullGraph={fullLoading}
      constellationGraph={constellationGraph as ArchitectureGraph | undefined}
      isLoadingConstellationGraph={constellationLoading}
      communitySlice={communitySlice as CommunitySlice | undefined}
      isLoadingCommunitySlice={sliceLoading}
      activeCommunity={activeCommunity}
      onActiveCommunityChange={onActiveCommunityChange}
      population={population}
      onPopulationChange={onPopulationChange}
      {...(resolvedRepoName ? { repoName: resolvedRepoName } : {})}
      deadCodeGraph={deadGraph as GraphExport | undefined}
      isLoadingDeadCodeGraph={deadLoading}
      hotFilesGraph={hotGraph as GraphExport | undefined}
      isLoadingHotFilesGraph={hotLoading}
      communities={communities as CommunitySummaryItem[] | undefined}
      executionFlows={executionFlowsData as ExecutionFlows | undefined}
      onFlowsVisibilityChange={handleFlowsVisibilityChange}
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
      // The file page already carries a Health, a History and a Decisions tab
      // for exactly this file, so the graph hands off to them rather than to a
      // repo-wide page the reader then has to search.
      fileHealthHrefFor={(nodeId) =>
        `${fileEntityPath(`/repos/${repoId}`, nodeId)}?tab=health`
      }
      fileHistoryHrefFor={(nodeId) =>
        `${fileEntityPath(`/repos/${repoId}`, nodeId)}?tab=history`
      }
      fileDecisionsHrefFor={(nodeId) =>
        `${fileEntityPath(`/repos/${repoId}`, nodeId)}?tab=decisions`
      }
      deadCodeHref={`/repos/${repoId}/code-health?tab=dead-code`}
      onCommunityPanelOpen={onCommunityPanelOpen}
      onSelectedNodeChange={onSelectedNodeChange}
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
          population={population}
          onClose={props.onClose}
          onEnterCommunity={props.onEnterCommunity}
          onNeighborSelect={props.onNeighborSelect}
        />
      )}
    />
  );
}
