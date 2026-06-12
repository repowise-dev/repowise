/**
 * Dependency-registry types — the `external_systems` table populated by the
 * manifest parsers during ingestion. Mirrors the Pydantic models served by
 * `/api/repos/{id}/external-systems`.
 */

/** One declared third-party dependency. */
export interface ExternalSystemEntry {
  name: string;
  display_name: string;
  /** npm | pypi | cargo | gomod | nuget | ... */
  ecosystem: string;
  /** framework | service | tool | library */
  category: string;
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
