"use client";

import type { ZoomMap } from "@repowise-dev/ui/zoom";
import useSWR from "swr";
import {
  getArchitecture,
  getArchitectureGraph,
  getCallersCallees,
  getCommunities,
  getCommunityDetail,
  getCommunitySlice,
  getDeadCodeGraph,
  getExecutionFlows,
  getGraph,
  getGraphMetrics,
  getHotFilesGraph,
  getZoomMap,
} from "@/lib/api/graph";
import type { GraphPopulation } from "@repowise-dev/types/graph";
import type {
  ArchitectureGraphResponse,
  CallersCalleesResponse,
  CommunityDetailResponse,
  CommunitySliceResponse,
  CommunitySummaryItem,
  DeadCodeGraphResponse,
  ExecutionFlowsResponse,
  GraphExportResponse,
  GraphMetricsResponse,
  HotFilesGraphResponse,
} from "@/lib/api/types";

const SWR_OPTS = { revalidateOnFocus: false, revalidateOnReconnect: false };

/** The population flags as a key fragment and as query params. */
function populationKey(p?: GraphPopulation): string {
  return p ? `${+p.tests}${+p.examples}${+p.docs}` : "000";
}
function populationParams(p?: GraphPopulation) {
  return p
    ? { include_tests: p.tests, include_examples: p.examples, include_docs: p.docs }
    : undefined;
}

export function useZoomMap(
  repoId: string | null,
  params?: { max_depth?: number; focus?: string },
) {
  const key = repoId
    ? `zoom-map:${repoId}:${params?.max_depth ?? ""}:${params?.focus ?? ""}`
    : null;
  const { data, error, isLoading } = useSWR<ZoomMap>(
    key,
    () => getZoomMap(repoId!, params),
    SWR_OPTS,
  );
  return { zoomMap: data, error, isLoading };
}

export function useGraph(repoId: string | null, limit?: number) {
  const { data, error, isLoading } = useSWR<GraphExportResponse>(
    repoId ? `graph:${repoId}:${limit ?? "default"}` : null,
    () => getGraph(repoId!, limit),
    SWR_OPTS,
  );
  return { graph: data, error, isLoading };
}

/**
 * Community super-graph for the constellation (radial Knowledge Graph) scope.
 * Conditional SWR: only fetched when a repoId is given (the wrapper gates it
 * to the constellation scope).
 */
export function useArchitectureCommunityGraph(
  repoId: string | null,
  population?: GraphPopulation,
) {
  const { data, error, isLoading } = useSWR<ArchitectureGraphResponse>(
    repoId ? `arch-community:${repoId}:${populationKey(population)}` : null,
    () => getArchitecture(repoId!, 2, populationParams(population)),
    SWR_OPTS,
  );
  return { graph: data, error, isLoading };
}

export function useArchitectureGraph(repoId: string | null) {
  const { data, error, isLoading } = useSWR<GraphExportResponse>(
    repoId ? `arch-graph:${repoId}` : null,
    () => getArchitectureGraph(repoId!),
    SWR_OPTS,
  );
  return { graph: data, error, isLoading };
}

export function useDeadCodeGraph(repoId: string | null) {
  const { data, error, isLoading } = useSWR<DeadCodeGraphResponse>(
    repoId ? `dead-graph:${repoId}` : null,
    () => getDeadCodeGraph(repoId!),
    SWR_OPTS,
  );
  return { graph: data, error, isLoading };
}

export function useHotFilesGraph(repoId: string | null, days = 30, limit = 25) {
  const { data, error, isLoading } = useSWR<HotFilesGraphResponse>(
    repoId ? `hot-graph:${repoId}:${days}:${limit}` : null,
    () => getHotFilesGraph(repoId!, days, limit),
    SWR_OPTS,
  );
  return { graph: data, error, isLoading };
}

// ---------------------------------------------------------------------------
// Graph Intelligence
// ---------------------------------------------------------------------------

export function useCommunities(repoId: string | null, population?: GraphPopulation) {
  const { data, error, isLoading } = useSWR<CommunitySummaryItem[]>(
    repoId ? `communities:${repoId}:${populationKey(population)}` : null,
    () => getCommunities(repoId!, populationParams(population)),
    SWR_OPTS,
  );
  return { communities: data, error, isLoading };
}

export function useCommunityDetail(
  repoId: string | null,
  communityId: number | null,
  population?: GraphPopulation,
) {
  const { data, error, isLoading } = useSWR<CommunityDetailResponse>(
    repoId && communityId !== null
      ? `community:${repoId}:${communityId}:${populationKey(population)}`
      : null,
    () => getCommunityDetail(repoId!, communityId!, populationParams(population)),
    SWR_OPTS,
  );
  return { community: data, error, isLoading };
}

/**
 * The drill-down payload: one community's members plus the one-hop stubs that
 * bound them. Conditional — no request until somebody enters a community.
 */
export function useCommunitySlice(
  repoId: string | null,
  communityId: number | null,
  population?: GraphPopulation,
) {
  const { data, error, isLoading } = useSWR<CommunitySliceResponse>(
    repoId && communityId != null
      ? `community-slice:${repoId}:${communityId}:${populationKey(population)}`
      : null,
    () => getCommunitySlice(repoId!, communityId!, populationParams(population)),
    SWR_OPTS,
  );
  return { slice: data, error, isLoading };
}

export function useGraphMetrics(repoId: string | null, nodeId: string | null) {
  const { data, error, isLoading } = useSWR<GraphMetricsResponse>(
    repoId && nodeId ? `metrics:${repoId}:${nodeId}` : null,
    () => getGraphMetrics(repoId!, nodeId!),
    SWR_OPTS,
  );
  return { metrics: data, error, isLoading };
}

export function useCallersCallees(
  repoId: string | null,
  symbolId: string | null,
  params?: { direction?: string; edge_types?: string; limit?: number },
) {
  const key = repoId && symbolId
    ? `callers:${repoId}:${symbolId}:${params?.edge_types ?? "calls"}`
    : null;
  const { data, error, isLoading } = useSWR<CallersCalleesResponse>(
    key,
    () => getCallersCallees(repoId!, symbolId!, params),
    SWR_OPTS,
  );
  return { data, error, isLoading };
}

export function useExecutionFlows(
  repoId: string | null,
  params?: { top_n?: number; max_depth?: number },
) {
  const { data, error, isLoading } = useSWR<ExecutionFlowsResponse>(
    repoId ? `flows:${repoId}:${params?.top_n ?? 5}` : null,
    () => getExecutionFlows(repoId!, params),
    SWR_OPTS,
  );
  return { flows: data, error, isLoading };
}
