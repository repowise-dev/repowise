/**
 * Architecture webview: the web Architecture page's Map, composed from the
 * same shared pieces (packages/web app/repos/[id]/architecture and
 * components/architecture/graph-view.tsx). The dependency graph at community
 * or file scope, or as the outline, steered by the one `GraphScopeSwitcher`;
 * the narrowing select and truncation banner beside it; the shared
 * `GraphFlow` shell with its toolbar, legend, inspector, community panel and
 * path finder.
 *
 * The web page keeps scope, signal, module, community and population in the
 * URL; a webview has no URL, so the same axes are plain state here and reset
 * the same way the web handlers reset their params. The Coupling, Packages
 * and Symbols tabs are separate datasets with their own pages on the web and
 * are not part of this panel. Every "open this file" action opens it in an
 * editor column through the host.
 */

import { useCallback, useMemo, useState, type ComponentProps, type ReactNode } from "react";
import { GraphFlow, type GraphFlowProps } from "@repowise-dev/ui/graph/graph-flow";
import { GraphTruncationBanner } from "@repowise-dev/ui/graph/graph-truncation-banner";
import {
  GraphNarrowingSelect,
  GraphScopeSwitcher,
  type GraphScope,
} from "@repowise-dev/ui/graph/graph-scope-controls";
import { CodeGraphTree } from "@repowise-dev/ui/graph/code-graph-tree";
import { GraphCommunityPanel } from "@repowise-dev/ui/graph/graph-community-panel";
import { PathFinderPanel } from "@repowise-dev/ui/graph/path-finder-panel";
import type { ModuleGroup } from "@repowise-dev/ui/graph/use-module-filter";
import { ApiError } from "@repowise-dev/ui/shared";
import { isExternal } from "@repowise-dev/types";
import {
  PRODUCTION_ONLY,
  type CommunityDetail,
  type GraphPath,
  type GraphPopulation,
  type NodeSearchResult,
} from "@repowise-dev/types/graph";
import type { WebviewHost } from "../../runtime/rpc";
import type { ViewProps } from "../../runtime/mount";
import {
  useArchitectureData,
  useHostQuery,
  type ArchitectureData,
  type ViewMode,
} from "./use-architecture-data";

type Scope = "communities" | "files";
type Signal = "dead" | "hot" | null;
type GraphColorMode = NonNullable<GraphFlowProps["colorMode"]>;

/** Same copy the web page shows under each scope. */
const DESCRIPTION: Record<Scope, string> = {
  communities:
    "Files that depend on each other more than on the rest of the repo, detected automatically. Circle size is how much code a group holds, and the nearer the centre, the nearer an entry point. Double-click a group to see the files inside it and what they depend on.",
  files:
    "Every file, grouped into the communities it depends on most, with a band for each strong link between communities. Hover or select a file to see exactly what it imports and what imports it.",
};

/** Scope + signal -> the canvas's internal ViewMode, as the web maps them. */
function toViewMode(scope: Scope, signal: Signal): ViewMode {
  if (scope === "communities") return "architecture";
  if (signal === "dead") return "dead";
  if (signal === "hot") return "hotfiles";
  return "full";
}

/** Hub (`__community__N`) and repo-core ids are synthetic, and third-party
 *  and framework nodes name code outside the repo: none of them is a file. */
function isFileBackedNodeId(nodeId: string): boolean {
  return !nodeId.startsWith("__") && !isExternal(nodeId);
}

function populationKey(p: GraphPopulation): string {
  return `${+p.tests}${+p.examples}${+p.docs}`;
}

export function App({ host, params, repo, refreshToken }: ViewProps<"architecture">) {
  // The web page lands on Files. A preselected file pins the file scope, the
  // way a `?node=` link does there.
  // Dropped on the first scope or community change, as the web drops `?node=`,
  // so a later remount of the canvas does not reselect the launch file.
  const [initialNode, setInitialNode] = useState<string | null>(params.selectPath ?? null);
  const [colorMode, setColorMode] = useState<GraphColorMode>("community");
  const [graphScope, setGraphScope] = useState<GraphScope>("files");
  const [signal, setSignal] = useState<Signal>(null);
  const [activeModule, setActiveModule] = useState<string | null>(null);
  const [activeCommunity, setActiveCommunity] = useState<number | null>(null);
  const [population, setPopulation] = useState<GraphPopulation>(PRODUCTION_ONLY);
  const [showExternal, setShowExternal] = useState(false);
  const [graphLimit, setGraphLimit] = useState<number | undefined>(undefined);
  const [moduleGroups, setModuleGroups] = useState<ModuleGroup[]>([]);
  const [flowsRequested, setFlowsRequested] = useState(false);

  const tree = graphScope === "tree";
  const scope: Scope = graphScope === "communities" ? "communities" : "files";
  const viewMode = toViewMode(scope, signal);
  const isCommunities = scope === "communities";

  const data = useArchitectureData(host, refreshToken, {
    viewMode,
    tree,
    population,
    narrowingPopulation: isCommunities || activeCommunity !== null ? population : undefined,
    activeCommunity,
    graphLimit,
    flowsRequested,
  });

  const handleScopeChange = useCallback((next: GraphScope) => {
    // Narrowing is a file-scope concept and does not survive a scope change.
    setActiveModule(null);
    setActiveCommunity(null);
    setInitialNode(null);
    setGraphScope(next);
  }, []);

  // Entering a community implies the file scope; leaving returns to the
  // constellation it came from.
  const handleActiveCommunityChange = useCallback((next: number | null) => {
    setActiveCommunity(next);
    setInitialNode(null);
    if (next !== null) {
      setActiveModule(null);
      // A slice was never filtered to dead or hot files.
      setSignal(null);
      setGraphScope("files");
    } else {
      setGraphScope("communities");
    }
  }, []);

  const handleNarrowingChange = useCallback(
    ({ module, community }: { module: string | null; community: number | null }) => {
      if (community !== null) {
        handleActiveCommunityChange(community);
        return;
      }
      setActiveCommunity(null);
      setActiveModule(module);
    },
    [handleActiveCommunityChange],
  );

  const handleViewModeChange = useCallback((mode: ViewMode) => {
    setSignal(mode === "dead" ? "dead" : mode === "hotfiles" ? "hot" : null);
  }, []);

  const handleFlowsVisibilityChange = useCallback((visible: boolean) => {
    if (visible) setFlowsRequested(true);
  }, []);

  const openIfFile = useCallback(
    (nodeId: string) => {
      if (isFileBackedNodeId(nodeId)) host.openFile(nodeId);
    },
    [host],
  );

  const handleNodeClick = useCallback<NonNullable<GraphFlowProps["onNodeClick"]>>(
    (nodeId, nodeType) => {
      if (nodeType === "file" && isFileBackedNodeId(nodeId)) host.openFile(nodeId);
    },
    [host],
  );

  // Only the unfiltered file scope (and the outline) draws the capped
  // payload, so only they carry its truncation banner.
  const usesFullGraph = tree || (scope === "files" && !signal && activeCommunity === null);
  const graph = data.fullGraph.data;
  const truncation =
    usesFullGraph && graph?.truncated && graph.total_node_count != null ? (
      <GraphTruncationBanner
        shown={graph.nodes.length}
        total={graph.total_node_count}
        limit={graphLimit ?? graph.nodes.length}
        onLoadMore={(next) => setGraphLimit(next)}
        {...(tree ? {} : { onSwitchToArchitecture: () => handleScopeChange("communities") })}
      />
    ) : undefined;

  const headerControls = useMemo(
    () => (
      <div className="flex flex-wrap items-center gap-2">
        {!isCommunities && (
          <GraphNarrowingSelect
            modules={moduleGroups}
            communities={data.narrowingCommunities.data}
            activeModule={activeModule}
            activeCommunity={activeCommunity}
            onChange={handleNarrowingChange}
          />
        )}
        <GraphScopeSwitcher scope={scope} onScopeChange={handleScopeChange} />
      </div>
    ),
    [
      isCommunities,
      moduleGroups,
      data.narrowingCommunities.data,
      activeModule,
      activeCommunity,
      handleNarrowingChange,
      scope,
      handleScopeChange,
    ],
  );

  if (tree) {
    return (
      <TreeScope
        host={host}
        graph={data.fullGraph}
        banner={truncation}
        onScopeChange={handleScopeChange}
      />
    );
  }

  // A failed slice is reported above the canvas rather than in place of it,
  // so the scope switcher and the way out of a community stay on screen.
  const banner = data.error ? (
    <ApiError title="Could not load the architecture map." message={data.error} />
  ) : (
    truncation
  );

  return (
    <div className="flex h-full flex-col">
      <GraphFlow
        description={DESCRIPTION[scope]}
        headerActions={headerControls}
        banner={banner}
        fullGraph={graph}
        isLoadingFullGraph={data.fullGraph.isLoading}
        constellationGraph={data.constellationGraph.data}
        isLoadingConstellationGraph={data.constellationGraph.isLoading}
        communitySlice={data.communitySlice.data}
        isLoadingCommunitySlice={data.communitySlice.isLoading}
        activeCommunity={activeCommunity}
        onActiveCommunityChange={handleActiveCommunityChange}
        population={population}
        onPopulationChange={setPopulation}
        showExternal={showExternal}
        onShowExternalChange={setShowExternal}
        repoName={repo.name}
        deadCodeGraph={data.deadCodeGraph.data}
        isLoadingDeadCodeGraph={data.deadCodeGraph.isLoading}
        hotFilesGraph={data.hotFilesGraph.data}
        isLoadingHotFilesGraph={data.hotFilesGraph.isLoading}
        communities={data.communities.data}
        executionFlows={data.executionFlows.data}
        onFlowsVisibilityChange={handleFlowsVisibilityChange}
        viewMode={viewMode}
        activeModule={activeModule}
        onModuleGroupsChange={setModuleGroups}
        colorMode={colorMode}
        onColorModeChange={setColorMode}
        initialSelectedNode={initialNode}
        onViewModeChange={handleViewModeChange}
        onNodeClick={handleNodeClick}
        onNodeViewDocs={openIfFile}
        renderPathFinder={(p) => (
          <PathFinderPanel
            searchNodes={(q, l) => host.api.searchNodes(q, l) as Promise<NodeSearchResult[]>}
            findPath={(from, to) => host.api.graphPath(from, to) as Promise<GraphPath>}
            onPathFound={p.onPathFound}
            onClear={p.onClear}
            onClose={p.onClose}
            initialFrom={p.initialFrom}
            initialTo={p.initialTo}
          />
        )}
        renderCommunityPanel={(p) => (
          <CommunityPanel
            host={host}
            refreshToken={refreshToken}
            communityId={p.communityId}
            population={population}
            onClose={p.onClose}
            onEnterCommunity={p.onEnterCommunity}
            onNeighborSelect={p.onNeighborSelect}
          />
        )}
      />
    </div>
  );
}

/**
 * The outline scope: the same capped file graph as an expandable tree, with
 * the scope switcher carrying the way back to the canvas. File rows open the
 * file in an editor column.
 */
function TreeScope({
  host,
  graph,
  banner,
  onScopeChange,
}: {
  host: WebviewHost;
  graph: ArchitectureData["fullGraph"];
  banner: ReactNode;
  onScopeChange: (scope: GraphScope) => void;
}) {
  const OpenFileLink = useMemo(
    () =>
      function OpenFileLink({ href, onClick, ...rest }: ComponentProps<"a">) {
        return (
          <a
            {...rest}
            href="#"
            onClick={(e) => {
              e.preventDefault();
              onClick?.(e);
              if (href) host.openFile(href);
            }}
          />
        );
      },
    [host],
  );

  return (
    <div className="h-full overflow-auto">
      <div className="max-w-[1600px] space-y-5 p-4 sm:p-6">
        <div className="flex flex-col gap-3 border-b border-[var(--color-border-default)] pb-4 sm:flex-row sm:items-start sm:justify-between">
          <p className="max-w-3xl text-sm text-[var(--color-text-secondary)]">
            The same files and dependencies the Map draws, as a tree you can open. Expand a
            directory to walk it, then a file to see the types it imports and which file each one
            comes from.
          </p>
          <div className="shrink-0">
            <GraphScopeSwitcher scope="tree" onScopeChange={onScopeChange} />
          </div>
        </div>
        {graph.error ? (
          <ApiError title="Couldn't load the code graph" message={graph.error} />
        ) : (
          <>
            {banner}
            <CodeGraphTree
              graph={graph.data}
              isLoading={graph.isLoading}
              fileHref={(path) => path}
              LinkComponent={OpenFileLink}
            />
          </>
        )}
      </div>
    </div>
  );
}

/**
 * Fetches one community's detail on open and feeds the shared panel shell,
 * mirroring the web app's SWR wrapper.
 */
function CommunityPanel({
  host,
  refreshToken,
  communityId,
  population,
  onClose,
  onEnterCommunity,
  onNeighborSelect,
}: {
  host: WebviewHost;
  refreshToken: number;
  communityId: number;
  population: GraphPopulation;
  onClose: () => void;
  onEnterCommunity: () => void;
  onNeighborSelect: (communityId: number) => void;
}) {
  const { data, isLoading } = useHostQuery(
    `community:${communityId}:${populationKey(population)}`,
    () => host.api.communityDetail(communityId, population) as Promise<CommunityDetail>,
    refreshToken,
  );
  return (
    <GraphCommunityPanel
      communityId={communityId}
      community={data ?? null}
      isLoading={isLoading}
      onClose={onClose}
      onEnterCommunity={onEnterCommunity}
      onNeighborSelect={onNeighborSelect}
    />
  );
}
