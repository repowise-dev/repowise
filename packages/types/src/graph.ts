/**
 * Canonical graph types — file/module dependency graph plus graph-intelligence
 * surfaces (callers/callees, communities, execution flows, metrics).
 *
 * Canonical source: engine `PipelineResult.graph` (NetworkX node_link_data
 * format) and the per-symbol intelligence endpoints in
 * `packages/server/src/repowise/server/schemas.py`.
 *
 * Some downstream backends emit a looser `{ nodes, links, directed?, multigraph? }`
 * shape; consumer-side adapters are responsible for converting that to
 * `GraphExport` below before passing data to components.
 */

// ---------------------------------------------------------------------------
// Core node + link
// ---------------------------------------------------------------------------

export interface GraphNode {
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
}

export interface GraphLink {
  source: string;
  target: string;
  imported_names: string[];
  /** Edge kind from v0.4.0 framework-aware extractors (e.g. "spring.bean", "rails.route"). */
  edge_type?: string;
  /** Confidence score for resolved symbol-level call edges (v0.4.x). */
  confidence?: number;
}

export interface GraphExport {
  nodes: GraphNode[];
  links: GraphLink[];
}

// ---------------------------------------------------------------------------
// NetworkX-shaped raw payload (what some downstream backends emit)
// ---------------------------------------------------------------------------

export interface RawGraphNode {
  id?: string;
  node_id?: string;
  label?: string;
  type?: string;
  language?: string;
  loc?: number;
  is_test?: boolean;
  is_config?: boolean;
  is_entry_point?: boolean;
  community?: number;
  community_id?: number;
  pagerank?: number;
  betweenness?: number;
  symbol_count?: number;
  has_doc?: boolean;
  [extra: string]: unknown;
}

export interface RawGraphLink {
  source: string;
  target: string;
  kind?: string;
  weight?: number;
  confidence?: number;
  imported_names?: string[];
  edge_type?: string;
  [extra: string]: unknown;
}

export interface RawGraph {
  nodes: RawGraphNode[];
  links?: RawGraphLink[];
  edges?: RawGraphLink[];
  directed?: boolean;
  multigraph?: boolean;
}

export interface RawGraphResponse {
  graph: RawGraph;
  pagerank: Record<string, number>;
  betweenness: Record<string, number>;
  communities: Record<string, number>;
}

// ---------------------------------------------------------------------------
// Module rollup
// ---------------------------------------------------------------------------

export interface ModuleNode {
  module_id: string;
  file_count: number;
  symbol_count: number;
  avg_pagerank: number;
  doc_coverage_pct: number;
}

export interface ModuleEdge {
  source: string;
  target: string;
  edge_count: number;
}

export interface ModuleGraph {
  nodes: ModuleNode[];
  edges: ModuleEdge[];
}

// ---------------------------------------------------------------------------
// Ego (neighborhood) graph
// ---------------------------------------------------------------------------

export interface EgoGraph {
  nodes: GraphNode[];
  links: GraphLink[];
  center_node_id: string;
  /** Optional git metadata for the center file. Present when the engine has indexed git history. */
  center_git_meta?: import("./git.js").GitMetadata | null;
  inbound_count: number;
  outbound_count: number;
}

// ---------------------------------------------------------------------------
// Path finder
// ---------------------------------------------------------------------------

export interface GraphPath {
  path: string[];
  distance: number;
  explanation: string;
}

// ---------------------------------------------------------------------------
// Symbol-level intelligence (v0.4.x)
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

export interface CallersCallees {
  symbol_id: string;
  symbol: SymbolNodeSummary;
  callers: CallerCalleeEntry[];
  callees: CallerCalleeEntry[];
  caller_count: number;
  callee_count: number;
  truncated: boolean;
}

// ---------------------------------------------------------------------------
// Communities (Leiden — v0.4.0)
// ---------------------------------------------------------------------------

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

export interface CommunityDetail {
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

// ---------------------------------------------------------------------------
// Graph metrics + execution flows
// ---------------------------------------------------------------------------

export interface GraphMetrics {
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

export interface ExecutionFlows {
  total_entry_points: number;
  flows: ExecutionFlowEntry[];
}
