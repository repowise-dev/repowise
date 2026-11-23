/**
 * A period's health story, read from the summary endpoints
 * (`/commits/health-summary`, `/changes/summary`). Pure, so the headline and
 * the quick-filter counts beside it say the same numbers.
 */
import type { PeriodHealthSummaryResponse } from "@repowise-dev/types/history";

export type PeriodHealthTone = "good" | "mixed" | "quiet" | "catchingUp" | "empty";

export interface PeriodHealthRead {
  /** Items that made anything better, including those that also made
   *  something worse: what the "Made things better" filter returns. */
  better: number;
  /** Items that made anything worse, including those that also improved. */
  worse: number;
  /** Items counted in both of the above. */
  both: number;
  /** Analysed items that changed no finding. */
  neutral: number;
  /** Items with no current analysis ("not yet analysed"). */
  unscanned: number;
  /** Items with a current analysis. */
  analysed: number;
  total: number;
  /** Findings made better: resolved, plus improved once stored deltas carry it. */
  findingsBetter: number;
  /** Findings made worse: introduced plus worsened. */
  findingsWorse: number;
  /** `good` when something improved and nothing got worse; `quiet` when the
   *  analysed work changed nothing; `catchingUp` when most of the window is
   *  not analysed by the current analyzer yet (after an upgrade, or older
   *  history the bounded scan has not reached), so counts would mislead;
   *  `empty` when the window holds nothing. */
  tone: PeriodHealthTone;
}

export function readPeriodHealth(s: PeriodHealthSummaryResponse): PeriodHealthRead {
  const better = s.improved + s.mixed;
  const worse = s.worsened + s.mixed;
  const analysed = s.improved + s.mixed + s.worsened + s.neutral;
  const tone: PeriodHealthTone =
    s.total === 0
      ? "empty"
      : s.unscanned * 2 > s.total
        ? "catchingUp"
        : worse === 0
          ? better > 0
            ? "good"
            : "quiet"
          : "mixed";
  return {
    better,
    worse,
    both: s.mixed,
    neutral: s.neutral,
    unscanned: s.unscanned,
    analysed,
    total: s.total,
    findingsBetter: s.findings_resolved + (s.findings_improved ?? 0),
    findingsWorse: s.findings_introduced + s.findings_worsened,
    tone,
  };
}
