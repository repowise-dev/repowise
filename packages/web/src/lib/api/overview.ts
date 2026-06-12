import type { OverviewSummaryResponse } from "@repowise-dev/types/overview";
import { apiGet } from "./client";

/** One-call Overview payload — replaces the old N-call waterfall. */
export async function getOverviewSummary(repoId: string): Promise<OverviewSummaryResponse> {
  return apiGet<OverviewSummaryResponse>(`/api/repos/${repoId}/overview-summary`);
}
