/**
 * The accent ramp, as CSS var references.
 *
 * One place so a stacked bar, a histogram and an area chart all step through
 * the same five values. The values themselves live in `styles/globals.css`,
 * per theme — see the comment there for why the light and dark ramps are not
 * the same mix percentages.
 *
 * Use `RAMP` for ordered shares of a whole, where position means magnitude.
 * For unrelated categories reach for `CATEGORICAL` instead: two hues the
 * token system already sanctions together, then neutrals. A qualitative
 * palette of five unrelated hues is what this exists to avoid.
 */

export const RAMP = [
  "var(--color-ramp-1)",
  "var(--color-ramp-2)",
  "var(--color-ramp-3)",
  "var(--color-ramp-4)",
  "var(--color-ramp-5)",
] as const;

export const NEUTRALS = [
  "var(--color-neutral-1)",
  "var(--color-neutral-2)",
  "var(--color-neutral-3)",
] as const;

/** The long tail past the end of the ramp: present, but not a step. */
export const RAMP_TAIL = "var(--color-bg-inset)";

/**
 * Ramp step for position `i`. Anything past the last step returns the tail,
 * so a caller can map an arbitrarily long sorted list without bounds checks
 * and without inventing colours it does not have.
 */
export function rampStep(i: number): string {
  return RAMP[i] ?? RAMP_TAIL;
}

/**
 * Two hues then neutrals, for a small set of unrelated categories.
 *
 * The pairing is not invented here: `--color-savings-distill` and
 * `--color-savings-mcp` are already accent-fill and accent-secondary, so the
 * token system has settled that these two read as distinct without either
 * standing in for a health band. Everything after position 2 recedes, which
 * is the honest encoding when the trailing categories are context rather
 * than subject.
 */
export const CATEGORICAL = [
  "var(--color-ramp-1)",
  "var(--color-accent-secondary)",
  ...NEUTRALS,
] as const;
