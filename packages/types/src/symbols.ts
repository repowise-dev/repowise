/**
 * Canonical symbol types.
 *
 * Canonical source: engine `SymbolResponse`. Some downstream backends emit a
 * leaner shape that omits `repository_id` and `symbol_id` — consumer-side
 * adapters synthesise both before passing data to components.
 */

export type SymbolKind =
  | "function"
  | "method"
  | "class"
  | "interface"
  | "struct"
  | "enum"
  | "trait"
  | "module"
  | "variable"
  | "type"
  | (string & {});

export type SymbolVisibility = "public" | "private" | "protected" | (string & {});

/**
 * Transparent breakdown of the composite importance score. Mirrors the
 * server's ``SymbolImportanceComponents``; lets the UI explain *why* a
 * symbol ranks where it does (tooltip on the score chip).
 */
export interface SymbolImportanceComponents {
  file_pagerank: number;
  visibility_factor: number;
  complexity_norm: number;
  kind_boost: number;
  is_entry_point: boolean;
}

/** Renamed `CodeSymbol` to avoid shadowing the global `Symbol`. */
export interface CodeSymbol {
  id: string;
  repository_id: string;
  file_path: string;
  symbol_id: string;
  name: string;
  qualified_name: string;
  kind: SymbolKind;
  signature: string;
  start_line: number;
  end_line: number;
  docstring: string | null;
  visibility: SymbolVisibility;
  is_async: boolean;
  complexity_estimate: number;
  language: string;
  parent_name: string | null;
  /** Composite importance score (0–1ish). Populated by the list endpoint. */
  importance_score?: number | null;
  importance_components?: SymbolImportanceComponents | null;
  /** File-level signals — populated by the list endpoint via JOINs. */
  file_pagerank?: number | null;
  is_entry_point?: boolean | null;
  file_churn_percentile?: number | null;
  file_is_hotspot?: boolean | null;
  /** Function-blame join — populated by the list/detail endpoints when a
   *  git_function_blame row exists for the symbol. */
  blame_mod_count?: number | null;
  blame_recent_mod_count?: number | null;
  blame_median_author_time?: number | null;
  blame_owner_name?: string | null;
  blame_owner_line_pct?: number | null;
}

// ---------------------------------------------------------------------------
// Symbol detail (the symbol entity page aggregate)
// ---------------------------------------------------------------------------

export interface SymbolCallEntry {
  symbol_id: string;
  name: string;
  kind: string;
  file: string;
  start_line: number | null;
  edge_type: string;
  confidence: number;
}

export interface SymbolDetailGraph {
  pagerank: number;
  in_degree: number;
  out_degree: number;
  callers: SymbolCallEntry[];
  callees: SymbolCallEntry[];
}

export interface SymbolFileContext {
  file_path: string;
  health_score: number | null;
  is_hotspot: boolean | null;
  primary_owner: string | null;
  language: string;
}

export interface SymbolDetailResponse {
  symbol: CodeSymbol;
  graph: SymbolDetailGraph;
  governing_decisions: { id: string; title: string; status: string }[];
  file_context: SymbolFileContext;
}

export interface SymbolList {
  total: number;
  symbols: CodeSymbol[];
}

// ---------------------------------------------------------------------------
// Unified symbol-detail shape (drawer + route render the same body)
// ---------------------------------------------------------------------------

/** A caller/callee edge in the normalized symbol body. */
export interface SymbolBodyCall {
  symbol_id: string;
  name: string;
  file: string;
  edge_type: string;
  confidence?: number | null;
}

/** Graph-intelligence block for the unified body (optional — degrades when absent). */
export interface SymbolBodyGraph {
  in_degree: number;
  out_degree: number;
  callers: SymbolBodyCall[];
  callees: SymbolBodyCall[];
  pagerank_percentile?: number | null;
  betweenness_percentile?: number | null;
  community_label?: string | null;
  entry_point_score?: number | null;
}

/** File-level git intelligence for the unified body (optional). */
export interface SymbolBodyGit {
  primary_owner_name: string | null;
  primary_owner_commit_pct?: number | null;
  recent_owner_name?: string | null;
  bus_factor?: number | null;
  contributor_count?: number | null;
  commit_count_90d?: number | null;
  is_hotspot?: boolean | null;
  churn_percentile?: number | null;
}

export interface SymbolBodyCoChange {
  file_path: string;
  co_change_count: number;
}

export interface SymbolBodyDeadFinding {
  id: string;
  kind: string;
  reason: string;
  lines: number;
  safe_to_delete: boolean;
}

/**
 * The single normalized shape rendered by `SymbolDetailBody`. Both the drawer
 * (`CodeSymbol` + graph/git APIs) and the route (`SymbolDetailResponse`)
 * normalize into this so they expose the same capabilities. Every block beyond
 * `identity` is optional so a surface that lacks a feed degrades gracefully.
 */
export interface SymbolDetailData {
  identity: {
    name: string;
    qualified_name?: string | null;
    kind: SymbolKind;
    visibility?: SymbolVisibility | null;
    language?: string | null;
    is_async?: boolean | null;
    file_path: string;
    start_line: number;
    parent_name?: string | null;
    file_is_hotspot?: boolean | null;
  };
  signature?: string | null;
  docstring?: string | null;
  importance_score?: number | null;
  complexity_estimate?: number | null;
  blame_mod_count?: number | null;
  blame_recent_mod_count?: number | null;
  blame_median_author_time?: number | null;
  blame_owner_name?: string | null;
  blame_owner_line_pct?: number | null;
  graph?: SymbolBodyGraph | null;
  /** Heritage relations (extends/implements + extended-by). Renders when present. */
  heritage?: SymbolHeritage | null;
  git?: SymbolBodyGit | null;
  co_changes?: SymbolBodyCoChange[];
  dead_code?: SymbolBodyDeadFinding[];
  governing_decisions?: { id: string; title: string; status: string }[];
  file_context?: {
    health_score?: number | null;
    language?: string | null;
  } | null;
}

// ---------------------------------------------------------------------------
// Heritage (extends / implements / trait_impl / mixin / overrides)
// ---------------------------------------------------------------------------

/**
 * Heritage relation kinds. Mirrors the engine's edge_type values for
 * heritage edges in the symbol graph plus the raw AST-level "mixin" kind.
 */
export type HeritageKind =
  | "extends"
  | "implements"
  | "trait_impl"
  | "mixin"
  | "method_overrides"
  | "method_implements";

/**
 * A single resolved heritage edge.
 *
 * `child_id` and `parent_id` are symbol-graph node IDs (e.g.
 * `src/app.py::MyClass`). `confidence` is present for resolved relations
 * (0.0–1.0) and absent for raw, unresolved entries lifted directly out of
 * `parsed_files.json::heritage` (in which case only `child_name`,
 * `parent_name`, `kind`, and `line` are meaningful).
 */
export interface HeritageRelation {
  child_id?: string;
  parent_id?: string;
  child_name: string;
  parent_name: string;
  kind: HeritageKind;
  line: number;
  confidence?: number;
}

/**
 * Heritage view for a single symbol — both directions of the relation.
 *
 * `parents` are the relations where this symbol is the child (i.e. what it
 * extends/implements). `children` are the relations where this symbol is the
 * parent (i.e. what extends/implements it).
 */
export interface SymbolHeritage {
  symbol_id: string;
  parents: HeritageRelation[];
  children: HeritageRelation[];
}
