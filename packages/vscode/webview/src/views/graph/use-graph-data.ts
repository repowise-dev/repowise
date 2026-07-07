/**
 * Data orchestration for the Knowledge Graph webview.
 *
 * The web page fetches every graph slice through SWR hooks; a webview never
 * fetches, so this reimplements that orchestration against the typed host RPC
 * with plain React state. It is deliberately LAZY: this panel runs on a
 * 2,000+ node index, so each slice is requested only when the view mode that
 * renders it becomes active, and each slice is fetched at most once (a ref
 * guard survives StrictMode's double-invoke). The default mode is the radial
 * community constellation, so a fresh mount touches only the community
 * super-graph and the community summaries.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { WebviewHost } from "../../runtime/rpc";
import type {
  ArchitectureGraph,
  CommunitySlice,
  CommunitySummaryItem,
  ExecutionFlows,
  GraphExport,
  ModuleGraph,
} from "@repowise-dev/types/graph";

export type ViewMode =
  | "module"
  | "full"
  | "architecture"
  | "dead"
  | "hotfiles"
  | "unified";

/** Default scope: the radial community constellation (matches the ui shell). */
const DEFAULT_VIEW_MODE: ViewMode = "architecture";

export interface GraphData {
  moduleGraph: ModuleGraph | undefined;
  isLoadingModuleGraph: boolean;
  fullGraph: GraphExport | undefined;
  isLoadingFullGraph: boolean;
  constellationGraph: ArchitectureGraph | undefined;
  isLoadingConstellationGraph: boolean;
  constellationSlices: Map<number, CommunitySlice>;
  deadCodeGraph: GraphExport | undefined;
  isLoadingDeadCodeGraph: boolean;
  hotFilesGraph: GraphExport | undefined;
  isLoadingHotFilesGraph: boolean;
  communities: CommunitySummaryItem[] | undefined;
  executionFlows: ExecutionFlows | undefined;
  /** First error hit while loading the active scope, already user-presentable. */
  error: string | null;
  /** Node/edge counts of the graph the active scope renders (header chrome). */
  stats: { nodes: number; edges: number } | null;
  viewMode: ViewMode;
  setViewMode: (mode: ViewMode) => void;
  /** Legacy breadcrumb drill-down state, owned by the ui shell. */
  setModulePath: (path: string[]) => void;
  setHasExpandedModules: (expanded: boolean) => void;
  /** Currently-expanded constellation hubs; drives the incremental slice fetch. */
  setExpandedHubs: (ids: number[]) => void;
}

/**
 * A single lazily-loaded slice: fetched once when {@link enabled} first turns
 * true, and re-fetched whenever {@link resetToken} changes (index moved).
 */
function useHostSlice<T>(
  enabled: boolean,
  resetToken: number,
  fetcher: () => Promise<T>,
  onError: (message: string) => void,
): { data: T | undefined; isLoading: boolean } {
  const [data, setData] = useState<T | undefined>(undefined);
  const [isLoading, setIsLoading] = useState(false);
  const requested = useRef(false);

  // The index moved under the panel: drop the cache so the gate below refetches.
  // Declared before the fetch effect so it resets `requested` first on a reset.
  useEffect(() => {
    requested.current = false;
    setData(undefined);
    setIsLoading(false);
  }, [resetToken]);

  useEffect(() => {
    if (!enabled || requested.current) return;
    requested.current = true;
    setIsLoading(true);
    let cancelled = false;
    void fetcher()
      .then((value) => {
        if (!cancelled) setData(value);
      })
      .catch((err: unknown) => {
        if (!cancelled) onError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [enabled, resetToken, fetcher, onError]);

  return { data, isLoading };
}

export function useGraphData(host: WebviewHost, refreshToken: number): GraphData {
  const [viewMode, setViewMode] = useState<ViewMode>(DEFAULT_VIEW_MODE);
  const [modulePath, setModulePath] = useState<string[]>([]);
  const [hasExpandedModules, setHasExpandedModules] = useState(false);
  const [expandedHubs, setExpandedHubs] = useState<number[]>([]);
  const [error, setError] = useState<string | null>(null);

  const onError = useCallback((message: string) => setError(message), []);

  // Clear the surfaced error whenever the scope changes or the index moves, so
  // a stale message never lingers over a healthy view.
  useEffect(() => {
    setError(null);
  }, [viewMode, refreshToken]);

  const isDrilledDown = modulePath.length > 0;
  // The full file graph backs the drill-down, the flat file/unified scopes, and
  // module expansion; execution flows only trace a file-level path, so they
  // ride the same gate rather than fetching on the constellation mount.
  const needsFullGraph =
    isDrilledDown ||
    viewMode === "full" ||
    viewMode === "unified" ||
    hasExpandedModules;

  // Stable fetchers (host is created once at mount) so the slice effects don't
  // re-run on every render.
  const fetchModuleGraph = useCallback(() => host.api.moduleGraph(), [host]);
  const fetchFullGraph = useCallback(() => host.api.fullGraph(), [host]);
  const fetchConstellation = useCallback(
    () => host.api.architectureCommunityGraph(),
    [host],
  );
  const fetchCommunities = useCallback(() => host.api.communities(), [host]);
  const fetchDeadCode = useCallback(() => host.api.deadCodeGraph(), [host]);
  const fetchHotFiles = useCallback(() => host.api.hotFilesGraph(), [host]);
  const fetchExecutionFlows = useCallback(() => host.api.executionFlows(), [host]);

  const moduleSlice = useHostSlice(
    viewMode === "module",
    refreshToken,
    fetchModuleGraph,
    onError,
  );
  const fullSlice = useHostSlice(needsFullGraph, refreshToken, fetchFullGraph, onError);
  const constellationSlice = useHostSlice(
    viewMode === "architecture",
    refreshToken,
    fetchConstellation,
    onError,
  );
  // Community labels feed the legend and inspection panel across every scope;
  // the constellation (default) mode needs them immediately.
  const communitiesSlice = useHostSlice(true, refreshToken, fetchCommunities, onError);
  const deadSlice = useHostSlice(
    viewMode === "dead",
    refreshToken,
    fetchDeadCode,
    onError,
  );
  const hotSlice = useHostSlice(
    viewMode === "hotfiles",
    refreshToken,
    fetchHotFiles,
    onError,
  );
  const flowsSlice = useHostSlice(
    needsFullGraph,
    refreshToken,
    fetchExecutionFlows,
    onError,
  );

  // Constellation blossom: fetch each newly-expanded hub's member slice once and
  // merge it into the Map incrementally (already-loaded ids are never refetched).
  const [constellationSlices, setConstellationSlices] = useState<
    Map<number, CommunitySlice>
  >(() => new Map());
  const requestedHubs = useRef<Set<number>>(new Set());

  useEffect(() => {
    for (const id of expandedHubs) {
      if (requestedHubs.current.has(id)) continue;
      requestedHubs.current.add(id);
      void host.api
        .communitySlice(id)
        .then((slice) => {
          setConstellationSlices((prev) => {
            const next = new Map(prev);
            next.set(id, slice as CommunitySlice);
            return next;
          });
        })
        .catch((err: unknown) =>
          onError(err instanceof Error ? err.message : String(err)),
        );
    }
  }, [expandedHubs, host, onError]);

  // Index moved: drop the accumulated slices so expanded hubs refetch fresh.
  useEffect(() => {
    requestedHubs.current = new Set();
    setConstellationSlices(new Map());
  }, [refreshToken]);

  const moduleGraph = moduleSlice.data as ModuleGraph | undefined;
  const fullGraph = fullSlice.data as GraphExport | undefined;
  const constellationGraph = constellationSlice.data as
    | ArchitectureGraph
    | undefined;
  const deadCodeGraph = deadSlice.data as GraphExport | undefined;
  const hotFilesGraph = hotSlice.data as GraphExport | undefined;
  const communities = communitiesSlice.data as CommunitySummaryItem[] | undefined;
  const executionFlows = flowsSlice.data as ExecutionFlows | undefined;

  const stats = useMemo<GraphData["stats"]>(() => {
    switch (viewMode) {
      case "architecture":
        return constellationGraph
          ? {
              nodes: constellationGraph.nodes.length,
              edges: constellationGraph.edges.length,
            }
          : null;
      case "module":
        return moduleGraph
          ? { nodes: moduleGraph.nodes.length, edges: moduleGraph.edges.length }
          : null;
      case "dead":
        return deadCodeGraph
          ? { nodes: deadCodeGraph.nodes.length, edges: deadCodeGraph.links.length }
          : null;
      case "hotfiles":
        return hotFilesGraph
          ? { nodes: hotFilesGraph.nodes.length, edges: hotFilesGraph.links.length }
          : null;
      default:
        return fullGraph
          ? { nodes: fullGraph.nodes.length, edges: fullGraph.links.length }
          : null;
    }
  }, [viewMode, constellationGraph, moduleGraph, deadCodeGraph, hotFilesGraph, fullGraph]);

  return {
    moduleGraph,
    isLoadingModuleGraph: moduleSlice.isLoading,
    fullGraph,
    isLoadingFullGraph: fullSlice.isLoading,
    constellationGraph,
    isLoadingConstellationGraph: constellationSlice.isLoading,
    constellationSlices,
    deadCodeGraph,
    isLoadingDeadCodeGraph: deadSlice.isLoading,
    hotFilesGraph,
    isLoadingHotFilesGraph: hotSlice.isLoading,
    communities,
    executionFlows,
    error,
    stats,
    viewMode,
    setViewMode,
    setModulePath,
    setHasExpandedModules,
    setExpandedHubs,
  };
}
