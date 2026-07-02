/**
 * Knowledge Graph webview: mounts the shared radial-constellation GraphFlow
 * shell against host RPC data. The shell keeps its own toolbar/legend/controls;
 * this view adds only a slim header and wires the shell's structural callbacks
 * back into the lazy data hook so scope changes fetch just what they render.
 */

import { useCallback, useEffect, useState } from "react";
import {
  GraphFlow,
  type GraphFlowProps as GraphFlowShellProps,
} from "@repowise-dev/ui/graph/graph-flow";
import { PathFinderPanel } from "@repowise-dev/ui/graph/path-finder-panel";
import { GraphCommunityPanel } from "@repowise-dev/ui/graph/graph-community-panel";
import type {
  ArchitectureGraph,
  CommunityDetail,
  CommunitySlice,
  CommunitySummaryItem,
  ExecutionFlows,
  GraphExport,
  GraphPath,
  ModuleGraph,
  NodeSearchResult,
} from "@repowise-dev/types/graph";
import type { WebviewHost } from "../../runtime/rpc";
import type { ViewProps } from "../../runtime/mount";
import { useGraphData } from "./use-graph-data";
import { GraphHeader } from "./graph-header";

/** Hub (`__community__N`) and repo-core (`__repo_core__`) ids are synthetic and
 *  not backed by a file; everything else the shell hands us is a repo path. */
function isFileBackedNodeId(nodeId: string): boolean {
  return !nodeId.startsWith("__");
}

export function App({ host, params, repo, refreshToken }: ViewProps<"graph">) {
  const data = useGraphData(host, refreshToken);

  const openIfFile = useCallback(
    (nodeId: string) => {
      if (isFileBackedNodeId(nodeId)) host.openFile(nodeId);
    },
    [host],
  );

  const handleNodeClick = useCallback<NonNullable<GraphFlowShellProps["onNodeClick"]>>(
    (nodeId, nodeType) => {
      if (nodeType === "file") host.openFile(nodeId);
    },
    [host],
  );

  return (
    <div className="flex h-screen flex-col">
      <GraphHeader repoName={repo.name} stats={data.stats} />
      <div className="relative min-h-0 flex-1">
        {data.error ? (
          <div className="flex h-full items-center justify-center p-6">
            <div className="max-w-md rounded-lg border border-[var(--color-error)] bg-[var(--color-bg-elevated)] p-4 text-sm">
              <p className="font-medium text-[var(--color-error)]">
                Could not load the knowledge graph.
              </p>
              <p className="mt-2 text-[var(--color-text-secondary)]">{data.error}</p>
            </div>
          </div>
        ) : (
          <GraphFlow
            moduleGraph={data.moduleGraph as ModuleGraph | undefined}
            isLoadingModuleGraph={data.isLoadingModuleGraph}
            fullGraph={data.fullGraph as GraphExport | undefined}
            isLoadingFullGraph={data.isLoadingFullGraph}
            // Legacy file-level architecture graph is unused by the constellation
            // scope; the ui prop is still required, so pass an empty pair.
            architectureGraph={undefined}
            isLoadingArchitectureGraph={false}
            constellationGraph={data.constellationGraph as ArchitectureGraph | undefined}
            isLoadingConstellationGraph={data.isLoadingConstellationGraph}
            constellationSlices={
              data.constellationSlices as Map<number, CommunitySlice>
            }
            onExpandedHubsChange={data.setExpandedHubs}
            repoName={repo.name}
            deadCodeGraph={data.deadCodeGraph as GraphExport | undefined}
            isLoadingDeadCodeGraph={data.isLoadingDeadCodeGraph}
            hotFilesGraph={data.hotFilesGraph as GraphExport | undefined}
            isLoadingHotFilesGraph={data.isLoadingHotFilesGraph}
            communities={data.communities as CommunitySummaryItem[] | undefined}
            executionFlows={data.executionFlows as ExecutionFlows | undefined}
            initialSelectedNode={params.selectNode ?? null}
            onViewModeChange={data.setViewMode}
            onModulePathChange={data.setModulePath}
            onExpandedModulesChange={(expanded) =>
              data.setHasExpandedModules(expanded.size > 0)
            }
            onNodeClick={handleNodeClick}
            onNodeViewDocs={openIfFile}
            renderPathFinder={(p) => (
              <PathFinderPanel
                searchNodes={(q, l) =>
                  host.api.searchNodes(q, l) as Promise<NodeSearchResult[]>
                }
                findPath={(from, to) =>
                  host.api.graphPath(from, to) as Promise<GraphPath>
                }
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
                communityId={p.communityId}
                onClose={p.onClose}
                onExpandOnCanvas={p.onExpandOnCanvas}
              />
            )}
          />
        )}
      </div>
    </div>
  );
}

/**
 * Fetches one community's detail on open and feeds the shared panel shell.
 * The shell expects pre-fetched data + a loading flag (it does no fetching of
 * its own), so this thin wrapper owns the host call, mirroring the web app's
 * SWR wrapper. Member links open the file in an editor column.
 */
function CommunityPanel({
  host,
  communityId,
  onClose,
  onExpandOnCanvas,
}: {
  host: WebviewHost;
  communityId: number;
  onClose: () => void;
  onExpandOnCanvas: () => void;
}) {
  const [community, setCommunity] = useState<CommunityDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setCommunity(null);
    host.api
      .communityDetail(communityId)
      .then((detail) => {
        if (!cancelled) setCommunity(detail as CommunityDetail);
      })
      .catch(() => {
        if (!cancelled) setCommunity(null);
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [host, communityId]);

  return (
    <GraphCommunityPanel
      communityId={communityId}
      community={community}
      isLoading={isLoading}
      onClose={onClose}
      onExpandOnCanvas={onExpandOnCanvas}
    />
  );
}
