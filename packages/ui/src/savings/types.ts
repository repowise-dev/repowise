/**
 * The savings report, as the shared surface consumes it.
 *
 * Field names match the wire exactly. That is the point: the server's report
 * service is the only thing that aggregates or prices, so a component here
 * reads a field and renders it rather than deriving it. Three surfaces used to
 * aggregate the ledger independently and published three different dollar
 * figures for one repository; a number computed in React would be a fourth.
 *
 * Declared here rather than imported from `@repowise-dev/api-client` because
 * this package stays free of the fetch layer -- the same reason
 * `settings/provider-settings.tsx` redeclares `ProviderInfo`. A host maps its
 * response onto this; it does not hand a component its HTTP client.
 */

/** One bucket of a breakdown. `group` is null for the bucket with no name --
 *  savings whose events carry no pricing model, which is a real quantity
 *  rather than a row to drop. */
export interface SavingsBreakdownRow {
  group: string | null;
  events: number;
  saved_input_tokens: number;
}

/** One agent's savings. `agent_display_name` is resolved from the server's
 *  identity registry, so no client keeps a label map of its own. */
export interface SavingsAgentRow {
  agent: string;
  agent_display_name: string | null;
  events: number;
  saved_input_tokens: number;
}

/** One kind of recorded opportunity. Never part of achieved savings. */
export interface SavingsOpportunityRow {
  kind: string;
  observations: number;
  estimated_potential_input_tokens: number;
}

export interface SavingsView {
  /** `false` means nothing has been measured here, which is a different claim
   *  from a measured zero. */
  available: boolean;
  /** Window the figures cover; null means all time. */
  window_days: number | null;
  as_of: string;
  first_event_at: string | null;
  last_event_at: string | null;

  unique_events: number;
  successful_or_usable_partial_events: number;
  saving_interactions: number;
  mcp_queries_answered: number;
  dead_ends: number;

  saved_input_tokens: number;
  measured_saved_input_tokens: number;
  inferred_saved_input_tokens: number;
  priced_saved_input_tokens: number;
  unpriced_saved_input_tokens: number;
  priced_input_savings_usd: number;
  /** Null when no event in the window carried output evidence at all, which is
   *  distinct from a measured zero. */
  saved_output_tokens: number | null;
  priced_saved_output_tokens: number;
  unpriced_saved_output_tokens: number;
  priced_output_savings_usd: number;

  /** How much smaller the input got, over the events that carry a baseline.
   *  The ratios are null when nothing in the window had a baseline to compare
   *  against, which is distinct from a measured zero. */
  baseline_events: number;
  baseline_input_tokens: number;
  baseline_saved_input_tokens: number;
  input_reduction_ratio: number | null;
  input_reduction_ratio_p90: number | null;

  per_operation: SavingsBreakdownRow[];
  per_surface: SavingsBreakdownRow[];
  per_agent: SavingsAgentRow[];
  per_model: SavingsBreakdownRow[];
  per_day: SavingsBreakdownRow[];

  opportunity_count: number;
  opportunity_tokens_excluded: number;
  per_opportunity_kind: SavingsOpportunityRow[];
  /** Transcript-mined opportunities: what was *not* saved. */
  missed_events: number;
  missed_tokens_est: number;
  missed_window_days: number;
  reread_events: number;
  reread_tokens_est: number;
}

/** What Repowise's own model work cost. Metered spend, never netted against
 *  savings: they are different accounts and the page keeps them apart. */
export interface SpendView {
  total_cost_usd: number;
  total_calls: number;
  total_input_tokens: number;
  total_output_tokens: number;
  since: string | null;
}

/**
 * Surface slugs read as identifiers; these are the words for them.
 *
 * Kept client-side rather than on the wire because a surface is a fixed part
 * of the accounting contract -- the set is closed and changing it is a schema
 * change -- unlike an agent, whose label comes from the identity registry in
 * the payload precisely because that set is open.
 */
const SURFACE_LABELS: Record<string, string> = {
  distill: "Distill",
  hook: "Hooks",
  mcp: "MCP",
  vscode_lm: "VS Code",
};

/** Name a surface slug. An unrecognised slug renders as itself rather than as
 *  "Unknown": it is a real surface this build has no word for yet, and hiding
 *  it behind a sentinel would lose which one it was. */
export function surfaceLabel(slug: string | null): string {
  if (!slug) return "Unknown";
  return SURFACE_LABELS[slug] ?? slug;
}

/** Name an agent from its row. The display name is the server's; the slug is
 *  the fallback when this build's registry did not resolve one. */
export function agentLabel(row: SavingsAgentRow): string {
  return row.agent_display_name ?? row.agent;
}

/** Name a pricing-model bucket. Null is the unpriced bucket, which is a
 *  quantity worth naming rather than a gap. */
export function modelLabel(slug: string | null): string {
  return slug ?? "No rate recorded";
}
