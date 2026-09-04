// ---------------------------------------------------------------------------
// Graph Intelligence
// ---------------------------------------------------------------------------

// Imported rather than re-declared: `group` is a closed vocabulary pinned to
// the engine's edge-type set by a Python test, and a local copy here would be
// its third.
import type { SymbolRelationGroup } from "@repowise-dev/types/symbols";

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
  /** Which resolution strategy produced the edge. Absent on an older index. */
  resolution_origin?: string | null;
}

export interface CallersCalleesResponse {
  symbol_id: string;
  symbol: SymbolNodeSummary;
  /** `calls` edges only. Every other relation kind is in `relations`. */
  callers: CallerCalleeEntry[];
  callees: CallerCalleeEntry[];
  /** True totals, not the number of rows served. */
  caller_count: number;
  callee_count: number;
  truncated: boolean;
  /** Heritage, framework wiring and references, each named and counted.
   *  Absent on a backend that predates the split. */
  relations?: SymbolRelationGroup<CallerCalleeEntry>[];
}

export interface CommunityMember {
  path: string;
  pagerank: number;
  is_entry_point: boolean;
  /** Cross-link signals, so a count can name the files behind it. Optional: an
   *  older server omits them, which reads as "no signal", not as "not flagged". */
  is_hotspot?: boolean;
  is_dead?: boolean;
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
  conductance?: number | null;
  member_count: number;
  hidden_member_count?: number;
  members: CommunityMember[];
  truncated: boolean;
  neighboring_communities: NeighboringCommunity[];
  /** State of the area rather than its shape. Every field below is optional:
   *  additive on the wire, and absent from an older server.
   *
   *  LOC-weighted mean health over the members that carry a score, 0-10, higher
   *  is better. `null` when none do — which is not zero and must not render as
   *  a score. */
  health_score?: number | null;
  /** How many members contributed to `health_score`. */
  scored_member_count?: number;
  /** Members flagged hot / dead / decision-anchored, over every member — not
   *  only the page returned in `members`. */
  hot_count?: number;
  dead_count?: number;
  decision_count?: number;
  /** Who is primary owner on the most members, and how many. "Most files
   *  owned", not "most commits" — see the router comment. */
  primary_owner?: string | null;
  primary_owner_file_count?: number;
}

export interface CommunitySummaryItem {
  community_id: number;
  label: string;
  cohesion: number;
  conductance?: number | null;
  member_count: number;
  hidden_member_count?: number;
  top_file: string;
}

export interface GraphMetricsResponse {
  target: string;
  node_type: string;
  pagerank: number;
  pagerank_percentile: number;
  betweenness: number;
  betweenness_percentile: number;
  /** False when the node appeared after the last exact centrality scoring. */
  betweenness_scored?: boolean;
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
  /** Why the walk stopped, and how each hop resolved. Absent on an older index.
   *  `trace_via` is pairwise with `trace`, so it is one shorter. */
  termination?: string | null;
  termination_detail?: Record<string, number> | null;
  trace_via?: (string | null)[] | null;
}

export interface ExecutionFlowsResponse {
  total_entry_points: number;
  flows: ExecutionFlowEntry[];
}
