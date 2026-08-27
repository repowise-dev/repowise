/**
 * Pure selectors over the change-impact payload for the risk panel. Kept free
 * of React and the host runtime so they unit-test like src/shared/changeImpact.
 */

import type { ChangeImpactReport } from "../../../../src/shared/webviewMessages";

/**
 * Temporal-hotspot fraction above which a file earns the quiet hotspot marker.
 * The per-file structural weight is centrality-weighted and unbounded, so rows
 * carry a share relative to the riskiest file instead of an absolute scale.
 */
export const HOTSPOT_FLOOR = 0.6;

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
      hotspot: d.temporal_hotspot >= HOTSPOT_FLOOR,
    }))
    .sort((a, b) => b.share - a.share);
}
