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
