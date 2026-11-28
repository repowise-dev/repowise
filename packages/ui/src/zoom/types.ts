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
  /** Up to three code languages / imported frameworks of the subtree, most
   *  files first (e.g. `["TypeScript", "React", "Next"]`). Empty on file
   *  leaves, which carry `language`. Optional: an older server omits it. */
  technologies?: string[];
  /** Ids of the `ZoomMap.containers` this subtree's files belong to, most files
   *  first. One id = the node lies wholly inside that container; several =
   *  it spans them (curated layers cut across deployables). Optional. */
  container_ids?: string[];
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

/** Kind of an external system: its boundary type when typed, else its
 *  dependency category. Open-ended so a new detector value still parses. */
export type ZoomExternalKind = "db" | "network" | "service" | (string & {});

/**
 * A third-party system the code reaches (a database, a remote API, a SaaS),
 * drawn beside the tree rather than inside it. `ZoomMap.external_relations`
 * link tree nodes to it at every zoom level.
 */
export interface ZoomExternal {
  /** `ext:<package name>`; the `target_id` of its external relations. */
  id: string;
  name: string;
  display_name: string;
  kind: ZoomExternalKind;
  /** service | framework | library | tool */
  category: string;
  /** db | network, or null when the package is untyped. */
  io_kind: string | null;
  /** pypi | npm | cargo | ... */
  ecosystem: string;
  /** Distinct production files importing it, repository-wide (not narrowed
   *  by `focus` / `max_depth`; count the served `external_relations` for that). */
  file_count: number;
  /** Import edges reaching it, repository-wide. */
  edge_count: number;
}

/** What a container is, from what it imports and how it is run. */
export type ZoomContainerKind =
  | "extension"
  | "web_app"
  | "api"
  | "cli"
  | "worker"
  | "library"
  | (string & {});

/** A deployable unit (C4 container): a package-manifest root. */
export interface ZoomContainer {
  /** `pkg:<root path>` (`pkg:.` for the repository root). */
  id: string;
  /** Manifest package name, else the root path. */
  name: string;
  /** Repo-relative root; "" for the repository root. */
  path: string;
  kind: ZoomContainerKind;
  /** Up to three code languages / imported frameworks, most files first. */
  technologies: string[];
  /** Its files on the whole tree, not narrowed by `focus` / `max_depth`. */
  file_count: number;
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
  /** External systems the code reaches. Optional: an older server omits it. */
  externals?: ZoomExternal[];
  /** Node -> external links, shaped like `relations` except `target_id` is a
   *  `ZoomExternal.id`: `source_id` is a child of `parent_id` whose subtree
   *  imports it, so zoomed into `parent_id` you draw child -> external. */
  external_relations?: ZoomRelation[];
  /** Deployables the served nodes belong to. Optional. */
  containers?: ZoomContainer[];
}
