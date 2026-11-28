/**
 * Better/worse history: what a commit or change did to code health, a period
 * headline that counts improvements first, and the wins in a window.
 *
 * Generated from the server's response models
 * (`packages/server/src/repowise/server/schemas/history.py`), so the wire
 * shape cannot drift. A `kind` reads the counts: `improved` only made things
 * better, `worsened` only worse, `mixed` both, `neutral` neither. A null delta
 * or rollup means not yet analysed, which is not the same claim as `neutral`.
 * A row's `stale` delta came from an older analyzer: show its direction, not
 * its counts. Aggregates count current-analyzer rows only. `improved` is null
 * until stored deltas carry it; `files` stays empty for stored deltas.
 */
export type {
  ChangeHealthFindingResponse,
  ChangeHealthResponse,
  FileScoreShiftResponse,
  HealthDeltaSummaryResponse,
  HealthRollupResponse,
  PeriodHealthSummaryResponse,
  WinsFileGain,
  WinsFilesImproved,
  WinsFindingsResolved,
  WinsRefactoring,
  WinsRefactorings,
  WinsResolvingCommit,
  WinsResponse,
} from "./generated/http.js";

/** Values the `delta` filter takes; `improved` and `worsened` include `mixed`. */
export const DELTA_FILTERS = ["improved", "worsened", "mixed", "neutral"] as const;
export type DeltaFilter = (typeof DELTA_FILTERS)[number];

/** History filters shared by the commit and change lists and their summaries. */
export interface HistoryFilters {
  /** Author email (any case) or exact name. */
  author?: string;
  /** A directory or file path; keeps work touching it. */
  area?: string;
  /** ISO 8601, inclusive. */
  since?: string;
  /** ISO 8601, exclusive. */
  until?: string;
  delta?: DeltaFilter;
  touches_hotspot?: boolean;
}
