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
  SavingsFilesRow,
  SavingsResponse,
} from "@repowise-dev/types/generated/http";

export type SavingsView = SavingsResponse;
export type SavingsBreakdownView = SavingsBreakdownRow;
export type SavingsFilesView = SavingsFilesRow;

/** What Repowise's own model work cost, for the same window. Metered spend,
 *  never netted against savings: they are different accounts. */
export type SpendView = CostSummaryResponse;

/** One row of spend grouped by activity, model or day. */
export type SpendRowView = CostGroupResponse;

/** The groupings the spend table offers; the server's `by` values. */
export type SpendGrouping = "operation" | "model" | "day";

/** True when the report holds at least one recorded event. A repository that
 *  never served an agent has no savings, which is a different claim from a
 *  measured zero. */
export function hasSavings(data: SavingsView | undefined): data is SavingsView {
  return Boolean(data?.available) && (data?.unique_events ?? 0) > 0;
}
