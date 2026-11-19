import { apiGet } from "./client";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface CostGroup {
  group: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

export interface CostSummary {
  total_cost_usd: number;
  total_calls: number;
  total_input_tokens: number;
  total_output_tokens: number;
  since: string | null;
}

/** One bucket of a savings breakdown. `group` is null for the bucket with no
 *  name — savings whose events carry no pricing model, which is a real
 *  quantity rather than a row to drop. */
export interface SavingsBreakdownRow {
  group: string | null;
  events: number;
  saved_input_tokens: number;
}

/** One agent's savings. The display name comes from the server's identity
 *  registry, so no client keeps a label map of its own. */
export interface SavingsAgentRow {
  agent: string;
  agent_display_name: string | null;
  events: number;
  saved_input_tokens: number;
}

/** One kind of observed opportunity. Never part of achieved savings. */
export interface SavingsOpportunityRow {
  kind: string;
  observations: number;
  estimated_potential_input_tokens: number;
}

/**
 * What agents avoided in one repository, from the canonical savings ledger.
 *
 * Three distinctions the UI preserves rather than flattens: measured vs
 * inferred evidence, priced vs unpriced tokens, and achieved savings vs
 * observed opportunities. `available: false` means nothing has been measured
 * in this repository, which is not the same as a measured zero.
 */
export interface Savings {
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
  saved_output_tokens: number | null;
  priced_saved_output_tokens: number;
  unpriced_saved_output_tokens: number;
  priced_output_savings_usd: number;

  /** How much smaller the input got, over the events that carry a baseline.
   *  The ratios are null when nothing in the window had a baseline to compare
   *  against, which is distinct from a measured zero. */
  baseline_events: number;
  reducing_events: number;
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

  /** Parts of the totals above, never added to them. `backfilled_*` prices
   *  unpriced tokens at report time from the session's or agent's rate. */
  subagent_events?: number;
  subagent_saved_input_tokens?: number;
  backfilled_saved_input_tokens?: number;
  backfilled_input_savings_usd?: number;
  /** Output tokens agents did not write, estimated from the paired benchmark
   *  per question asked in each session with an MCP call. Null when no calibration could be loaded. */
  output_tokens_saved_estimated?: number | null;
  output_usd_saved_estimated?: number | null;
  /** Priced input saving plus the estimated output saving. */
  total_usd_saved_estimated?: number | null;
  calibration?: SavingsOutputCalibration | null;
}

export interface SavingsOutputModelRow {
  model: string | null;
  /** "measured" | "anthropic_fallback" | "other_fallback" */
  basis: string;
  sessions: number;
  questions: number;
  mcp_calls_per_question: number;
  reduction: number;
  output_tokens_per_question: number;
  output_tokens_saved: number;
  output_rate_usd_per_million: number | null;
  output_usd_saved: number | null;
}

export interface SavingsOutputCalibration {
  source: string;
  version: string;
  /** True: credited per question asked, not once per session. */
  per_question: boolean;
  questions_per_session_cap: number;
  formula: string;
  sessions: number;
  questions: number;
  unpriced_output_tokens_saved: number;
  per_model: SavingsOutputModelRow[];
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

export async function listCosts(
  repoId: string,
  opts: { since?: string; by?: "operation" | "model" | "day" } = {},
): Promise<CostGroup[]> {
  return apiGet<CostGroup[]>(`/api/repos/${repoId}/costs`, {
    since: opts.since,
    by: opts.by ?? "day",
  });
}

export async function getCostSummary(
  repoId: string,
  since?: string,
): Promise<CostSummary> {
  return apiGet<CostSummary>(`/api/repos/${repoId}/costs/summary`, {
    since,
  });
}

export async function getSavings(
  repoId: string,
  opts: { days?: number } = {},
): Promise<Savings> {
  return apiGet<Savings>(`/api/repos/${repoId}/savings`, {
    days: opts.days,
  });
}
