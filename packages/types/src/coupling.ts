/**
 * Change-coupling (co-change) wire contract — shared by the web dashboard
 * (`packages/web`), the shared UI (`packages/ui`), and any future hosted
 * consumer. Mirrors the server's `routers/coupling.py` response shape.
 *
 * The graph is a pure surfacing of `GitMetadata.co_change_partners_json`: files
 * that have been committed together, deduplicated into an undirected edge list.
 * Co-change is a TEMPORAL hint (shared commits), not a verified code
 * dependency, and `strength` is a decay-weighted count — not a percentage.
 * `support` is the plain number of shared commits behind it, and the two
 * confidences read that against each file's own commit total, so they differ
 * whenever one file changes more often than the other. No
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
  /** Commits that touched both files, undecayed. `0` on an older index. */
  support: number;
  /** Share of `source`'s commits that also touched `target`; `null` if unknown. */
  confidence_ab: number | null;
  /** The same share from `target`'s side. */
  confidence_ba: number | null;
  /**
   * Whether the dependency graph explains the pair. `not_applicable` means a
   * side is not in the graph at all — a lockfile has no edge to find, so its
   * absence is not evidence of hidden coupling. `null` on an older index.
   */
  structural: CouplingStructure | null;
  /**
   * The graph edge behind a `corroborated` verdict -- `imports`, `type_use`,
   * `framework`, and so on. `null` for every other verdict, and on an index
   * written before the kind was recorded.
   */
  dependency_kind: string | null;
}

/** What the dependency graph says about a co-changing pair. */
export type CouplingStructure = "corroborated" | "unexplained" | "not_applicable";

/** Response of `GET /api/repos/{repo_id}/coupling`. */
export interface CouplingGraphResponse {
  nodes: CouplingNode[];
  edges: CouplingEdge[];
  /** Pre-cap edge count, for an honest "showing N of M couplings" line. */
  total_edges: number;
  /** Distinct files spanned by those pre-cap pairs, so the count has a scale. */
  coupled_files: number;
  /** Files with any commit history: the denominator `coupled_files` is a share of. */
  total_files: number;
}
