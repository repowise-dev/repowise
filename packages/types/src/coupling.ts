/**
 * Change-coupling (co-change) wire contract — shared by the web dashboard
 * (`packages/web`), the shared UI (`packages/ui`), and any future hosted
 * consumer. Mirrors the server's `routers/coupling.py` response shape.
 *
 * The graph is a pure surfacing of `GitMetadata.co_change_partners_json`: files
 * that have been committed together, deduplicated into an undirected edge list.
 * Co-change is a TEMPORAL hint (shared commits), not a verified code
 * dependency, and `strength` is a decay-weighted count — not a percentage. No
 * "strengthening/weakening" trend is carried because co-change history is not
 * snapshotted; only magnitude and recency are honest signals.
 */

/** One file that participates in at least one coupling. */
export interface CouplingNode {
  file_path: string;
  /** Module grouping for the legend/table; `null` when the file has no health metric. */
  module: string | null;
  /** Health score (drives the band dot color); `null` for a file with no health metric. */
  score: number | null;
  /** Logical lines of code; encodes dot size. */
  nloc: number;
}

/** One undirected coupling between two files. */
export interface CouplingEdge {
  /** Lexicographically-smaller file path (stable, deduplicated pair). */
  source: string;
  /** Lexicographically-larger file path. */
  target: string;
  /** Decay-weighted co-change count (verbatim from the indexer; not a percentage). */
  strength: number;
  /** ISO date of the most recent shared commit, or `null` if unknown. */
  last_co_change: string | null;
}

/** Response of `GET /api/repos/{repo_id}/coupling`. */
export interface CouplingGraphResponse {
  nodes: CouplingNode[];
  edges: CouplingEdge[];
  /** Pre-cap edge count, for an honest "showing N of M couplings" line. */
  total_edges: number;
}
