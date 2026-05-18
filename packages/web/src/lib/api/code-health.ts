import { apiGet, apiPatch } from "./client";

export interface HealthFileMetric {
  file_path: string;
  score: number;
  max_ccn: number;
  max_nesting: number;
  nloc: number;
  has_test_file: boolean;
  line_coverage_pct: number | null;
  module: string | null;
  duplication_pct?: number | null;
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

export interface HealthModuleRow {
  module: string;
  file_count: number;
  nloc: number;
  average_health: number;
  worst_performer_path: string;
  worst_performer_score: number;
}

export interface BiomarkerBreakdownRow {
  biomarker_type: string;
  critical: number;
  high: number;
  medium: number;
  low: number;
  total: number;
}

export interface HealthOverviewResponse {
  summary: {
    file_count: number;
    average_health: number;
    hotspot_health?: number | null;
    worst_performer_path: string | null;
    worst_performer_score: number | null;
    open_findings: number;
    severity_breakdown?: { critical: number; high: number; medium: number; low: number };
  };
  files: HealthFileMetric[];
  top_findings: HealthFinding[];
  modules?: HealthModuleRow[];
  biomarkers?: BiomarkerBreakdownRow[];
  meta?: {
    last_indexed_at: string | null;
    head_commit: string | null;
    snapshot_count: number;
  };
}

export async function getHealthOverview(
  repoId: string,
  limit = 25,
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

export interface HealthFilesResponse {
  total: number;
  offset: number;
  limit: number;
  files: HealthFileMetric[];
}

export interface HealthFilesQuery {
  limit?: number;
  offset?: number;
  sort?: string;
  order?: "asc" | "desc";
  search?: string;
  module?: string;
  only_hotspots?: boolean;
  only_untested?: boolean;
  only_failing?: boolean;
}

export async function listHealthFiles(
  repoId: string,
  opts?: HealthFilesQuery,
): Promise<HealthFilesResponse> {
  return apiGet<HealthFilesResponse>(
    `/api/repos/${repoId}/health/files`,
    opts as Record<string, string | number | boolean | undefined>,
  );
}

export interface FileBreakdownFinding {
  id: string;
  biomarker_type: string;
  severity: "low" | "medium" | "high" | "critical";
  raw_impact: number;
  applied_impact: number;
  function_name: string | null;
  reason: string;
}

export interface FileBreakdownCategory {
  category: string;
  cap: number;
  raw_deduction: number;
  applied_deduction: number;
  capped: boolean;
  finding_count: number;
  findings: FileBreakdownFinding[];
}

export interface HealthFileBreakdownResponse {
  file_path: string;
  metric: HealthFileMetric | null;
  breakdown: {
    score: number;
    total_deduction: number;
    categories: FileBreakdownCategory[];
  };
  findings: HealthFinding[];
  suggestions: Record<string, string>;
}

export async function getHealthFileBreakdown(
  repoId: string,
  filePath: string,
): Promise<HealthFileBreakdownResponse> {
  return apiGet<HealthFileBreakdownResponse>(
    `/api/repos/${repoId}/health/files/breakdown`,
    { file_path: filePath },
  );
}

export interface HealthTrendResponse {
  history: Array<{
    taken_at: string | null;
    hotspot_health: number;
    average_health: number;
    worst_performer_path: string | null;
    worst_performer_score: number | null;
  }>;
  summary: {
    current_hotspot_health: number;
    current_average_health: number;
    previous_hotspot_health: number | null;
    previous_average_health: number | null;
    hotspot_delta: number | null;
    average_delta: number | null;
  };
  alerts: Array<{
    kind: string;
    metric: string;
    current: number;
    baseline: number | null;
    delta: number;
    message: string;
  }>;
  file_deltas: Array<{ file_path: string; before: number; after: number; delta: number }>;
  snapshot_count: number;
}

export async function getHealthTrend(repoId: string, limit = 20): Promise<HealthTrendResponse> {
  return apiGet<HealthTrendResponse>(`/api/repos/${repoId}/health/trend`, { limit });
}

export async function updateFindingStatus(
  repoId: string,
  findingId: string,
  status: "open" | "acknowledged" | "resolved" | "false_positive",
): Promise<HealthFinding> {
  return apiPatch<HealthFinding>(
    `/api/repos/${repoId}/health/findings/${findingId}`,
    { status },
  );
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
  module?: string | null;
  primary_biomarker: string;
  primary_severity: "low" | "medium" | "high" | "critical";
  primary_reason: string;
  primary_function: string | null;
  primary_line_start: number | null;
  primary_line_end: number | null;
  primary_suggestion?: string;
  primary_finding_id?: string;
  total_impact: number;
  finding_count: number;
  biomarkers: string[];
  effort_bucket: "S" | "M" | "L" | "XL";
  impact_per_effort: number;
  all_findings?: Array<{
    id: string;
    biomarker_type: string;
    severity: "low" | "medium" | "high" | "critical";
    function_name: string | null;
    health_impact: number;
    reason: string;
    status?: string;
  }>;
}

export interface RefactoringTargetsResponse {
  targets: RefactoringTarget[];
  total: number;
}

export interface RefactoringQuery {
  limit?: number;
  module?: string;
  biomarker?: string;
  min_severity?: string;
  max_effort?: string;
  sort?: "impact_per_effort" | "total_impact" | "score" | "finding_count";
}

export async function getRefactoringTargets(
  repoId: string,
  opts?: RefactoringQuery,
): Promise<RefactoringTargetsResponse> {
  return apiGet<RefactoringTargetsResponse>(
    `/api/repos/${repoId}/health/refactoring-targets`,
    opts as Record<string, string | number | boolean | undefined>,
  );
}
