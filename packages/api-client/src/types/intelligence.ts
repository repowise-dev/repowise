// ---------------------------------------------------------------------------
// Graph Intelligence
// ---------------------------------------------------------------------------

export interface SymbolNodeSummary {
  symbol_id: string;
  name: string;
  kind: string;
  file: string;
  start_line?: number | null;
  signature?: string | null;
}

export interface CallerCalleeEntry {
  symbol_id: string;
  name: string;
  kind: string;
  file: string;
  start_line?: number | null;
  edge_type: string;
  confidence: number;
}

export interface CallersCalleesResponse {
  symbol_id: string;
  symbol: SymbolNodeSummary;
  callers: CallerCalleeEntry[];
  callees: CallerCalleeEntry[];
  caller_count: number;
  callee_count: number;
  truncated: boolean;
}

export interface CommunityMember {
  path: string;
  pagerank: number;
  is_entry_point: boolean;
}

export interface NeighboringCommunity {
  community_id: number;
  label: string;
  cross_edge_count: number;
}

export interface CommunityDetailResponse {
  community_id: number;
  label: string;
  cohesion: number;
  member_count: number;
  members: CommunityMember[];
  truncated: boolean;
  neighboring_communities: NeighboringCommunity[];
}

export interface CommunitySummaryItem {
  community_id: number;
  label: string;
  cohesion: number;
  member_count: number;
  top_file: string;
}

export interface GraphMetricsResponse {
  target: string;
  node_type: string;
  pagerank: number;
  pagerank_percentile: number;
  betweenness: number;
  betweenness_percentile: number;
  community_id: number;
  community_label: string | null;
  is_entry_point: boolean;
  in_degree: number;
  out_degree: number;
  entry_point_score?: number | null;
  kind?: string | null;
  file?: string | null;
}

export interface ExecutionFlowEntry {
  entry_point: string;
  entry_point_name: string;
  entry_point_score: number;
  trace: string[];
  depth: number;
  crosses_community: boolean;
  communities_visited: number[];
}

export interface ExecutionFlowsResponse {
  total_entry_points: number;
  flows: ExecutionFlowEntry[];
}
