/**
 * Shared copy and framing for change risk and diff shape.
 *
 * The change-risk percentile ranks a commit or range among recent commits by
 * diff size and spread. It is a diff-size statistic, not a bug predictor.
 * Fix history reports the recency-weighted bug-fix record of the files
 * touched, without printing a competing percentile beside the size rank.
 */

export const SIZE_AND_SPREAD_LABEL = "Size and spread";
export const SIZE_AND_SPREAD_QUALIFIER = "diff size, not a bug predictor";
export const COMMITS_FILTER_LABEL = "Size, spread and fixes";
export const HIGH_SIZE_AND_SPREAD_LABEL = "High size and spread";

export const LIMITS =
  "The rank is a diff-size statistic: it barely beats churn alone and cannot rank danger.";

export const TERCILES: Record<string, string> = {
  high: "sits in the top third of this repo's own diff-shape distribution by size and spread",
  moderate:
    "sits in the middle third of this repo's own diff-shape distribution by size and spread",
  low: "sits in the bottom third of this repo's own diff-shape distribution by size and spread",
};

/**
 * Explains where the commit sits in the repository's diff-shape distribution.
 */
export function formatSizeSentence(reviewPriority?: string | null): string {
  return `This commit ${TERCILES[reviewPriority ?? "moderate"] ?? TERCILES.moderate}.`;
}

/**
 * Reconciles size/spread and fix history when the two signals diverge,
 * without printing a secondary percentile for fix history.
 */
export function reconcileSizeAndFixHistory(
  riskPercentile: number,
  fixPercentile: number | null,
): string | null {
  if (fixPercentile == null) return null;
  const gap = fixPercentile - riskPercentile;
  if (Math.abs(gap) < 25) return null;
  return gap > 0
    ? "Ordinary in size, but it lands in files with a heavy fix record. That is the signal worth reading here."
    : "Large for this repo, but it lands in files that have rarely broken.";
}
