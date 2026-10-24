/**
 * The estate report for a workspace: `GET /api/workspace/estate`, the same
 * document `repowise report estate --format json` prints.
 *
 * The envelope is generated from the server's response models
 * (`packages/server/src/repowise/server/schemas/workspace.py`). Each check's
 * `data` is free-form on the wire; the interfaces below name the keys the
 * estate sections write (`packages/core/src/repowise/core/report/sections/estate_*.py`)
 * for the checks a page reads as tables.
 */
export type {
  WorkspaceEstateCheck as EstateCheck,
  WorkspaceEstateRepo as EstateRepo,
  WorkspaceEstateResponse as EstateReport,
  WorkspaceEstateSection as EstateSection,
} from "./generated/http.js";

/** `estate-summary` / `repositories`: `data.repositories`. */
export interface EstateRepositoryRow {
  alias: string;
  indexed: boolean;
  indexed_commit: string | null;
  /** The commit contracts were last read at; differs from `indexed_commit` when stale. */
  contracts_commit: string | null;
  services: string[];
  /** Set when an index exists but was not read: written by an older repowise, or unreadable. */
  index_state?: "behind" | "unreadable" | null;
}

/** `estate-health` / `health`: `data.repositories`, lowest health first. */
export interface EstateHealthRow {
  repo: string;
  /** Average file health, 1-10. */
  average_health: number;
  maintainability_average: number | null;
  hotspot_health: number | null;
  taken_at: string | null;
  store_commit: string | null;
  index_commit: string | null;
}

/** `estate-health` / `churn-hotspots` and `bug-magnets`: `data.repositories[alias]`. */
export interface EstateHistoryRow {
  commits: number;
  newest_commit: string | null;
  files: number;
  hotspots?: number;
  top_hotspots?: string[];
  bug_magnets?: number;
  top_bug_magnets?: { file: string; fixes: number; last_fix: string | null }[];
  files_with_fixes?: number;
}

/** `estate-map` / `dependencies`: `data.edges`, a system-graph edge. */
export interface EstateEdge {
  source: string;
  target: string;
  kind: string;
  weight: number;
  confidence: number;
  match_type: string;
  structural: boolean;
  strategy?: string | null;
}

/** `estate-contracts` / `dangling-calls`: `data.dangling`. */
export interface EstateDanglingCall {
  consumer: string;
  file: string;
  line: number | null;
  contract_id: string;
  type: string;
  /** `no_provider` or `unlinked`. */
  reason: string;
  target: string | null;
  external: boolean;
}

/** `estate-seams` / `seams`: `data.seams`, highest score first. */
export interface EstateSeam {
  services: [string, string];
  /** `->` (first depends on second), `<->` (both ways) or `~` (co-change only). */
  direction: string;
  kinds: string[];
  confidence: number | null;
  reach: number;
  co_change: number | null;
  declared: boolean;
  undeclared_co_change: number | null;
  score: number | null;
  /** The score left out co-change that could only raise it. */
  score_is_floor: boolean;
  missing: string[];
}

/** `estate-start` / `start-files`: `data.repositories[alias]`. */
export interface EstateStartFile {
  path: string;
  pagerank: number;
  /** Words on the file's written page; 0 when only a page rendered from code exists. */
  doc_words: number;
}
