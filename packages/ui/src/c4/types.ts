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
