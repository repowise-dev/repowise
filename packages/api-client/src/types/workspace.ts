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
  // Canonical 0-100 health score. Optional so a frontend ahead of its server
  // degrades rather than rendering NaN; null means "not measured", which is
  // not the same as a measured zero.
  health_score?: number | null;
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
  /**
   * 1-indexed line of the declaration or call. Null when the contract never
   * bound to a line.
   */
  line: number | null;
  /**
   * Ingestion symbol id (`"<rel_path>::<name>"`). Null when the repo has no
   * index or nothing is declared at `line` — the contract still matches, it
   * just cannot be traversed into the call graph.
   */
  symbol_id: string | null;
  /**
   * Extractor-supplied detail (`extraction_layer`, `framework`, `method`,
   * `path`, `table`, `package`...). Keys vary by contract type.
   */
  meta: Record<string, unknown>;
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
  /**
   * Service boundary the provider sits behind, when the workspace declares one.
   * Matching skips a pair only when the repo *and* the service are the same, so
   * this is what explains a link between two services inside one repo.
   */
  provider_service: string | null;
  /** The same, for the calling side. */
  consumer_service: string | null;
  /**
   * The linked contracts' symbol ids, so a caller can name the code rather than
   * a display label. Null when that side never bound to one.
   */
  provider_symbol_id: string | null;
  consumer_symbol_id: string | null;
}

export interface WorkspaceContractsResponse {
  contracts: WorkspaceContractEntry[];
  links: WorkspaceContractLinkEntry[];
  total_contracts: number;
  total_links: number;
  by_type: Record<string, number>;
}

/**
 * One contract, keyed by `(repo, file_path, contract_id)`.
 *
 * Carries the request/response shape the list endpoint deliberately withholds:
 * `schema` is present on roughly a third of contracts and single rows run to
 * full inline type declarations, so it is affordable one at a time and not 200
 * at a time.
 */
export interface WorkspaceContractDetail {
  contract: WorkspaceContractEntry;
  /**
   * The artifact's `schema` block, named around Pydantic — a field literally
   * called `schema` shadows an attribute of `BaseModel`.
   */
  contract_schema: Record<string, unknown> | null;
  /** Links this contract participates in, on whichever side it plays. */
  links: WorkspaceContractLinkEntry[];
  /**
   * Why this consumer matched no provider (`external_host`, `internal_only`,
   * `no_provider`). Null for providers, for linked consumers, and when no
   * system graph is built.
   */
  unmatched_reason: string | null;
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
  /** Pairs matching the query, before `limit` paged them. */
  total: number;
  /**
   * Pairs the miner scored before its edge caps trimmed the stored overlay.
   * Not every pair in git history — each session's file list is bounded
   * before pairing, so some pairs are in neither number.
   */
  total_mined: number;
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

// ---------------------------------------------------------------------------
// System graph + extraction diagnostics — the canonical service-granular
// shapes live in @repowise-dev/types; the wire responses match them 1:1, so we
// re-export rather than re-derive (the repo-wide consolidation convention).
// ---------------------------------------------------------------------------

export type {
  SystemNode,
  SystemEdge,
  SystemGraph,
  SystemEdgeKind,
  SystemEdgeMatchType,
  ExtractionDiagnostics,
  RepoDiagnostics,
  UnmatchedConsumer,
  UnmatchedReason,
  OrphanProvider,
  ImpactedNode,
  CrossRepoBlastRadius,
  ContractSchema,
  SchemaField,
  BreakingChange,
  BreakingChangeConsumer,
  BreakingChangeReport,
  BreakingChangeSeverity,
  ConformanceRule,
  ConformanceViolation,
  DependencyCycle,
  ConformanceReport,
  DsmCell,
  DsmMatrix,
  ArchitectureMetrics,
  NodeArchitectureRole,
  NodeRole,
  ArchitectureType,
} from "@repowise-dev/types";

import type {
  SystemGraph,
  CrossRepoBlastRadius,
  BreakingChangeReport,
  ConformanceReport,
  ArchitectureMetrics,
} from "@repowise-dev/types";

/** `GET /api/workspace/system-graph` — the full service-granular system graph. */
export type WorkspaceSystemGraphResponse = SystemGraph;

/** `GET /api/workspace/blast-radius` — cross-repo downstream impact set. */
export type WorkspaceBlastRadiusResponse = CrossRepoBlastRadius;

/** `GET /api/workspace/breaking-changes` — incompatible provider changes + impact. */
export type WorkspaceBreakingChangesResponse = BreakingChangeReport;

/** `GET /api/workspace/conformance` — architecture rule violations + cycles. */
export type WorkspaceConformanceResponse = ConformanceReport;

/** `GET /api/workspace/architecture` — architecture-complexity metrics + roles. */
export type WorkspaceArchitectureResponse = ArchitectureMetrics;
