/**
 * Zoom-map data model (mirror of the backend response).
 *
 * The backend serves one nested containment tree (system -> layer -> group ->
 * folder -> file) as a flat list of nodes, each carrying its own `id`, plus
 * parent-relative `relations`. The renderer indexes the list by id, rebuilds
 * the tree from `parent_id` / `children`, and packs each parent's children
 * itself (see `layout.ts`) into rects that compose multiplicatively down the
 * tree into an absolute world rect for clip-and-scale drawing.
 *
 * Source of truth: `packages/server/src/repowise/server/schemas/zoom.py`.
 */

/** Node kinds, coarsest to finest. A leaf is always `file`. */
export type ZoomKind = "system" | "layer" | "group" | "folder" | "file";

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
  summary: string;
  language: string | null;
  /** Id of the module page documenting this folder, or "" when none does. */
  page_id: string;
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
  /** Files curation placed in no layer, so they are on no tree. Optional: an
   *  older server does not send it. */
  unclaimed_files?: number;
  max_depth: number;
  truncated: boolean;
  nodes: ZoomNode[];
  relations: ZoomRelation[];
}
