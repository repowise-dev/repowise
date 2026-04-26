"use client";

import useSWR from "swr";
import {
  getArchitectureGraph,
  getCallersCallees,
  getCommunities,
  getCommunityDetail,
  getDeadCodeGraph,
  getEgoGraph,
  getExecutionFlows,
  getGraph,
  getGraphMetrics,
  getHotFilesGraph,
  getModuleGraph,
} from "@/lib/api/graph";
import type {
  CallersCalleesResponse,
  CommunityDetailResponse,
  CommunitySummaryItem,
  DeadCodeGraphResponse,
  EgoGraphResponse,
  ExecutionFlowsResponse,
  GraphExportResponse,
  GraphMetricsResponse,
  HotFilesGraphResponse,
  ModuleGraphResponse,
} from "@/lib/api/types";

const SWR_OPTS = { revalidateOnFocus: false, revalidateOnReconnect: false };

export function useGraph(repoId: string | null) {
  const { data, error, isLoading } = useSWR<GraphExportResponse>(
    repoId ? `graph:${repoId}` : null,
    () => getGraph(repoId!),
    SWR_OPTS,
  );
  return { graph: data, error, isLoading };
}

export function useModuleGraph(repoId: string | null) {
  const { data, error, isLoading } = useSWR<ModuleGraphResponse>(
    repoId ? `module-graph:${repoId}` : null,
    () => getModuleGraph(repoId!),
    SWR_OPTS,
  );
  return { graph: data, error, isLoading };
}

export function useEgoGraph(repoId: string | null, nodeId: string | null, hops = 2) {
  const { data, error, isLoading } = useSWR<EgoGraphResponse>(
    repoId && nodeId ? `ego-graph:${repoId}:${nodeId}:${hops}` : null,
    () => getEgoGraph(repoId!, nodeId!, hops),
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

export function useCommunities(repoId: string | null) {
  const { data, error, isLoading } = useSWR<CommunitySummaryItem[]>(
    repoId ? `communities:${repoId}` : null,
    () => getCommunities(repoId!),
    SWR_OPTS,
  );
  return { communities: data, error, isLoading };
}

export function useCommunityDetail(repoId: string | null, communityId: number | null) {
  const { data, error, isLoading } = useSWR<CommunityDetailResponse>(
    repoId && communityId !== null ? `community:${repoId}:${communityId}` : null,
    () => getCommunityDetail(repoId!, communityId!),
    SWR_OPTS,
  );
  return { community: data, error, isLoading };
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
