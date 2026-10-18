/**
 * What one commit did to code health, in sentences: the detail sheet's
 * verdict, "Got better" and "Got worse". Pure, so tests pin the wording rules
 * (counts only from a current analysis, never-scanned is not clean).
 */
import type { CommitHealth } from "@repowise-dev/types/git";
import type { HealthDeltaSummaryResponse } from "@repowise-dev/types/history";
import { betterCount, healthDeltaKind, worseCount, type HealthDeltaKind } from "./health-delta";

export interface CommitHealthRead {
  /** Null when the commit has no analysis at all. */
  kind: HealthDeltaKind | null;
  /** From an earlier analyzer version: direction only, no counts. */
  stale: boolean;
  better: number;
  worse: number;
}

export function readCommitHealth(
  delta: HealthDeltaSummaryResponse | null | undefined,
  health: CommitHealth | null | undefined,
): CommitHealthRead {
  const stale = delta?.stale === true || health?.stale === true;
  if (delta) {
    return { kind: healthDeltaKind(delta), stale, better: betterCount(delta), worse: worseCount(delta) };
  }
  if (health) {
    const counts = {
      introduced: health.introduced_count,
      worsened: health.worsened_count,
      resolved: health.resolved_count,
    };
    return { kind: healthDeltaKind(counts), stale, better: betterCount(counts), worse: worseCount(counts) };
  }
  return { kind: null, stale, better: 0, worse: 0 };
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** The verdict's sentence. Improvements are named before regressions. */
export function verdictSentence(r: CommitHealthRead): string {
  if (r.kind === null) {
    return "This commit has not been analysed for code health yet, so the review priority below is the only signal.";
  }
  if (r.stale) {
    const way = { improved: "better", worsened: "worse", mixed: "better in places and worse in others", neutral: "neither better nor worse" }[r.kind];
    return `An earlier analysis read this commit as making health ${way}. The counts are not compared with the current analysis.`;
  }
  switch (r.kind) {
    case "improved":
      return `Made health better: ${plural(r.better, "finding")} resolved, nothing made worse.`;
    case "worsened":
      return `Made health worse: ${plural(r.worse, "finding")} introduced or made more severe.`;
    case "mixed":
      return `Made ${plural(r.better, "finding")} better and ${plural(r.worse, "finding")} worse.`;
    default:
      return "Compared clean: nothing introduced, worsened or resolved.";
  }
}

/** "Got worse" description: the counts, and what the list cannot show. */
export function worseSentence(h: CommitHealth): string {
  const made = h.introduced_count + h.worsened_count;
  const parts = [
    h.introduced_count > 0 && h.worsened_count > 0
      ? `${h.introduced_count} new, ${h.worsened_count} made more severe.`
      : null,
    h.findings.length < made ? `Showing the ${h.findings.length} most severe of ${made}.` : null,
    h.files_skipped > 0
      ? `${h.files_skipped} of ${h.files_analyzed + h.files_skipped} changed files could not be analysed.`
      : null,
  ].filter((p): p is string => p !== null);
  return parts.length > 0 ? parts.join(" ") : "Each one at the file and line where it sits.";
}
