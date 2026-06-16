/**
 * Code-health API client. The response/query *types* now live in the shared
 * `@repowise-dev/types/health` contract (migrated out of this web-local file so
 * the hosted frontend and the bot read the same shapes); this module re-exports
 * them for back-compat and keeps only the fetch functions.
 */
import type {
  ChurnComplexityResponse,
  HealthFilesQuery,
  HealthFilesResponse,
  HealthFinding,
  HealthCoverageResponse,
  HealthFileBreakdownResponse,
  HealthOverviewResponse,
  HealthTrendResponse,
  RefactoringQuery,
  RefactoringTargetsResponse,
} from "@repowise-dev/types/health";
import { apiGet, apiPatch } from "./client";

export type {
  BiomarkerBreakdownRow,
  ChurnComplexityPoint,
  ChurnComplexityResponse,
  CoverageFileRow,
  CoverageSummary,
  DefectAccuracy,
  FileBreakdownCategory,
  FileBreakdownFinding,
  HealthBand,
  HealthCoverageResponse,
  HealthDistribution,
  HealthFileBreakdownResponse,
  HealthFileMetric,
  HealthFilesQuery,
  HealthFilesResponse,
  HealthFinding,
  HealthModuleRow,
  HealthOverviewResponse,
  HealthTrendResponse,
  ModuleCoverageRow,
  RefactoringQuery,
  RefactoringTarget,
  RefactoringTargetsResponse,
} from "@repowise-dev/types/health";

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

export async function listHealthFiles(
  repoId: string,
  opts?: HealthFilesQuery,
): Promise<HealthFilesResponse> {
  return apiGet<HealthFilesResponse>(
    `/api/repos/${repoId}/health/files`,
    opts as Record<string, string | number | boolean | undefined>,
  );
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

export async function getHealthCoverage(
  repoId: string,
  opts?: { file_path?: string; limit?: number },
): Promise<HealthCoverageResponse> {
  return apiGet<HealthCoverageResponse>(
    `/api/repos/${repoId}/health/coverage`,
    opts,
  );
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

export async function getChurnComplexity(
  repoId: string,
  opts?: { limit?: number },
): Promise<ChurnComplexityResponse> {
  return apiGet<ChurnComplexityResponse>(
    `/api/repos/${repoId}/health/churn-complexity`,
    opts,
  );
}
