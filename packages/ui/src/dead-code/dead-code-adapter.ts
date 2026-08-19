import type {
  DeadCodeFinding,
  DeadCodePatchInput,
  DeadCodeStatus,
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
  /**
   * List findings. `status` defaults to `"open"` server-side; the view passes
   * it explicitly so an acknowledged or false-positive finding can be reviewed
   * and reopened, which was otherwise only possible from a toast that expires
   * after six seconds.
   */
  listFindings(opts?: { limit?: number; status?: DeadCodeStatus }): Promise<DeadCodeFinding[]>;
  /**
   * Kick off a fresh analysis pass (host owns auth + job dispatch). Returning
   * the job id lets the view wait for the pass and refresh itself; hosts that
   * cannot observe the job may return nothing, and the view falls back to
   * "results will appear shortly".
   */
  analyze(): Promise<{ job_id?: string } | void>;
  /**
   * Resolve once the analysis job reaches a terminal state, rejecting if it
   * fails. Optional: without it the view cannot know when to refetch.
   */
  waitForAnalysis?(jobId: string): Promise<void>;
  patchFinding(
    findingId: string,
    patch: DeadCodePatchInput,
  ): Promise<DeadCodeFinding>;

  /** Build an href to a file detail page. */
  fileHref(path: string): string;
  /**
   * Build an href to the dependency graph focused on a file. Optional: hosts
   * without a graph surface omit it and the row action disappears, which keeps
   * the app's route map out of this package.
   */
  graphHref?(path: string): string;
  /** Navigate to an href (host wires this to its router). */
  navigate(href: string): void;
}
