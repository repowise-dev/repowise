/**
 * The savings and spend reports, as the shared surface consumes them.
 *
 * These are the generated wire types, not a hand-kept copy: field names match
 * the server exactly and a new field arrives without a second declaration to
 * update. Every field but `available` is optional on the wire, so a host that
 * only has part of the data (spend but no savings events yet) can still pass
 * what it has. Components read a field and render it; the server's report
 * service is the only thing that aggregates or prices.
 */
import type {
  CostGroupResponse,
  CostSummaryResponse,
  SavingsBreakdownRow,
  SavingsCheckRow,
  SavingsFilesRow,
  SavingsResponse,
} from "@repowise-dev/types/generated/http";

export type SavingsView = SavingsResponse;
export type SavingsBreakdownView = SavingsBreakdownRow;
export type SavingsFilesView = SavingsFilesRow;
/** One check tool's calls and what Repowise returned; outcome fields null when unrecorded. */
export type SavingsCheckView = SavingsCheckRow;

/** What Repowise's own model work cost, for the same window. Metered spend,
 *  never netted against savings: they are different accounts. */
export type SpendView = CostSummaryResponse;

/** One row of spend grouped by activity, model or day. */
export type SpendRowView = CostGroupResponse;

/** The groupings the spend table offers; the server's `by` values. */
export type SpendGrouping = "operation" | "model" | "day";

/** The first day in the report that skipped a file open, or null. */
export function firstFilesDay(data: SavingsView): string | null {
  return data.files_per_day?.find((row) => row.group && (row.files_not_opened ?? 0) > 0)?.group ?? null;
}

/** When file tracking began after the first saving in this window: that day,
 *  so a small count reads as recent rather than as the whole window's. */
export function filesTrackedSince(data: SavingsView): string | null {
  const first = firstFilesDay(data);
  const firstSaving = data.per_day?.find((row) => row.group)?.group;
  return first && firstSaving && first > firstSaving ? first : null;
}

/** True when the report holds at least one recorded event. A repository that
 *  never served an agent has no savings, which is a different claim from a
 *  measured zero. */
export function hasSavings(data: SavingsView | undefined): data is SavingsView {
  return Boolean(data?.available) && (data?.unique_events ?? 0) > 0;
}
