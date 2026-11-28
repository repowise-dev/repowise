import { apiGet } from "./client";
import type {
  CostGroupResponse,
  CostSummaryResponse,
  SavingsAgentRow,
  SavingsBreakdownRow,
  SavingsFilesRow,
  SavingsOpportunityRow,
  SavingsOutputCalibration,
  SavingsOutputModelRow,
  SavingsResponse,
} from "@repowise-dev/types/generated/http";

// ---------------------------------------------------------------------------
// Types: the generated wire shapes, so a new server field reaches the page
// without a hand-kept copy drifting behind it.
// ---------------------------------------------------------------------------

export type CostGroup = CostGroupResponse;
export type CostSummary = CostSummaryResponse;
/** What agents avoided in one repository. `available: false` means nothing
 *  has been measured here, which is not the same as a measured zero. */
export type Savings = SavingsResponse;
export type {
  SavingsAgentRow,
  SavingsBreakdownRow,
  SavingsFilesRow,
  SavingsOpportunityRow,
  SavingsOutputCalibration,
  SavingsOutputModelRow,
};

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
