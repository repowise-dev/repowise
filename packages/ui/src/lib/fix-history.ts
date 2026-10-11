import { formatRelativeTimeOrNull } from "./format";

export interface FixHistorySummary {
  /** Counted bug fixes in the trailing defect window. Always > 0. */
  count: number;
  /** "3 fixes" / "1 fix" — safe to show on its own. */
  countLabel: string;
  /** "2mo ago", or null when the timestamp is missing, invalid, or in the future. */
  age: string | null;
  /** Only ever true when `age` is non-null — see the recency contract below. */
  magnet: boolean;
  /** "3 fixes · last 2mo ago", or just the count when the age is unknown. */
  label: string;
}

/**
 * The one place that turns raw fix columns into copy, so every surface tells
 * the same story about the same file.
 *
 * Two rules are baked in rather than left to each caller:
 *
 * - Silence when absent. No counted fixes means null, and callers render
 *   nothing at all — not a zero, not an empty row. A clean file's UI is
 *   unchanged by this feature.
 * - Recency. `bug_magnet` is a claim about *recent* fix pressure, so `magnet`
 *   is suppressed unless an age can sit beside it. "Fixed 4x last month" and
 *   "fixed 4x two years ago" must not render identically, and an unanchored
 *   flag says exactly that. The count itself is windowed and stands alone.
 *
 * `formatRelativeTimeOrNull` supplies the future-guard, so a clock-skewed
 * timestamp degrades to count-only rather than "in 3 days".
 */
export function summarizeFixHistory(
  count: number | null | undefined,
  lastFixAt?: string | null,
  bugMagnet?: boolean | null,
): FixHistorySummary | null {
  if (count == null || count <= 0) return null;
  const age = formatRelativeTimeOrNull(lastFixAt ?? null, "") || null;
  const countLabel = `${count} ${count === 1 ? "fix" : "fixes"}`;
  return {
    count,
    countLabel,
    age,
    magnet: Boolean(bugMagnet) && age !== null,
    label: age ? `${countLabel} · last ${age}` : countLabel,
  };
}
