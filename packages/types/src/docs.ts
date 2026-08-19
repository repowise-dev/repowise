/**
 * Canonical doc/wiki types — wiki pages, freshness, coverage rollups.
 *
 * Canonical source: engine `PageResponse` (`packages/server/.../schemas.py`).
 * Some downstream backends type their `DocsResponse.pages` and
 * `CoverageResponse.pages` as `Array<Record<string, unknown>>` — consumer
 * adapters cast through the types below before passing data to components.
 */

export type FreshnessStatus = "fresh" | "stale" | "outdated" | (string & {});

/**
 * A page without its body or its metadata blob.
 *
 * This is what a *list* of pages should be. On a large wiki the two omitted
 * fields are 95% of a full listing, and nothing that renders a list — the docs
 * tree, breadcrumbs, the command palette, the path index — reads either one.
 * Anything that shows a page's body takes `DocPage` instead and fetches it.
 */
export interface DocPageSummary {
  id: string;
  repository_id: string;
  page_type: string;
  title: string;
  target_path: string;
  source_hash: string;
  model_name: string;
  provider_name: string;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  generation_level: number;
  version: number;
  confidence: number;
  freshness_status: FreshnessStatus;
  /**
   * Length of the omitted `content`, so a list can still rank pages by how
   * much was written without carrying the writing. Optional: a backend older
   * than this field simply doesn't send it, and callers fall back rather than
   * ranking on `undefined`.
   */
  content_chars?: number;
  /**
   * Present only when this row was fetched in full. A list is free to hydrate
   * a few of its rows — the docs page does exactly that for cycle pages, whose
   * label is parsed out of the body — so readers must handle its absence
   * rather than assume a body is there.
   */
  content?: string;
  metadata?: Record<string, unknown>;
  /**
   * Which layer of the architecture spine this page belongs to, stamped at
   * generation time. Promoted out of `metadata` because a *list* needs it: the
   * docs tree groups modules under their layer from this stamp, and a listing
   * drops the metadata blob. Absent or `null` means no layer claimed the page
   * — a repo with no curated spine, or a page the grouping leaves where it is.
   */
  layer_id?: string | null;
  layer_name?: string | null;
  /**
   * Whether this module page heads a chapter: a subsystem's landing page,
   * with the module pages of its directory nested under it.
   *
   * Promoted out of `metadata` for the same reason as the layer stamp: a
   * chapter's `page_type` is `module_page`, exactly like the pages beneath it,
   * so a listing that drops the blob cannot tell a chapter from an ordinary
   * module that happens to have children. Absent on every page written before
   * chapters shipped, which reads as `false`.
   */
  is_chapter?: boolean;
  human_notes: string | null;
  /**
   * Position in the wiki outline, computed once at generation time so every
   * reader navigates the same tree. Optional: pages written before the wiki
   * carried a tree have no placement, which reads as flat.
   */
  parent_page_id?: string | null;
  display_order?: number;
  section_number?: string | null;
  structural_key?: string | null;
  created_at: string;
  updated_at: string;
}

export interface DocPage extends DocPageSummary {
  content: string;
  metadata: Record<string, unknown>;
}

export interface DocPageVersion {
  id: string;
  page_id: string;
  version: number;
  page_type: string;
  title: string;
  content: string;
  source_hash: string;
  model_name: string;
  provider_name: string;
  input_tokens: number;
  output_tokens: number;
  confidence: number;
  archived_at: string;
}

export interface DocPageList {
  pages: DocPage[];
  total: number;
}

export interface CoverageRollup {
  available: boolean;
  total_pages: number;
  fresh: number;
  stale: number;
  outdated: number;
  pages: DocPage[];
}
