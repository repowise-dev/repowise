/**
 * The one categorical palette for every chart: `--color-series-*` in
 * globals.css. Literal strings so Tailwind's scan keeps the tokens.
 */
export const SERIES = [
  "var(--color-series-1)", // orange
  "var(--color-series-2)", // plum
  "var(--color-series-3)", // blue
  "var(--color-series-4)", // olive
  "var(--color-series-5)", // rose
  "var(--color-series-6)", // gold
  "var(--color-series-7)", // brick
  "var(--color-series-8)", // slate
] as const;

/** The grey for an unnamed remainder ("Other"): present, but not a series. */
export const SERIES_TAIL = "var(--color-share-tail)";

/** Fill for series `i`, wrapping past the last colour. */
export function seriesColor(i: number): string {
  const n = SERIES.length;
  return SERIES[((Math.trunc(i) % n) + n) % n]!;
}

/**
 * Commit categories keep one colour on every chart that shows them, so Fix
 * is the same orange on a donut, a share bar and the evolution chart.
 */
const COMMIT_CATEGORY_SERIES: Record<string, number> = {
  fix: 0,
  feature: 1,
  refactor: 2,
  docs: 3,
  test: 4,
  deps: 5,
  dependency: 5,
  chore: 7,
};

/** Fill for a commit category; unknown or "other" takes the tail grey. */
export function commitCategoryColor(category: string): string {
  const i = COMMIT_CATEGORY_SERIES[category.toLowerCase()];
  return i === undefined ? SERIES_TAIL : seriesColor(i);
}
