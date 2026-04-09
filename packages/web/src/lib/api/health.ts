import { apiGet } from "./client";
import type { HealthResponse } from "./types";

export async function getHealth(): Promise<HealthResponse> {
  return apiGet<HealthResponse>("/health");
}

export interface CoordinatorHealth {
  sql_pages: number | null;
  vector_count: number | null;
  graph_nodes: number | null;
  drift_pct: number | null;
  status: "ok" | "warning" | "critical";
}

export async function getCoordinatorHealth(repoId: string): Promise<CoordinatorHealth> {
  return apiGet<CoordinatorHealth>(`/api/repos/${repoId}/health/coordinator`);
}
