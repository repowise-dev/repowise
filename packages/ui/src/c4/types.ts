/**
 * Frontend mirror of the backend C4 Pydantic models from
 * `packages/server/src/repowise/server/schemas.py`. Keep field names in
 * lock-step — these are the on-the-wire shapes returned by /api/graph/{id}/c4/*.
 */

export type C4Level = 1 | 2 | 3;

export type C4Category = "framework" | "service" | "tool" | "library";

export interface C4Person {
  id: string;
  name: string;
  description: string;
}

export interface C4System {
  id: string;
  name: string;
  description: string;
}

export interface C4ExternalSystem {
  id: string;
  name: string;
  display_name: string;
  category: C4Category | string;
  ecosystem: string;
  version: string | null;
}

export interface C4Container {
  id: string;
  name: string;
  path: string;
  language: string;
  file_count: number;
  symbol_count: number;
  hotspot_count: number;
  dead_count: number;
}

export interface C4Component {
  id: string;
  name: string;
  path: string;
  container_id: string;
  file_count: number;
  symbol_count: number;
}

export interface C4Relation {
  source_id: string;
  target_id: string;
  label: string;
  edge_count: number;
  edge_types: string[];
}

export interface C4L1 {
  system: C4System;
  people: C4Person[];
  external_systems: C4ExternalSystem[];
  relations: C4Relation[];
}

export interface C4L2 {
  containers: C4Container[];
  external_systems: C4ExternalSystem[];
  relations: C4Relation[];
}

export interface C4L3 {
  container: C4Container;
  components: C4Component[];
  external_systems: C4ExternalSystem[];
  relations: C4Relation[];
}

/** Node `data` payloads attached to React Flow nodes. Discriminated by `kind`. */
export type C4NodeData =
  | { kind: "system"; system: C4System }
  | { kind: "person"; person: C4Person }
  | { kind: "external"; external: C4ExternalSystem }
  | { kind: "container"; container: C4Container }
  | { kind: "component"; component: C4Component };

export interface C4EdgeData {
  relation: C4Relation;
}

// ---------------------------------------------------------------------------
// Architecture view types (mirror backend ArchitectureViewResponse)
// ---------------------------------------------------------------------------

export type ArchNodeType =
  | "file" | "function" | "class" | "module" | "concept"
  | "config" | "document" | "service" | "table" | "endpoint"
  | "pipeline" | "schema" | "resource";

export interface ArchNode {
  id: string;
  node_type: ArchNodeType;
  name: string;
  file_path: string | null;
  line_range: [number, number] | null;
  summary: string;
  complexity: "simple" | "moderate" | "complex";
  tags: string[];
  language: string | null;
  pagerank: number;
  pagerank_percentile: number;
  betweenness: number;
  in_degree: number;
  out_degree: number;
  community_id: number | null;
  is_entry_point: boolean;
  is_test: boolean;
  is_hotspot: boolean;
  is_dead: boolean;
  has_doc: boolean;
  primary_owner: string | null;
  primary_owner_pct: number | null;
  bus_factor: number | null;
}

export interface ArchEdge {
  source: string;
  target: string;
  edge_type: string;
  direction: "forward" | "backward" | "bidirectional";
  weight: number;
  confidence: number;
}

export interface ArchSubGroup {
  id: string;
  name: string;
  node_ids: string[];
}

export interface ArchLayer {
  id: string;
  name: string;
  description: string;
  node_ids: string[];
  file_count: number;
  complexity_distribution: Record<string, number>;
  health_score: number | null;
  /** Curated drill-down groups within the layer (empty when uncurated). */
  sub_groups: ArchSubGroup[];
  /** Dependency-ordered stacking position (0 = top of the stack). */
  display_order: number;
}

export type ArchTourStepKind = "overview" | "code" | "infra" | "";

export interface ArchTourStep {
  order: number;
  title: string;
  description: string;
  node_ids: string[];
  /** Curated, layer-aware fields (null/empty for legacy LLM tours). */
  target_path: string | null;
  layer_id: string | null;
  reason: string;
  depth: number | null;
  kind: ArchTourStepKind;
  page_type: string | null;
}

export interface ArchitectureView {
  project_name: string;
  project_description: string;
  layers: ArchLayer[];
  nodes: ArchNode[];
  edges: ArchEdge[];
  tour: ArchTourStep[];
  total_files: number;
  total_symbols: number;
  total_edges: number;
  languages: string[];
  frameworks: string[];
  external_systems: C4ExternalSystem[];
  /** Curated, ranked entry points (repo-relative paths; empty when uncurated). */
  entry_points: string[];
  entry_candidates: string[];
}

// ---------------------------------------------------------------------------
// Architecture store types
// ---------------------------------------------------------------------------

export type NavigationLevel = "overview" | "layer-groups" | "layer-detail";
export type Persona = "overview" | "learn" | "deep-dive";
export type DetailLevel = "file" | "class" | "symbol";
export type SearchMode = "fuzzy" | "semantic";

export const PERSONA_NODE_TYPES: Record<Persona, Set<string> | null> = {
  overview: new Set<string>(["file", "module"]),
  learn: null,
  "deep-dive": null,
};

export interface SearchResult {
  nodeId: string;
  name: string;
  node_type: ArchNodeType;
  score: number;
  matchedField: "name" | "summary" | "tags";
}

export interface ArchFilters {
  nodeTypes: Set<string>;
  complexities: Set<string>;
  layerIds: Set<string>;
  edgeCategories: Set<string>;
}

export interface ContainerLayoutResult {
  positions: Map<string, { x: number; y: number }>;
  size: { width: number; height: number };
}
