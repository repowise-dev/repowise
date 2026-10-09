/**
 * Pure selectors over the change-impact payload for the risk panel. Kept free
 * of React and the host runtime so they unit-test like src/shared/changeImpact.
 */

import type { ChangeImpactReport } from "../../../../src/shared/webviewMessages";

/**
 * Churn percentile above which a file earns the marker when the server is too
 * old to send `is_hotspot`.
 *
 * The marker read `temporal_hotspot >= 0.6`, calling that field a fraction. It
 * is an unbounded churn sum whose median is above 1.0 and whose maximum
 * exceeds 40, so the marker fired on most of a fast repo's tree and on none of
 * a slow one's. The index's own `is_hotspot` is the answer, and this quartile
 * is only its churn conjunct: it lacks the absolute activity floors, so on a
 * dormant repository it degenerates to "any file touched recently". Fall back
 * to it, do not prefer it.
 *
 * The per-file structural weight is centrality-weighted and unbounded, so rows
 * still carry a share relative to the riskiest file.
 */
export const HOTSPOT_FLOOR = 0.75;

/** One changed file ranked by its raw blast-radius structural weight. */
export interface RankedDirectRisk {
  /** Repo-relative path of the changed file. */
  path: string;
  /** Structural weight relative to the strongest changed file, 0 to 1. */
  share: number;
  /** True when git history marks the file as a temporal hotspot. */
  hotspot: boolean;
}

/** Ranks per-file structural weights for display, strongest first. */
export function selectDirectRisks(report: ChangeImpactReport): RankedDirectRisk[] {
  const risks = report.blast?.direct_risks ?? [];
  const max = risks.reduce((m, d) => Math.max(m, d.structural_score), 0);
  return risks
    .map((d) => ({
      path: d.path,
      share: max > 0 ? d.structural_score / max : 0,
      hotspot: d.is_hotspot ?? (d.churn_percentile ?? 0) >= HOTSPOT_FLOOR,
    }))
    .sort((a, b) => b.share - a.share);
}
