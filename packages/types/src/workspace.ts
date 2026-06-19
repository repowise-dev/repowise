/**
 * Canonical workspace types — multi-repo views (cross-repo summary, shared
 * contracts, co-changes) plus a few shared per-repo aggregates that the
 * workspace UI consumes directly.
 *
 * Canonical source: engine `WorkspaceResponse` and the per-domain rollups
 * (RepoStats, GitSummary). Downstream backends should rename via an adapter
 * to match these field names before passing to UI components.
 */

export interface RepoStats {
  file_count: number;
  symbol_count: number;
  entry_point_count: number;
  doc_coverage_pct: number;
  freshness_score: number;
  dead_export_count: number;
}

export interface WorkspaceCrossRepoSummary {
  co_change_count: number;
  package_dep_count: number;
  top_connections: Array<{ repos: string[]; edge_count: number }>;
}

export interface WorkspaceContractSummary {
  total_contracts: number;
  total_links: number;
  by_type: Record<string, number>;
}

export interface WorkspaceContractLinkEntry {
  contract_id: string;
  contract_type: string;
  match_type: string;
  confidence: number;
  provider_repo: string;
  provider_file: string;
  provider_symbol: string;
  consumer_repo: string;
  consumer_file: string;
  consumer_symbol: string;
}

export interface WorkspaceCoChangeEntry {
  source_repo: string;
  source_file: string;
  target_repo: string;
  target_file: string;
  strength: number;
  frequency: number;
  last_date: string;
}

export interface WorkspacePackageDepEntry {
  source_repo: string;
  source_manifest: string;
  target_repo: string;
  target_package: string;
  kind: string;
}

// ---------------------------------------------------------------------------
// System graph — the service-granular, typed cross-repo structure that the
// Live System Map, blast radius, the DSM, and the MCP/CLI surfaces all read.
// Mirrors `repowise.core.workspace.system_graph` (Python). Edge direction is
// uniform: `source` depends on / calls `target`.
// ---------------------------------------------------------------------------

/** Transport of a system-graph edge. `db` is reserved for a future transport. */
export type SystemEdgeKind = "http" | "grpc" | "event" | "package" | "co_change" | "db";

/** How confidently an edge was matched. Behavioral co-change edges are `inferred`. */
export type SystemEdgeMatchType = "exact" | "candidate" | "manual" | "inferred";

/** A service in the workspace (or a repo-root node when the repo is undivided). */
export interface SystemNode {
  /** Stable id: `"repo"` or `"repo::service/path"`. */
  id: string;
  /** Repo alias — the grouping attribute. */
  repo: string;
  /** Service boundary path, or null for a whole-repo node. */
  service_path: string | null;
  /** Display name (service directory basename, or repo alias). */
  name: string;
  kind: "service" | "frontend" | "worker" | "library" | "external";
  provider_count: number;
  consumer_count: number;
  contract_types: string[];
  /** Exposes provider contracts no consumer calls. */
  is_orphan_provider: boolean;
  /** Consumes contracts that never matched a provider. */
  is_orphan_consumer: boolean;
  /** Participates in no edges. */
  is_isolated: boolean;
}

/** A typed, directed relationship between two services (`source` → `target`). */
export interface SystemEdge {
  id: string;
  source: string;
  target: string;
  kind: SystemEdgeKind;
  match_type: SystemEdgeMatchType;
  confidence: number;
  /** Number of underlying contracts / co-changes / deps this edge aggregates. */
  weight: number;
  /** True for contract/package edges, false for behavioral co-change edges. */
  structural: boolean;
  /** Back-pointers to the underlying evidence, for drill-down (bounded). */
  contract_refs: string[];
}

export interface SystemGraph {
  version: number;
  generated_at: string;
  nodes: SystemNode[];
  edges: SystemEdge[];
  diagnostics: ExtractionDiagnostics;
}

// ---------------------------------------------------------------------------
// Extraction diagnostics — explains the cross-repo link count (providers /
// consumers found, unmatched-by-reason, orphan providers, weak links).
// Mirrors `repowise.core.workspace.diagnostics`.
// ---------------------------------------------------------------------------

/** Why a consumer contract never formed a cross-repo link. */
export type UnmatchedReason = "no_provider" | "internal_only" | "unlinked";

export interface RepoDiagnostics {
  repo: string;
  providers_by_type: Record<string, number>;
  consumers_by_type: Record<string, number>;
  provider_count: number;
  consumer_count: number;
}

export interface UnmatchedConsumer {
  repo: string;
  file_path: string;
  contract_id: string;
  contract_type: string;
  reason: UnmatchedReason;
}

export interface OrphanProvider {
  repo: string;
  file_path: string;
  contract_id: string;
  contract_type: string;
}

export interface ExtractionDiagnostics {
  total_providers: number;
  total_consumers: number;
  total_links: number;
  weak_link_count: number;
  repo_breakdown: RepoDiagnostics[];
  unmatched_consumers: UnmatchedConsumer[];
  unmatched_by_reason: Record<string, number>;
  orphan_providers: OrphanProvider[];
}
