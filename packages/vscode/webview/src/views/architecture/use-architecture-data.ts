/**
 * Data for the Architecture webview: the same graph slices the web
 * Architecture page's Map tab fetches through its SWR hooks
 * (packages/web components/graph/graph-flow.tsx and
 * components/architecture/graph-view.tsx), served here by host RPCs.
 *
 * Each slice is keyed the way the web keys it (scope, population, community,
 * node cap). A null key means the active scope does not draw that slice, so
 * nothing is requested; a fresh mount on the default Files scope touches only
 * the capped file graph and the community labels. The host caches every
 * response per indexed commit, so revisiting a key costs one message, not a
 * server round trip.
 */

import { useEffect, useRef, useState } from "react";
import type {
  ArchitectureGraph,
  CommunitySlice,
  CommunitySummaryItem,
  ExecutionFlows,
  GraphExport,
  GraphPopulation,
} from "@repowise-dev/types/graph";
import type { GraphExportResponse } from "@repowise-dev/api-client/types";
import type { WebviewHost } from "../../runtime/rpc";

/** The canvas's internal scope, as the shared shell names it. */
export type ViewMode = "full" | "architecture" | "dead" | "hotfiles" | "unified";

export interface QueryState<T> {
  data: T | undefined;
  isLoading: boolean;
  error: string | null;
}

/**
 * One host call per distinct `key`, refetched when the index moves.
 * `fetcher` is read through a ref so a fresh closure each render does not
 * refire the request; the key alone decides when to fetch.
 */
export function useHostQuery<T>(
  key: string | null,
  fetcher: () => Promise<T>,
  refreshToken: number,
): QueryState<T> {
  const [state, setState] = useState<{ key: string | null } & QueryState<T>>({
    key: null,
    data: undefined,
    isLoading: false,
    error: null,
  });
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    if (key === null) return;
    let cancelled = false;
    // A refetch of the same key (the index moved) keeps the last payload on
    // screen until the fresh one lands, so a canvas is not torn down for it.
    setState((prev) => ({
      key,
      data: prev.key === key ? prev.data : undefined,
      isLoading: true,
      error: null,
    }));
    fetcherRef.current().then(
      (data) => {
        if (!cancelled) setState({ key, data, isLoading: false, error: null });
      },
      (err: unknown) => {
        if (!cancelled) {
          const error = err instanceof Error ? err.message : String(err);
          setState({ key, data: undefined, isLoading: false, error });
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [key, refreshToken]);

  // A disabled or not-yet-started key reads as empty, never as the last
  // key's payload.
  if (key === null || state.key !== key) {
    return { data: undefined, isLoading: key !== null, error: null };
  }
  return { data: state.data, isLoading: state.isLoading, error: state.error };
}

/** The population flags as a key fragment, as the web hooks spell it. */
function populationKey(p: GraphPopulation | undefined): string {
  return p ? `${+p.tests}${+p.examples}${+p.docs}` : "000";
}

export interface ArchitectureDataInput {
  viewMode: ViewMode;
  /** The tree scope reads the same capped file graph the Files canvas draws. */
  tree: boolean;
  population: GraphPopulation;
  /** The population the narrowing list counts: undefined on the whole-repo
   *  file graph, which is not population-filtered. */
  narrowingPopulation: GraphPopulation | undefined;
  activeCommunity: number | null;
  graphLimit: number | undefined;
  /** Latched once the flows panel opens; traces are never fetched unasked. */
  flowsRequested: boolean;
}

export interface ArchitectureData {
  fullGraph: QueryState<GraphExportResponse>;
  constellationGraph: QueryState<ArchitectureGraph>;
  deadCodeGraph: QueryState<GraphExport>;
  hotFilesGraph: QueryState<GraphExport>;
  communitySlice: QueryState<CommunitySlice>;
  communities: QueryState<CommunitySummaryItem[]>;
  narrowingCommunities: QueryState<CommunitySummaryItem[]>;
  executionFlows: QueryState<ExecutionFlows>;
  /** First error among the slices the active scope draws, already presentable. */
  error: string | null;
}

export function useArchitectureData(
  host: WebviewHost,
  refreshToken: number,
  input: ArchitectureDataInput,
): ArchitectureData {
  const { viewMode, tree, population, narrowingPopulation, activeCommunity, graphLimit } = input;
  const pop = populationKey(population);

  const needsFullGraph = tree || viewMode === "full" || viewMode === "unified";
  const fullGraph = useHostQuery(
    needsFullGraph ? `full:${graphLimit ?? "default"}` : null,
    () => host.api.fullGraph(graphLimit),
    refreshToken,
  );
  const constellationGraph = useHostQuery(
    !tree && viewMode === "architecture" ? `constellation:${pop}` : null,
    () => host.api.architectureCommunityGraph(population) as Promise<ArchitectureGraph>,
    refreshToken,
  );
  const deadCodeGraph = useHostQuery(
    !tree && viewMode === "dead" ? "dead" : null,
    () => host.api.deadCodeGraph() as Promise<GraphExport>,
    refreshToken,
  );
  const hotFilesGraph = useHostQuery(
    !tree && viewMode === "hotfiles" ? "hot" : null,
    () => host.api.hotFilesGraph() as Promise<GraphExport>,
    refreshToken,
  );
  const communitySlice = useHostQuery(
    !tree && viewMode !== "architecture" && activeCommunity != null
      ? `slice:${activeCommunity}:${pop}`
      : null,
    () => host.api.communitySlice(activeCommunity!, population) as Promise<CommunitySlice>,
    refreshToken,
  );
  const communities = useHostQuery(
    tree ? null : `communities:${pop}`,
    () => host.api.communities(population),
    refreshToken,
  );
  const narrowingCommunities = useHostQuery(
    tree ? null : `communities:${populationKey(narrowingPopulation)}`,
    () => host.api.communities(narrowingPopulation),
    refreshToken,
  );
  const executionFlows = useHostQuery(
    !tree && input.flowsRequested && viewMode !== "architecture" ? "flows" : null,
    () => host.api.executionFlows() as Promise<ExecutionFlows>,
    refreshToken,
  );

  const error =
    fullGraph.error ??
    constellationGraph.error ??
    deadCodeGraph.error ??
    hotFilesGraph.error ??
    communitySlice.error ??
    null;

  return {
    fullGraph,
    constellationGraph,
    deadCodeGraph,
    hotFilesGraph,
    communitySlice,
    communities,
    narrowingCommunities,
    executionFlows,
    error,
  };
}
