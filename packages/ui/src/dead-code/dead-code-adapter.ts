import type {
  DeadCodeFinding,
  DeadCodePatchInput,
  DeadCodeSummary,
} from "@repowise-dev/types/dead-code";

/**
 * App-injected data + navigation for the shared {@link DeadCodeView}.
 *
 * The view owns the composition — the safe-to-delete pile, the cluster
 * rollups, the drill-down table, the optimistic patch/undo toast, bulk
 * resolve, the "Propose cleanup" agent brief, and Re-analyze. The host
 * supplies *how* to fetch/mutate and *where* file links go, so web and hosted
 * render the same view from one source.
 *
 * Fetch methods are keyed off `cacheKey` for SWR, so a page-level fetch that
 * shares a key dedupes onto the same request.
 */
export interface DeadCodeAdapter {
  /** Seeds the view's SWR cache keys — keep it stable per repo/snapshot. */
  cacheKey: string;
  /** Repo base used by the findings table to build per-row action links. */
  repoId: string;

  getSummary(): Promise<DeadCodeSummary>;
  listFindings(opts?: { limit?: number }): Promise<DeadCodeFinding[]>;
  /** Kick off a fresh analysis pass (host owns auth + job dispatch). */
  analyze(): Promise<void>;
  patchFinding(
    findingId: string,
    patch: DeadCodePatchInput,
  ): Promise<DeadCodeFinding>;

  /** Build an href to a file detail page. */
  fileHref(path: string): string;
  /** Navigate to an href (host wires this to its router). */
  navigate(href: string): void;
}
