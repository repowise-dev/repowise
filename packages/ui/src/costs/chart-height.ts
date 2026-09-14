/**
 * Chart geometry, shared so a skeleton and the chart it stands in for cannot
 * disagree. A literal height in a placeholder is a reflow waiting to drift:
 * the daily-spend skeleton was `h-48` (192px) against a 220px chart, which
 * snapped the page down 28px on every load.
 */

/** Default plot height for the fixed-height cost charts. */
export const CHART_HEIGHT = 220;

/**
 * The operation-breakdown chart grows with its rows, so its height is a
 * function rather than a constant. A placeholder that does not know the row
 * count should render at `operationBreakdownHeight(0)`, the floor, and let
 * the chart grow downward rather than reserving a guess that is usually wrong.
 */
export function operationBreakdownHeight(rows: number): number {
  return Math.max(180, rows * 24 + 40);
}
