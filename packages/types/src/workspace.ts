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

// ---------------------------------------------------------------------------
// Cross-repo blast radius — reachability over the system graph. "If I change
// this service, what downstream services and repos break?" Mirrors the
// single-repo blast-radius vocabulary (`blast-radius.ts`): impacted items carry
// an impact `score` and a `distance`. Mirrors `repowise.core.workspace.blast_radius`.
// ---------------------------------------------------------------------------

/** A service reachable from the change, with its ranked impact. */
export interface ImpactedNode {
  /** System-graph node id (`"repo"` or `"repo::service/path"`). */
  id: string;
  repo: string;
  name: string;
  kind: "service" | "frontend" | "worker" | "library" | "external";
  /** Hops from the nearest changed node (1 = a direct dependent). */
  distance: number;
  /** 0-1 ranked impact, with distance decay and behavioral weighting baked in. */
  score: number;
  /** Reachable via an all-structural path (a real dependency, not co-change). */
  structural: boolean;
  /** Distinct edge kinds that carried impact into this node. */
  edge_kinds: SystemEdgeKind[];
}

export interface CrossRepoBlastRadius {
  /** Resolved node ids the traversal started from. */
  targets: string[];
  /** Distinct repos of the targets. */
  target_repos: string[];
  /** Ranked impact set (strongest first), capped server-side. */
  impacted: ImpactedNode[];
  /** Distinct repos in the impact set, excluding the target repos. */
  impacted_repos: string[];
  /** Impacted nodes reachable via a real dependency. */
  structural_count: number;
  /** Impacted nodes reachable only via co-change correlation. */
  behavioral_count: number;
  max_distance: number;
  /** True count before the server-side cap. */
  total_impacted: number;
  /** Target strings that matched no node or repo. */
  unresolved_targets: string[];
}

// ---------------------------------------------------------------------------
// Contract schema — the optional request/response field shape a contract
// carries when a parser can recover it (proto message fields today; OpenAPI
// when present). Drives schema-level breaking-change detection.
// Mirrors `repowise.core.workspace.contract_schema`.
// ---------------------------------------------------------------------------

/** One field in a request or response shape. `number` is the proto wire tag. */
export interface SchemaField {
  name: string;
  type: string;
  required?: boolean;
  number?: number | null;
  repeated?: boolean;
}

export interface ContractSchema {
  /** Which parser produced the shape (`"proto"` / `"openapi"`). */
  source: string;
  request_fields: SchemaField[];
  response_fields: SchemaField[];
}

// ---------------------------------------------------------------------------
// Breaking-change guard — provider contract changes that break consumers across
// repos, computed by diffing the current contracts against the previously
// indexed set. Mirrors `repowise.core.workspace.breaking_change`.
// ---------------------------------------------------------------------------

/** How a breaking change ranks. `breaking` = wire-incompatible; `warning` = source risk. */
export type BreakingChangeSeverity = "breaking" | "warning";

/** A consumer endangered by a provider's breaking change (from a matched link). */
export interface BreakingChangeConsumer {
  repo: string;
  service: string | null;
  /** System-graph node id of the consumer (for map badging). */
  node_id: string;
  /** The exact consumer file that calls the changed contract. */
  file: string;
  symbol: string;
  match_type: string;
  confidence: number;
}

export interface BreakingChange {
  /** Rule key: `removed_endpoint` | `removed_field` | `field_type_changed` | ... */
  kind: string;
  severity: BreakingChangeSeverity;
  contract_id: string;
  contract_type: string;
  provider_repo: string;
  provider_file: string;
  provider_symbol: string;
  provider_service: string | null;
  /** System-graph node id of the changed provider. */
  provider_node_id: string;
  /** Human-readable one-liner. */
  detail: string;
  field_name?: string | null;
  old_value?: string | null;
  new_value?: string | null;
  impacted_consumers: BreakingChangeConsumer[];
}

export interface BreakingChangeReport {
  version: number;
  generated_at: string;
  changes: BreakingChange[];
  total: number;
  breaking_count: number;
  warning_count: number;
  /** Distinct repos with an endangered consumer. */
  impacted_repos: string[];
  /** Distinct system-graph node ids with an endangered consumer. */
  impacted_services: string[];
  total_impacted_consumers: number;
}
