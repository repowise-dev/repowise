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

// The relation vocabulary lives beside the symbol types that define it, so
// `CallersCallees` below borrows it rather than restating the group union.
import type { SymbolRelationGroup } from "./symbols";

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
  /** Cross-link signals — added in Phase A enrichment. All optional for
   *  back-compat with older backends; consumers should default to false/null. */
  is_hotspot?: boolean;
  churn_percentile?: number | null;
  is_dead?: boolean;
  dead_confidence?: number | null;
  has_decision?: boolean;
  primary_owner?: string | null;
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
  /** Server flagged response as capped (PageRank fill + reserved dead/hot/flow
   *  slots). UI should banner. */
  truncated?: boolean;
  total_node_count?: number;
  /** Signal-overlay counts: repo-wide totals vs how many nodes are in this
   *  response. Lets the UI say "12 of 37 dead files in view" and distinguish
   *  "none in the repo" from "none in the loaded view". Optional — older
   *  backends and endpoints that don't compute them omit these. */
  dead_total?: number | null;
  dead_in_view?: number | null;
  hot_total?: number | null;
  hot_in_view?: number | null;
}

// ---------------------------------------------------------------------------
// Architecture (community super-node) graph
// ---------------------------------------------------------------------------

export interface ArchitectureNode {
  community_id: number;
  label: string;
  /** Decays with size; kept for older servers. Read `conductance` instead. */
  cohesion: number;
  /** Share of this group's dependency volume that leaves it, lower is tighter.
   *  Absent or null on an older index. */
  conductance?: number | null;
  /** Members in the requested population; every figure is over them. */
  member_count: number;
  /** Members the population filter left out. */
  hidden_member_count?: number;
  top_file: string;
  avg_pagerank: number;
  hotspot_count: number;
  dead_count: number;
  has_decision: boolean;
  doc_coverage_pct: number;
  languages: string[];
}

export interface ArchitectureEdge {
  source: number;
  target: number;
  edge_count: number;
}

/** Which non-production files a community view counts. All off = production only. */
export interface GraphPopulation {
  tests: boolean;
  examples: boolean;
  docs: boolean;
}

export const PRODUCTION_ONLY: GraphPopulation = { tests: false, examples: false, docs: false };

/** What the map is counting. Category totals are always reported. */
export interface PopulationBreakdown {
  total: number;
  visible: number;
  tests?: number;
  examples?: number;
  docs?: number;
  include_tests?: boolean;
  include_examples?: boolean;
  include_docs?: boolean;
}

/** Visible files in no drawn community. `files` is the head by PageRank. */
export interface UnclusteredFiles {
  file_count: number;
  files?: string[];
}

export interface ArchitectureGraph {
  nodes: ArchitectureNode[];
  edges: ArchitectureEdge[];
  /** Both absent on an older server. */
  population?: PopulationBreakdown | null;
  unclustered?: UnclusteredFiles | null;
}

// ---------------------------------------------------------------------------
// Community slice (constellation blossom — one hub's member sub-graph)
// ---------------------------------------------------------------------------

export interface CommunitySliceNode extends GraphNode {
  /** True for one-hop neighbor stubs outside the community (cross-cluster). */
  is_boundary?: boolean;
}

export interface CommunitySlice {
  nodes: CommunitySliceNode[];
  links: GraphLink[];
  community_id: number;
  member_count: number;
  truncated?: boolean;
  hidden_member_count?: number;
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
  hotspot_count?: number;
  dead_count?: number;
  has_decision?: boolean;
  primary_owner?: string | null;
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
  visual_context?: unknown;
}

/**
 * Lightweight node-search result used by the path finder autocomplete.
 * Mirrors `NodeSearchResultResponse` from the engine.
 */
export interface NodeSearchResult {
  node_id: string;
  language: string;
  symbol_count: number;
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

/**
 * Which resolution strategy produced a `calls` edge. Closed vocabulary owned
 * by `ingestion/models.py::ResolutionOrigin`; the two are edited together.
 * Render via `originDescriptor` (`@repowise-dev/ui/graph/edge-provenance`) —
 * which origins count as a guess is one decision and lives in one place.
 */
export type ResolutionOrigin =
  | "same_file"
  | "self_scope"
  | "enclosing_class"
  | "receiver_same_file"
  | "same_package"
  | "import_scoped"
  | "receiver_same_package"
  | "package_alias"
  | "module_alias"
  | "crate_root"
  | "receiver_import"
  | "import_merged"
  | "scoped_name"
  | "same_target"
  | "receiver_global"
  | "global_unique"
  | "receiver_typed_same_file"
  | "receiver_typed_same_package"
  | "receiver_typed_import"
  | "receiver_typed_global"
  | "receiver_field_same_file"
  | "receiver_field_same_package"
  | "receiver_field_import"
  | "receiver_field_global"
  | "receiver_framework_same_file"
  | "receiver_framework_same_package"
  | "receiver_framework_import"
  | "receiver_framework_global"
  | "return_type_same_file"
  | "return_type_same_package"
  | "return_type_import"
  | "return_type_global"
  | "self_inherited"
  | "enclosing_inherited";

/**
 * An origin as received, not as declared. An index outlives the bundle reading
 * it, so a newer indexer can stamp a word this build predates —
 * `originDescriptor` degrades those rather than throwing. Same `(string & {})`
 * escape as `SymbolKind`: autocomplete on the vocabulary, no lie about the wire.
 */
export type ResolutionOriginWire = ResolutionOrigin | (string & {});

/**
 * What stopped a traced flow. Closed vocabulary owned by
 * `analysis/execution_flows.py::FlowTermination`. A trace that merely ends
 * reads as "execution ends here" whether it does or whether the walk ran out
 * of things it could follow; this keeps those apart.
 */
export type FlowTermination =
  | "depth_limit"
  | "callees_truncated"
  | "cycle"
  | "confidence_filtered"
  | "excluded_target"
  | "no_callees";

/** A termination as received. See `ResolutionOriginWire`. */
export type FlowTerminationWire = FlowTermination | (string & {});

export interface CallerCalleeEntry {
  symbol_id: string;
  name: string;
  kind: string;
  file: string;
  start_line?: number | null;
  edge_type: string;
  confidence: number;
  /** Absent on an index built before origins were stamped. */
  resolution_origin?: ResolutionOriginWire | null;
}

export interface CallersCallees {
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

// ---------------------------------------------------------------------------
// Communities (Leiden — v0.4.0)
// ---------------------------------------------------------------------------

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

export interface CommunityDetail {
  community_id: number;
  label: string;
  /** Decays with size; kept for older servers. The panel reads `conductance`. */
  cohesion: number;
  /** Share of this group's dependency volume that leaves it, lower is tighter.
   *  Absent or null on an older index. */
  conductance?: number | null;
  /** Members in the requested population; every count is over them. */
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
  /** Why the trace stopped. Absent on an index that carries no terminations. */
  termination?: FlowTerminationWire | null;
  /** For `confidence_filtered` only: declined origin -> count. */
  termination_detail?: Record<string, number> | null;
  /**
   * Origin per hop, pairwise with `trace`, so `trace_via[i]` describes the hop
   * from `trace[i]` to `trace[i + 1]` and the array is one shorter. Omitted
   * when no hop carries one.
   */
  trace_via?: (ResolutionOriginWire | null)[] | null;
}

export interface ExecutionFlows {
  total_entry_points: number;
  flows: ExecutionFlowEntry[];
}
