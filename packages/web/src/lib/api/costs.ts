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

export interface DistillSavingsGroup {
  group: string;
  events: number;
  raw_tokens: number;
  distilled_tokens: number;
  saved_tokens: number;
}

export interface McpDropGroup {
  tool: string;
  events: number;
  tokens: number;
  /** "counterfactual" (answer replaced raw exploration) or "truncation" (budget drop). */
  kind?: string;
}

export interface DistillSavings {
  available: boolean;
  events: number;
  raw_tokens: number;
  distilled_tokens: number;
  saved_tokens: number;
  estimated_usd_saved: number;
  pricing_model: string;
  /** How the pricing model was resolved (Phase 1 model-aware pricing). */
  pricing_agent: string;
  pricing_source: string;
  per_filter: DistillSavingsGroup[];
  per_day: DistillSavingsGroup[];
  /** Unified MCP savings — counterfactual ledger + truncation drops. */
  mcp_events: number;
  mcp_tokens: number;
  /** Count of counterfactual MCP queries answered ("N MCP queries answered"). */
  mcp_queries: number;
  mcp_per_tool: McpDropGroup[];
  /** Raw (non-distilled) agent commands a filter would have caught. */
  missed_events: number;
  missed_tokens_est: number;
  missed_window_days: number;
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

export async function getDistillSavings(
  repoId: string,
  since?: string,
): Promise<DistillSavings> {
  return apiGet<DistillSavings>(`/api/repos/${repoId}/distill-savings`, {
    since,
  });
}
