/**
 * Repo-summary contract — the one-call payload behind the multi-repo
 * dashboard (`GET /api/repos/summary`). Mirrors the server's
 * `schemas/repository.py` response shape.
 */

/** Whether a registered repo has an index behind it yet. Same vocabulary as
 *  `RepoResponse.workspace_status`, which the sidebar already renders. */
export type RepoIndexStatus = "indexed" | "needs_index" | "missing_dir";

export interface RepoSummaryRow {
  id: string;
  name: string;
  local_path: string;
  updated_at: string | null;
  status: RepoIndexStatus;

  /** File nodes only. `/api/repos/{id}/stats` counts every `graph_nodes` row
   *  under this name, symbols included, which over-reports by roughly 10x. */
  file_count: number;
  symbol_count: number;
  entry_point_count: number;

  /** Documentation pages, and how many are still fresh. Both counts ship
   *  rather than a percentage, so a surface can print "3,797 of 4,059" and
   *  the ratio without the two disagreeing. */
  doc_page_count: number;
  doc_fresh_page_count: number;

  /** Open `unused_export` findings. Acknowledged and resolved ones are out,
   *  and so are the other dead-code kinds. */
  dead_export_count: number;

  /** Files carrying git history, and the hotspot subset of them. The
   *  denominator ships because a hotspot count means nothing without it. */
  tracked_file_count: number;
  hotspot_count: number;

  /** Latest health snapshot, 0-10. `null` means never analysed — which is a
   *  different thing from a score of 0, and must not render as one. */
  average_health: number | null;
  hotspot_health: number | null;
  health_taken_at: string | null;

  /** Index-vs-checkout freshness. `index_behind` is `null` when the
   *  comparison could not run (no git checkout, unreadable HEAD) rather than
   *  `false`, so "current" and "unknown" stay separable. */
  indexed_commit: string | null;
  live_head: string | null;
  index_behind: boolean | null;
}

export interface ReposSummaryResponse {
  repos: RepoSummaryRow[];
}
