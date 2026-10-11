import type { StatsHighlights } from "@repowise-dev/types/stats";
import { apiGet } from "./client";

/** One-call payload for the repo Stats ("By the Numbers") page. */
export async function getStatsHighlights(repoId: string): Promise<StatsHighlights> {
  return apiGet<StatsHighlights>(`/api/repos/${repoId}/stats/highlights`);
}
