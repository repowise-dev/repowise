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

export interface CoverageFileRow {
  file_path: string;
  source_format: string;
  line_coverage_pct: number;
  branch_coverage_pct: number | null;
  total_coverable_lines: number;
  ingested_at: string | null;
  ingested_commit_sha: string | null;
  covered_lines?: number[];
  health_score?: number;
  nloc?: number;
}

export interface ModuleCoverageRow {
  module: string;
  files: number;
  covered_lines: number;
  total_lines: number;
  line_coverage_pct: number;
}

export interface CoverageSummary {
  file_count: number;
  covered_lines: number;
  total_lines: number;
  line_coverage_pct: number | null;
  branch_coverage_pct: number | null;
  source_format: string | null;
  ingested_at: string | null;
  ingested_commit_sha: string | null;
}

export interface HealthCoverageResponse {
  summary: CoverageSummary;
  files: CoverageFileRow[];
  modules: ModuleCoverageRow[];
}

export async function getHealthCoverage(
  repoId: string,
  opts?: { file_path?: string; limit?: number },
): Promise<HealthCoverageResponse> {
  return apiGet<HealthCoverageResponse>(
    `/api/repos/${repoId}/health/coverage`,
    opts,
  );
}

export interface RefactoringTarget {
  file_path: string;
  score: number;
  nloc: number;
  primary_biomarker: string;
  primary_severity: "low" | "medium" | "high" | "critical";
  primary_reason: string;
  primary_function: string | null;
  primary_line_start: number | null;
  primary_line_end: number | null;
  total_impact: number;
  finding_count: number;
  biomarkers: string[];
  effort_bucket: "S" | "M" | "L" | "XL";
  impact_per_effort: number;
}

export interface RefactoringTargetsResponse {
  targets: RefactoringTarget[];
  total: number;
}

export async function getRefactoringTargets(
  repoId: string,
  opts?: { limit?: number; module?: string },
): Promise<RefactoringTargetsResponse> {
  return apiGet<RefactoringTargetsResponse>(
    `/api/repos/${repoId}/health/refactoring-targets`,
    opts,
  );
}
