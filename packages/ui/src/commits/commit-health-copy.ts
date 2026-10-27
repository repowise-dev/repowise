/**
 * What one commit did to code health, read the same way by the detail sheet
 * and the fix prompt. Pure, so tests pin the rules (counts only from a current
 * analysis, never-scanned is not clean).
 */
import type { CommitHealth } from "@repowise-dev/types/git";
import type { HealthDeltaSummaryResponse } from "@repowise-dev/types/history";
import {
  betterCount,
  healthDeltaKind,
  worseCount,
  type HealthDeltaCounts,
  type HealthDeltaKind,
} from "./health-delta";

/** The commit's delta: the list row's summary, else the detail's counts;
 *  null when the commit was never analysed. */
export function commitDelta(
  delta: HealthDeltaSummaryResponse | null | undefined,
  health: CommitHealth | null | undefined,
): HealthDeltaCounts | null {
  if (delta) return { ...delta, stale: delta.stale === true || health?.stale === true };
  if (!health) return null;
  return {
    introduced: health.introduced_count,
    worsened: health.worsened_count,
    resolved: health.resolved_count,
    status: health.status,
    stale: health.stale === true,
  };
}

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
  const d = commitDelta(delta, health);
  if (!d) return { kind: null, stale: false, better: 0, worse: 0 };
  return { kind: healthDeltaKind(d), stale: d.stale === true, better: betterCount(d), worse: worseCount(d) };
}

/** "Got better" description: findings resolved, and (once stored deltas
 *  carry it) findings made less severe. */
export function betterSentence(d: HealthDeltaCounts): string {
  const improved = d.improved ?? 0;
  const parts = [
    d.resolved > 0 ? `Resolved ${d.resolved} existing finding${d.resolved === 1 ? "" : "s"}.` : null,
    improved > 0 ? `Made ${improved} finding${improved === 1 ? "" : "s"} less severe.` : null,
  ].filter((p): p is string => p !== null);
  return `${parts.join(" ")} A finding that got better has no new location to point at, so it is counted here.`;
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
