import { apiGet } from "./client";

export interface HealthFileMetric {
  file_path: string;
  score: number;
  max_ccn: number;
  max_nesting: number;
  nloc: number;
  has_test_file: boolean;
  line_coverage_pct: number | null;
  module: string | null;
}

export interface HealthFinding {
  id: string;
  file_path: string;
  biomarker_type: string;
  severity: "low" | "medium" | "high" | "critical";
  function_name: string | null;
  line_start: number | null;
  line_end: number | null;
  health_impact: number;
  reason: string;
  details: Record<string, unknown>;
  status: string;
}

export interface HealthOverviewResponse {
  summary: {
    file_count: number;
    average_health: number;
    worst_performer_path: string | null;
    worst_performer_score: number | null;
    open_findings: number;
  };
  files: HealthFileMetric[];
  top_findings: HealthFinding[];
}

export async function getHealthOverview(
  repoId: string,
  limit = 20,
): Promise<HealthOverviewResponse> {
  return apiGet<HealthOverviewResponse>(
    `/api/repos/${repoId}/health/overview`,
    { limit },
  );
}

export async function listHealthFindings(
  repoId: string,
  opts?: {
    biomarker_type?: string;
    file_path?: string;
    min_severity?: string;
    limit?: number;
  },
): Promise<HealthFinding[]> {
  return apiGet<HealthFinding[]>(`/api/repos/${repoId}/health/findings`, opts);
}
