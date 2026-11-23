import type { MeasuredRow } from "@repowise-dev/types/runtime-evidence";

import { formatNumber, formatPercent } from "../../lib/format";
import { humanizeToken } from "./presentation";

/**
 * Words and figures for runtime evidence, shared by the measured table, the
 * status strip and the agent prompt so each says a run's basis the same way.
 */

/** `entry_point` reads "entry point". */
export function workloadClassLabel(workloadClass: string): string {
  return humanizeToken(workloadClass).toLowerCase();
}

/** A share of the run: 0.029 reads "2.9%". */
export function shareText(share: number): string {
  return formatPercent(share, 1);
}

/** The share's interval, "2.5 to 3.4%", or null when the run counted no samples. */
export function intervalText(low: number | null, high: number | null): string | null {
  if (low == null || high == null) return null;
  return `${(low * 100).toFixed(1)} to ${shareText(high)}`;
}

/** `path:line`, the line where the function starts when the index still holds it. */
export function rowLocation(row: Pick<MeasuredRow, "file_path" | "lines">): string {
  const start = row.lines?.[0];
  return start != null ? `${row.file_path}:${start}` : row.file_path;
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${formatNumber(n)} ${n === 1 ? one : many}`;
}

/** Null when the function was never mapped to tests: unknown, not none. */
export function testsText(tests: readonly string[] | null): string {
  if (tests == null) return "tests unknown";
  return tests.length === 0 ? "no tests found" : plural(tests.length, "test");
}
