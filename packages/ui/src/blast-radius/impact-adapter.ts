import type { ReactNode } from "react";
import type {
  AffectedSignal,
  BlastRadiusResponse,
  ChangeBlastRadiusResponse,
  ChangeCandidatesResponse,
  ChangeHealthCheckResponse,
} from "@repowise-dev/types/blast-radius";

/** A hotspot suggestion chip — only the path is rendered. */
export interface ImpactHotspot {
  file_path: string;
}

/**
 * App-injected data + slots for the shared {@link ImpactView}.
 *
 * The view owns the analyzer composition — the searchable file picker, hotspot
 * chips, depth control, and the blast-radius results. The host supplies *how*
 * to fetch/analyze and an optional reviewer panel, so web and hosted render the
 * same view from one source.
 */
export interface ImpactAdapter {
  /** Seeds the view's SWR cache keys — keep it stable per repo/snapshot. */
  cacheKey: string;

  /** Top churn/risk hotspots offered as one-click changed-file chips. */
  listHotspots(limit: number): Promise<ImpactHotspot[]>;
  /** Typeahead source for the file picker — resolves a query to candidate paths. */
  searchFiles(query: string): Promise<string[]>;
  /**
   * Run the blast-radius analysis for a hand-picked set of files. A server
   * that returns `affected_signals` gets health and hotspot marks on the map.
   */
  analyze(input: {
    changedFiles: string[];
    maxDepth: number;
  }): Promise<BlastRadiusResponse & { affected_signals?: AffectedSignal[] }>;

  /**
   * Changes to pick from: work in progress, branches, recent commits. With
   * this and {@link analyzeChange}, the view leads with the list and opens the
   * most relevant change; without them it falls back to picking files.
   */
  listChanges?(): Promise<ChangeCandidatesResponse>;
  /** Analyze one change from {@link listChanges} by its id. */
  analyzeChange?(input: { changeId: string; maxDepth: number }): Promise<ChangeBlastRadiusResponse>;

  /**
   * Run `repowise health check` on a listed change. Needs a checkout, so a
   * host without one leaves it out and the view shows no health check.
   */
  checkHealth?(changeId: string): Promise<ChangeHealthCheckResponse>;

  /** Where a file on the map or in the verdict links to; omit for plain text. */
  fileHref?(path: string): string;

  /**
   * @deprecated Not rendered: the review's "Who to ask" block names the
   * changed files' owners from the analysis itself, so owners show once.
   * Kept so a host that still passes it type-checks.
   */
  renderReviewers?(changedFiles: string[]): ReactNode;
}
