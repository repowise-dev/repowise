/**
 * Canonical doc/wiki types — wiki pages, freshness, coverage rollups.
 *
 * Canonical source: engine `PageResponse` (`packages/server/.../schemas.py`).
 * Some downstream backends type their `DocsResponse.pages` and
 * `CoverageResponse.pages` as `Array<Record<string, unknown>>` — consumer
 * adapters cast through the types below before passing data to components.
 */

export type FreshnessStatus = "fresh" | "stale" | "outdated" | string;

export interface DocPage {
  id: string;
  repository_id: string;
  page_type: string;
  title: string;
  content: string;
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
  metadata: Record<string, unknown>;
  human_notes: string | null;
  created_at: string;
  updated_at: string;
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
