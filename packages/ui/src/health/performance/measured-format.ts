import type { BenchmarkRow, MeasuredRow } from "@repowise-dev/types/runtime-evidence";

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

/** 1234.5 reads "1,234.5", 0.00012 reads "0.00012": enough figures to compare. */
const perOpFigure = (v: number) =>
  Math.abs(v) >= 1 || v === 0 ? formatNumber(Math.round(v * 10) / 10) : String(Number(v.toPrecision(2)));

/**
 * A benchmark's figures per operation, "1,234.5 ns/op · 12 allocs/op · 2,048 B/op".
 * A figure the tool did not report is left out, never read as zero.
 */
export function perOpText(
  row: Pick<BenchmarkRow, "time" | "unit" | "allocs_per_op" | "bytes_per_op">,
): string {
  return [
    row.time != null ? `${perOpFigure(row.time)} ${row.unit ? `${row.unit}/op` : "per op"}` : null,
    row.allocs_per_op != null ? `${perOpFigure(row.allocs_per_op)} allocs/op` : null,
    row.bytes_per_op != null ? `${perOpFigure(row.bytes_per_op)} B/op` : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${formatNumber(n)} ${n === 1 ? one : many}`;
}

/** Null when the function was never mapped to tests: unknown, not none. */
export function testsText(tests: readonly string[] | null): string {
  if (tests == null) return "tests unknown";
  return tests.length === 0 ? "no tests found" : plural(tests.length, "test");
}
