import type { ReactNode } from "react";
import type { BlastRadiusResponse } from "@repowise-dev/types/blast-radius";

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
  /** Run the blast-radius analysis for the proposed change. */
  analyze(input: {
    changedFiles: string[];
    maxDepth: number;
  }): Promise<BlastRadiusResponse>;

  /**
   * Rich reviewer panel for the analyzed changeset. Kept as a slot so each app
   * supplies its own reviewer fetch (or omits it entirely — hosted may have no
   * reviewer endpoint, which degrades to no panel rather than an error).
   */
  renderReviewers?(changedFiles: string[]): ReactNode;
}
