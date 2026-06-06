import type { GitMetadataResponse } from "./git";

// ---------------------------------------------------------------------------
// Graph
// ---------------------------------------------------------------------------

export interface GraphNodeResponse {
  node_id: string;
  node_type: string;
  language: string;
  symbol_count: number;
  pagerank: number;
  betweenness: number;
  community_id: number;
  is_test: boolean;
  is_entry_point: boolean;
  has_doc: boolean;
  // Phase A: cross-link signals (all optional for back-compat)
  is_hotspot?: boolean;
  churn_percentile?: number | null;
  is_dead?: boolean;
  dead_confidence?: number | null;
  has_decision?: boolean;
  primary_owner?: string | null;
}

export interface GraphEdgeResponse {
  source: string;
  target: string;
  imported_names: string[];
}

/**
 * Shared shape for the node/link graph-response envelopes. The concrete
 * responses below differ only in their node type (and a few extra fields),
 * so they extend this generic rather than repeat `nodes`/`links`.
 */
export interface GraphLike<N> {
  nodes: N[];
  links: GraphEdgeResponse[];
}

export interface GraphExportResponse extends GraphLike<GraphNodeResponse> {
  /** Server set this true when the response was capped to top-N by PageRank. */
  truncated?: boolean;
  total_node_count?: number;
}

// Community slice (Phase G4 — constellation blossom)
export interface CommunitySliceNodeResponse extends GraphNodeResponse {
  /** True for one-hop neighbor stubs outside the community. */
  is_boundary?: boolean;
}

export interface CommunitySliceResponse
  extends GraphLike<CommunitySliceNodeResponse> {
  community_id: number;
  member_count: number;
  truncated?: boolean;
}

// Architecture super-node graph (Phase A)
export interface ArchitectureNodeResponse {
  community_id: number;
  label: string;
  cohesion: number;
  member_count: number;
  top_file: string;
  avg_pagerank: number;
  hotspot_count: number;
  dead_count: number;
  has_decision: boolean;
  doc_coverage_pct: number;
  languages: string[];
}

export interface ArchitectureEdgeResponse {
  source: number;
  target: number;
  edge_count: number;
}

export interface ArchitectureGraphResponse {
  nodes: ArchitectureNodeResponse[];
  edges: ArchitectureEdgeResponse[];
}

export interface GraphPathResponse {
  path: string[];
  distance: number;
  explanation: string;
  visual_context?: unknown;
}

export interface ModuleNodeResponse {
  module_id: string;
  file_count: number;
  symbol_count: number;
  avg_pagerank: number;
  doc_coverage_pct: number;
}

export interface ModuleEdgeResponse {
  source: string;
  target: string;
  edge_count: number;
}

export interface ModuleGraphResponse {
  nodes: ModuleNodeResponse[];
  edges: ModuleEdgeResponse[];
}

export interface EgoGraphResponse extends GraphLike<GraphNodeResponse> {
  center_node_id: string;
  center_git_meta: GitMetadataResponse | null;
  inbound_count: number;
  outbound_count: number;
}

export interface NodeSearchResult {
  node_id: string;
  language: string;
  symbol_count: number;
}

export interface DeadCodeGraphNodeResponse {
  node_id: string;
  node_type: string;
  language: string;
  symbol_count: number;
  pagerank: number;
  betweenness: number;
  community_id: number;
  is_test: boolean;
  is_entry_point: boolean;
  has_doc: boolean;
  confidence_group: string;
}

export type DeadCodeGraphResponse = GraphLike<DeadCodeGraphNodeResponse>;

export interface HotFilesNodeResponse {
  node_id: string;
  node_type: string;
  language: string;
  symbol_count: number;
  pagerank: number;
  betweenness: number;
  community_id: number;
  is_test: boolean;
  is_entry_point: boolean;
  has_doc: boolean;
  commit_count: number;
}

export type HotFilesGraphResponse = GraphLike<HotFilesNodeResponse>;

export interface RepoStatsResponse {
  file_count: number;
  symbol_count: number;
  entry_point_count: number;
  doc_coverage_pct: number;
  freshness_score: number;
  dead_export_count: number;
}
