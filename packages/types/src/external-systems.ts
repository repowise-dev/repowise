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
