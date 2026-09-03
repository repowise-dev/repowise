/**
 * Dependency-registry types — the `external_systems` table populated by the
 * manifest parsers during ingestion. Mirrors the Pydantic models served by
 * `/api/repos/{id}/external-systems`.
 */

/**
 * Canonical I/O-boundary kinds. Mirrors `IO_KINDS` in the Python classifier
 * (`packages/core/.../ingestion/external_systems/io_kind.py`). The cross-
 * language parity guard lives in `__tests__/contracts.test.ts` (TS half) and
 * `tests/unit/ingestion/test_io_kind.py` (Python half). Change one, change
 * all three.
 */
export const C4_IO_KINDS = [
  "db",
  "network",
  "filesystem",
  "subprocess",
  "lock",
] as const;

/** A dependency's I/O-boundary type, or null when it isn't typed. */
export type C4IoKind = (typeof C4_IO_KINDS)[number];

/** One declared third-party dependency. */
export interface ExternalSystemEntry {
  name: string;
  display_name: string;
  /** npm | pypi | cargo | gomod | nuget | ... */
  ecosystem: string;
  /** framework | service | tool | library */
  category: string;
  /** db | network | filesystem | subprocess | lock, or null when untyped. */
  io_kind: C4IoKind | null;
  version: string | null;
  /** Manifest path the dependency was declared in, e.g. "packages/web/package.json". */
  declared_in: string;
  is_dev_dep: boolean;
}

/** The full dependency registry for a repository. */
export interface ExternalSystemsRegistry {
  items: ExternalSystemEntry[];
  total: number;
  prod_count: number;
  dev_count: number;
  ecosystems: string[];
  manifests: string[];
}

export type ExternalSystemLinkState = "linked" | "unlinked";
export type ExternalSystemsSummaryScope = "primary" | "all";

/** One canonical package with declaration and persisted graph-usage aggregates. */
export interface ExternalSystemSummaryEntry {
  package_key: string;
  name: string;
  display_name: string;
  ecosystem: string;
  category: string;
  io_kind: C4IoKind | null;
  runtime_declared: boolean;
  dev_declared: boolean;
  declaration_count: number;
  manifest_count: number;
  versions: string[];
  versions_total: number;
  versions_truncated: boolean;
  multiple_versions: boolean;
  external_node_count: number;
  import_edge_count: number;
  importing_file_count: number;
  link_state: ExternalSystemLinkState;
}

/** Bounded package summaries for the external-dependency scan surface. */
export interface ExternalSystemsSummary {
  items: ExternalSystemSummaryEntry[];
  returned: number;
  total_packages: number;
  limit: number;
  offset: number;
  truncated: boolean;
  scope: ExternalSystemsSummaryScope;
  excluded_declarations: number;
  total_declarations: number;
  runtime_packages: number;
  dev_only_packages: number;
  observed_packages: number;
  linked_packages: number;
  unlinked_packages: number;
  linked_without_imports: number;
  ecosystems: string[];
  manifest_count: number;
}

export type ExternalSystemMatchBasis = "exact" | "subpath" | "mapped" | "mixed" | "unresolved";

export interface ExternalSystemGraphTarget {
  node_id: string;
  match_basis: Exclude<ExternalSystemMatchBasis, "mixed" | "unresolved">;
}

export interface ExternalSystemRelationshipNode {
  aggregate_key: string;
  label: string;
  community_id: number;
  importing_file_count: number;
  import_edge_count: number;
  top_file: string | null;
}

export interface ExternalSystemRelationshipEdge {
  source: string;
  target: string;
  import_edge_count: number;
}

export interface ExternalSystemRelationshipGraph {
  package_key: string;
  package_name: string;
  package_node_id: string;
  match_basis: ExternalSystemMatchBasis;
  matched_external_nodes: ExternalSystemGraphTarget[];
  matched_external_nodes_total: number;
  matched_external_nodes_truncated: boolean;
  evidence_target_limit: number;
  evidence_truncated: boolean;
  nodes: ExternalSystemRelationshipNode[];
  edges: ExternalSystemRelationshipEdge[];
  aggregate_total: number;
  aggregate_returned: number;
  edge_total: number;
  edge_returned: number;
  importing_file_total: number;
  import_edge_total: number;
  node_limit: number;
  edge_limit: number;
  truncated: boolean;
  scope: ExternalSystemsSummaryScope;
}

export interface ExternalSystemImportingFile {
  path: string;
  language: string;
  import_edge_count: number;
  matched_external_node_count: number;
}

export interface ExternalSystemImportingFiles {
  package_key: string;
  aggregate_key: string;
  items: ExternalSystemImportingFile[];
  total: number;
  returned: number;
  limit: number;
  offset: number;
  truncated: boolean;
  scope: ExternalSystemsSummaryScope;
}
