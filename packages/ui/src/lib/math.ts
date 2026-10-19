/**
 * Small numeric helpers shared across repowise UI surfaces.
 */

/**
 * Median of a numeric list, 0 for an empty one. An even count averages the two
 * middle values, so two charts splitting the same data draw the same line.
 */
export function median(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[mid - 1]! + sorted[mid]!) / 2 : sorted[mid]!;
}
