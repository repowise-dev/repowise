/**
 * The welcome: when it opens, and what its first step shows.
 *
 * It opens by itself once per repo per machine, on the overview, after a
 * first index, for a browser that started out with no repowise use. An
 * existing user who upgrades never gets it on their own; they hear about the
 * new setup through What's new and can take the tour from Setup. It shares
 * the release moment's session guard, so at most one of them interrupts a
 * session. `?tour=1` opens it on demand past every guard.
 */

import type { HealthBand } from "@repowise-dev/types/health";
import type { OverviewSummaryResponse } from "@repowise-dev/types/overview";

export type WelcomeOpen = "none" | "auto" | "tour";

export interface WelcomeOpenInput {
  /** `window.location.search`, or the route's search params as a string. */
  search: string;
  fileCount: number;
  /** The server's per-machine mark for this repo; null while unknown. */
  seen: boolean | null;
  /** `isNewBrowser()`: no repowise use before this browser's first visit. */
  newBrowser: boolean;
  /** `sessionMomentTaken()`: What's new or a patch note already showed. */
  sessionTaken: boolean;
}

/** Whether the welcome opens now, and how. */
export function decideWelcome(input: WelcomeOpenInput): WelcomeOpen {
  if (new URLSearchParams(input.search).get("tour") === "1") return "tour";
  if (input.fileCount <= 0 || input.seen !== false) return "none";
  if (!input.newBrowser || input.sessionTaken) return "none";
  return "auto";
}

// --- Step 1: the lens cards ---------------------------------------------------

/** What the overview already loaded, read on the server. */
export interface WelcomeFigures {
  health: {
    score: number;
    band: HealthBand | null;
    worstPath: string | null;
    worstScore: number | null;
  } | null;
  /** Decision candidates awaiting review; null when the store is absent. */
  decisionsToReview: number | null;
  docPages: number | null;
  /** Measured line coverage from the latest report, when there is one. */
  coveragePct: number | null;
}

/** Step 1's figures from the overview payload. Absent fields stay null, so
 *  their cards drop out. */
export function welcomeFigures(summary: OverviewSummaryResponse): WelcomeFigures {
  const { health, stats, explore } = summary;
  return {
    health:
      health.average_health != null
        ? {
            score: health.average_health,
            band: health.band ?? null,
            worstPath: health.worst_performer_path,
            worstScore: health.worst_performer_score,
          }
        : null,
    decisionsToReview: explore?.decisions?.candidates ?? null,
    docPages: stats.doc_page_count ?? null,
    coveragePct: explore?.coverage?.line_coverage_pct ?? null,
  };
}

/** Fetched when the welcome opens; each is null when it could not be read. */
export interface WelcomeExtras {
  deadCode: { files: number; safe: number } | null;
  risk: { path: string; reached: number } | null;
  testsReached: { reached: number; total: number } | null;
}

export type WelcomeLens =
  | { key: "health"; score: number; band: HealthBand | null; worstPath: string | null; worstScore: number | null }
  | { key: "deadCode"; files: number; safe: number }
  | { key: "risk"; path: string; reached: number }
  | { key: "decisions"; count: number }
  | { key: "docs"; pages: number }
  | { key: "tests"; measuredPct: number }
  | { key: "testsInferred"; reached: number; total: number };

/**
 * The cards step 1 shows, in the design's order. A lens whose value was not
 * measured is left out, never shown as zero.
 */
export function welcomeLenses(figures: WelcomeFigures, extras: WelcomeExtras): WelcomeLens[] {
  const lenses: WelcomeLens[] = [];
  if (figures.health) lenses.push({ key: "health", ...figures.health });
  if (extras.deadCode) lenses.push({ key: "deadCode", ...extras.deadCode });
  if (extras.risk && extras.risk.reached > 0) lenses.push({ key: "risk", ...extras.risk });
  if (figures.decisionsToReview != null) lenses.push({ key: "decisions", count: figures.decisionsToReview });
  if (figures.docPages) lenses.push({ key: "docs", pages: figures.docPages });
  if (figures.coveragePct != null) lenses.push({ key: "tests", measuredPct: figures.coveragePct });
  else if (extras.testsReached && extras.testsReached.total > 0) {
    lenses.push({ key: "testsInferred", ...extras.testsReached });
  }
  return lenses;
}

/** Whole years between the first commit and now; null under a year or unknown. */
export function historyYears(ageDays: number | null | undefined): number | null {
  if (ageDays == null) return null;
  const years = Math.floor(ageDays / 365);
  return years >= 1 ? years : null;
}
