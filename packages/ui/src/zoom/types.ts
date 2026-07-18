/**
 * Zoom-map data model (mirror of the backend response).
 *
 * The backend serves one nested containment tree (system -> layer -> group ->
 * folder -> file) as a flat list of nodes, each carrying its own `id`, plus
 * parent-relative `relations`. The renderer indexes the list by id and rebuilds
 * the tree from `parent_id` / `children`. Every node's `layout` rect is in its
 * parent's `[0,1]` space, which composes multiplicatively down the tree into an
 * absolute world rect for clip-and-scale drawing.
 *
 * Source of truth: `packages/server/src/repowise/server/schemas/zoom.py`.
 */

/** Node kinds, coarsest to finest. A leaf is always `file`. */
export type ZoomKind = "system" | "layer" | "group" | "folder" | "file";

/** A child's allocation inside its parent, in parent `[0,1]` space. */
export interface ZoomRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Counts rolled up over a node's subtree (a file is its own subtree). */
export interface ZoomMetrics {
  file_count: number;
  descendant_count: number;
  hotspot_count: number;
  dead_count: number;
  entry_point_count: number;
  on_flow_count: number;
}

export interface ZoomNode {
  id: string;
  parent_id: string | null;
  level: number;
  kind: ZoomKind;
  name: string;
  path: string;
  children: string[];
  importance: number;
  sibling_rank: number;
  metrics: ZoomMetrics;
  layout: ZoomRect | null;
  summary: string;
  language: string | null;
  /** Code-health score (0-10, higher = healthier), matching the /files treemap.
   *  Null when the file/subtree was unscored (health is sparse) — read as neutral. */
  health_score: number | null;
  is_entry_point: boolean;
  is_hotspot: boolean;
  is_dead: boolean;
  is_test: boolean;
  on_flow: boolean;
}

/** An aggregated edge between two sibling subtrees under a shared parent. */
export interface ZoomRelation {
  parent_id: string;
  source_id: string;
  target_id: string;
  label: string;
  edge_count: number;
  coupling: string; // loose | moderate | tight
}

/** The complete zoom map for one repository at a given depth/focus. */
export interface ZoomMap {
  root_id: string;
  project_name: string;
  total_files: number;
  max_depth: number;
  truncated: boolean;
  nodes: ZoomNode[];
  relations: ZoomRelation[];
}
