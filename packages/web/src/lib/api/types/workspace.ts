// ---------------------------------------------------------------------------
// Workspace
// ---------------------------------------------------------------------------

export interface WorkspaceRepoEntry {
  alias: string;
  path: string;
  is_primary: boolean;
  indexed_at: string | null;
  last_commit_at_index: string | null;
  // Per-repo stats from each repo's wiki.db
  repo_id: string | null;
  file_count: number;
  symbol_count: number;
  page_count: number;
  doc_coverage_pct: number;
  hotspot_count: number;
  // Phase B server augmentation
  status?: "indexed" | "needs_index" | "missing_dir" | null;
  docs_enabled?: boolean | null;
  docs_skip_reason?: string | null;
}

export interface WorkspaceSyncResult {
  alias: string;
  repo_id: string | null;
  status: "accepted" | "skipped" | "error";
  job_id: string | null;
  reason: string | null;
}

export interface WorkspaceSyncResponse {
  results: WorkspaceSyncResult[];
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

export interface WorkspaceResponse {
  is_workspace: boolean;
  workspace_root: string | null;
  workspace_name: string | null;
  repos: WorkspaceRepoEntry[];
  default_repo: string | null;
  cross_repo_summary: WorkspaceCrossRepoSummary | null;
  contract_summary: WorkspaceContractSummary | null;
}

export interface WorkspaceContractEntry {
  contract_id: string;
  contract_type: string;
  role: string;
  repo: string;
  file_path: string;
  symbol_name: string;
  confidence: number;
  service: string | null;
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

export interface WorkspaceContractsResponse {
  contracts: WorkspaceContractEntry[];
  links: WorkspaceContractLinkEntry[];
  total_contracts: number;
  total_links: number;
  by_type: Record<string, number>;
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

export interface WorkspaceCoChangesResponse {
  co_changes: WorkspaceCoChangeEntry[];
  total: number;
}

export interface WorkspaceGraphNode {
  repo_id: string;
  name: string;
  file_count: number;
  coverage_pct: number;
  health_score: number;
  health_score_source: "canonical" | "derived";
  top_language: string;
}

export interface WorkspaceGraphEdge {
  source: string;
  target: string;
  type: "contract" | "co_change";
  strength: number;
  label: string | null;
}

export interface WorkspaceGraphResponse {
  nodes: WorkspaceGraphNode[];
  edges: WorkspaceGraphEdge[];
}
