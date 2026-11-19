import type { WorkspaceRepoEntry } from "@repowise-dev/api-client/types";

export interface WorkspaceDocFreshness {
  fresh: number;
  pages: number;
  /** Fresh pages over all pages, 0-100; `null` with no pages. */
  pct: number | null;
}

/**
 * Doc freshness across the workspace: fresh pages over all pages.
 *
 * Not `doc_coverage_pct`: that is mean page confidence, which says how a page
 * was written, not whether it is current. Floored, not rounded: 99.7% rounds
 * to a perfect 100% and tells the reader every page is current when some are
 * not. Erring low never overclaims.
 */
export function workspaceDocFreshness(
  repos: Pick<WorkspaceRepoEntry, "page_count" | "fresh_page_count">[],
): WorkspaceDocFreshness {
  let fresh = 0;
  let pages = 0;
  for (const r of repos) {
    fresh += r.fresh_page_count;
    pages += r.page_count;
  }
  return { fresh, pages, pct: pages > 0 ? Math.floor((fresh / pages) * 100) : null };
}
