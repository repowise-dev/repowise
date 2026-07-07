import { apiGet } from "./client";
import type {
  ArchitectureGraphResponse,
  CallersCalleesResponse,
  CommunityDetailResponse,
  CommunitySliceResponse,
  CommunitySummaryItem,
  DeadCodeGraphResponse,
  EgoGraphResponse,
  ExecutionFlowsResponse,
  GraphExportResponse,
  GraphMetricsResponse,
  GraphPathResponse,
  HotFilesGraphResponse,
  ModuleGraphResponse,
  NodeSearchResult,
} from "./types";

export async function getGraph(
  repoId: string,
  limit?: number,
): Promise<GraphExportResponse> {
  return apiGet<GraphExportResponse>(
    `/api/graph/${repoId}`,
    limit != null ? { limit } : undefined,
  );
}

export async function getGraphPath(
  repoId: string,
  from: string,
  to: string,
): Promise<GraphPathResponse> {
  return apiGet<GraphPathResponse>(`/api/graph/${repoId}/path`, { from, to });
}

export async function getModuleGraph(repoId: string): Promise<ModuleGraphResponse> {
  return apiGet<ModuleGraphResponse>(`/api/graph/${repoId}/modules`);
}

export async function getEgoGraph(
  repoId: string,
  nodeId: string,
  hops = 2,
): Promise<EgoGraphResponse> {
  return apiGet<EgoGraphResponse>(`/api/graph/${repoId}/ego`, { node_id: nodeId, hops });
}

export async function searchNodes(
  repoId: string,
  q: string,
  limit = 10,
): Promise<NodeSearchResult[]> {
  return apiGet<NodeSearchResult[]>(`/api/graph/${repoId}/nodes/search`, { q, limit });
}

export async function getArchitectureGraph(repoId: string): Promise<GraphExportResponse> {
  return apiGet<GraphExportResponse>(`/api/graph/${repoId}/entry-points`);
}

/**
 * Community super-node view: one node per detected community, with
 * cross-community edges. Endpoint introduced in Phase A of the graph
 * UX overhaul; rendering surface is incremental and the legacy
 * entry-point view above remains the default until adapter support lands.
 */
export async function getArchitectureCommunityGraph(
  repoId: string,
  minMembers = 2,
): Promise<ArchitectureGraphResponse> {
  return apiGet<ArchitectureGraphResponse>(`/api/graph/${repoId}/architecture`, {
    min_members: minMembers,
  });
}

/**
 * Constellation (radial Knowledge Graph) data source — the community
 * super-graph. Thin alias over {@link getArchitectureCommunityGraph} so the
 * G3 wiring reads cleanly.
 */
export async function getArchitecture(
  repoId: string,
  minMembers = 2,
): Promise<ArchitectureGraphResponse> {
  return getArchitectureCommunityGraph(repoId, minMembers);
}

export async function getDeadCodeGraph(repoId: string): Promise<DeadCodeGraphResponse> {
  return apiGet<DeadCodeGraphResponse>(`/api/graph/${repoId}/dead-nodes`);
}

export async function getHotFilesGraph(
  repoId: string,
  days = 30,
  limit = 25,
): Promise<HotFilesGraphResponse> {
  return apiGet<HotFilesGraphResponse>(`/api/graph/${repoId}/hot-files`, { days, limit });
}

// ---------------------------------------------------------------------------
// Graph Intelligence
// ---------------------------------------------------------------------------

export async function getCommunities(repoId: string): Promise<CommunitySummaryItem[]> {
  return apiGet<CommunitySummaryItem[]>(`/api/graph/${repoId}/communities`);
}

export async function getCommunityDetail(
  repoId: string,
  communityId: number,
  params?: { include_members?: boolean; member_limit?: number },
): Promise<CommunityDetailResponse> {
  return apiGet<CommunityDetailResponse>(
    `/api/graph/${repoId}/communities/${communityId}`,
    params,
  );
}

/**
 * Constellation blossom slice: a single community's member file nodes, the
 * edges among them, and one-hop boundary stubs (neighbor nodes flagged
 * `is_boundary`) so cross-cluster edges render. Fetched on hub expand.
 */
export async function getCommunitySlice(
  repoId: string,
  communityId: number,
): Promise<CommunitySliceResponse> {
  return apiGet<CommunitySliceResponse>(
    `/api/graph/${repoId}/communities/${communityId}/slice`,
  );
}

export async function getGraphMetrics(
  repoId: string,
  nodeId: string,
): Promise<GraphMetricsResponse> {
  return apiGet<GraphMetricsResponse>(`/api/graph/${repoId}/metrics`, { node_id: nodeId });
}

export async function getCallersCallees(
  repoId: string,
  symbolId: string,
  params?: { direction?: string; edge_types?: string; limit?: number },
): Promise<CallersCalleesResponse> {
  return apiGet<CallersCalleesResponse>(`/api/graph/${repoId}/callers-callees`, {
    symbol_id: symbolId,
    ...params,
  });
}

export async function getExecutionFlows(
  repoId: string,
  params?: { top_n?: number; max_depth?: number; entry_point?: string },
): Promise<ExecutionFlowsResponse> {
  return apiGet<ExecutionFlowsResponse>(`/api/graph/${repoId}/execution-flows`, params);
}
