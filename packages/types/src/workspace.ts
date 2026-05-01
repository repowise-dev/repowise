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
