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
 * URL; a webview has no URL, so the same axes are plain state here
 * (`useMapState`) and reset the same way the web handlers reset their params.
 * The Coupling, Packages and Symbols tabs are separate datasets with their
 * own pages on the web and are not part of this panel. Every "open this file"
 * action opens it in an editor column through the host.
 */

import { useCallback, useMemo, type ComponentProps, type ReactNode } from "react";
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
import { ApiError } from "@repowise-dev/ui/shared";
import { isExternal } from "@repowise-dev/types";
import type {
  CommunityDetail,
  CommunitySummaryItem,
  GraphPath,
  GraphPopulation,
  NodeSearchResult,
} from "@repowise-dev/types/graph";
import type { WebviewHost } from "../../runtime/rpc";
import type { ViewProps } from "../../runtime/mount";
import { useArchitectureData, useHostQuery, type ArchitectureData } from "./use-architecture-data";
import { useMapState, type MapState, type Scope } from "./use-map-state";

/** Same copy the web page shows under each scope. */
const DESCRIPTION: Record<Scope, string> = {
  communities:
    "Files that depend on each other more than on the rest of the repo, detected automatically. Circle size is how much code a group holds, and the nearer the centre, the nearer an entry point. Double-click a group to see the files inside it and what they depend on.",
  files:
    "Every file, grouped into the communities it depends on most, with a band for each strong link between communities. Hover or select a file to see exactly what it imports and what imports it.",
};

/** Hub (`__community__N`) and repo-core ids are synthetic, and third-party
 *  and framework nodes name code outside the repo: none of them is a file. */
function isFileBackedNodeId(nodeId: string): boolean {
  return !nodeId.startsWith("__") && !isExternal(nodeId);
}

function populationKey(p: GraphPopulation): string {
  return `${+p.tests}${+p.examples}${+p.docs}`;
}

export function App({ host, params, repo, refreshToken }: ViewProps<"architecture">) {
  const map = useMapState(params.selectPath);
  const data = useArchitectureData(host, refreshToken, {
    viewMode: map.viewMode,
    tree: map.tree,
    population: map.population,
    // The whole-repo file graph is not population-filtered, so neither is its
    // narrowing list.
    narrowingPopulation:
      map.scope === "communities" || map.activeCommunity !== null ? map.population : undefined,
    activeCommunity: map.activeCommunity,
    graphLimit: map.graphLimit,
    flowsRequested: map.flowsRequested,
  });

  const openIfFile = useCallback(
    (nodeId: string) => {
      if (isFileBackedNodeId(nodeId)) host.openFile(nodeId);
    },
    [host],
  );
  const handleNodeClick = useCallback<NonNullable<GraphFlowProps["onNodeClick"]>>(
    (nodeId, nodeType) => {
      if (nodeType === "file") openIfFile(nodeId);
    },
    [openIfFile],
  );

  const truncation = <MapTruncation map={map} graph={data.fullGraph} />;

  if (map.tree) {
    return (
      <TreeScope
        host={host}
        graph={data.fullGraph}
        banner={truncation}
        onScopeChange={map.handleScopeChange}
      />
    );
  }

  return (
    <div className="flex h-full flex-col">
      <GraphFlow
        description={DESCRIPTION[map.scope]}
        headerActions={
          <HeaderControls map={map} narrowingCommunities={data.narrowingCommunities.data} />
        }
        // A failed slice is reported above the canvas rather than in place of
        // it, so the scope switcher and the way out of a community stay.
        banner={<MapBanner error={data.error}>{truncation}</MapBanner>}
        fullGraph={data.fullGraph.data}
        isLoadingFullGraph={data.fullGraph.isLoading}
        constellationGraph={data.constellationGraph.data}
        isLoadingConstellationGraph={data.constellationGraph.isLoading}
        communitySlice={data.communitySlice.data}
        isLoadingCommunitySlice={data.communitySlice.isLoading}
        activeCommunity={map.activeCommunity}
        onActiveCommunityChange={map.handleActiveCommunityChange}
        population={map.population}
        onPopulationChange={map.setPopulation}
        showExternal={map.showExternal}
        onShowExternalChange={map.setShowExternal}
        repoName={repo.name}
        deadCodeGraph={data.deadCodeGraph.data}
        isLoadingDeadCodeGraph={data.deadCodeGraph.isLoading}
        hotFilesGraph={data.hotFilesGraph.data}
        isLoadingHotFilesGraph={data.hotFilesGraph.isLoading}
        communities={data.communities.data}
        executionFlows={data.executionFlows.data}
        onFlowsVisibilityChange={map.handleFlowsVisibilityChange}
        viewMode={map.viewMode}
        activeModule={map.activeModule}
        onModuleGroupsChange={map.setModuleGroups}
        colorMode={map.colorMode}
        onColorModeChange={map.setColorMode}
        initialSelectedNode={map.initialNode}
        onViewModeChange={map.handleViewModeChange}
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
            population={map.population}
            onClose={p.onClose}
            onEnterCommunity={p.onEnterCommunity}
            onNeighborSelect={p.onNeighborSelect}
          />
        )}
      />
    </div>
  );
}

/** The load error when a drawn slice failed, otherwise the usual notice. */
function MapBanner({ error, children }: { error: string | null; children: ReactNode }) {
  if (error) return <ApiError title="Could not load the architecture map." message={error} />;
  return <>{children}</>;
}

/**
 * The capped-payload notice. Only the unfiltered file scope and the outline
 * draw the capped file graph, so only they carry it.
 */
function MapTruncation({ map, graph }: { map: MapState; graph: ArchitectureData["fullGraph"] }) {
  const usesFullGraph =
    map.tree || (map.scope === "files" && !map.signal && map.activeCommunity === null);
  const payload = graph.data;
  if (!usesFullGraph || !payload?.truncated || payload.total_node_count == null) return null;
  return (
    <GraphTruncationBanner
      shown={payload.nodes.length}
      total={payload.total_node_count}
      limit={map.graphLimit ?? payload.nodes.length}
      onLoadMore={map.setGraphLimit}
      {...(map.tree ? {} : { onSwitchToArchitecture: () => map.handleScopeChange("communities") })}
    />
  );
}

/** The narrowing select (file scope only) and the scope switcher. */
function HeaderControls({
  map,
  narrowingCommunities,
}: {
  map: MapState;
  narrowingCommunities: CommunitySummaryItem[] | undefined;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {map.scope === "files" && (
        <GraphNarrowingSelect
          modules={map.moduleGroups}
          communities={narrowingCommunities}
          activeModule={map.activeModule}
          activeCommunity={map.activeCommunity}
          onChange={map.handleNarrowingChange}
        />
      )}
      <GraphScopeSwitcher scope={map.scope} onScopeChange={map.handleScopeChange} />
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
