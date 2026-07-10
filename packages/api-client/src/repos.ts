import { apiGet, apiPost, apiPatch, apiDelete } from "./client";
import type {
  RepoCreate,
  RepoUpdate,
  RepoResponse,
  JobResponse,
  RepoStatsResponse,
  PreflightResponse,
} from "./types";

export async function listRepos(): Promise<RepoResponse[]> {
  return apiGet<RepoResponse[]>("/api/repos");
}

export async function getRepo(repoId: string): Promise<RepoResponse> {
  return apiGet<RepoResponse>(`/api/repos/${repoId}`);
}

export async function createRepo(data: RepoCreate): Promise<RepoResponse> {
  return apiPost<RepoResponse>("/api/repos", data);
}

export async function updateRepo(repoId: string, data: RepoUpdate): Promise<RepoResponse> {
  return apiPatch<RepoResponse>(`/api/repos/${repoId}`, data);
}

export async function syncRepo(repoId: string): Promise<JobResponse> {
  return apiPost<JobResponse>(`/api/repos/${repoId}/sync`);
}

export async function fullResyncRepo(repoId: string): Promise<JobResponse> {
  return apiPost<JobResponse>(`/api/repos/${repoId}/full-resync`);
}

/** Start the first full index (docs included) for a registered repo.
 * Returns 409 when a job is already active for it. */
export async function startIndexJob(
  repoId: string,
): Promise<{ job_id: string; status: string }> {
  return apiPost<{ job_id: string; status: string }>(`/api/repos/${repoId}/index`);
}

/** Provider connectivity smoke test + page/cost estimate for a first index. */
export async function preflightIndex(
  repoId: string,
  coveragePct?: number,
): Promise<PreflightResponse> {
  return apiPost<PreflightResponse>(
    `/api/repos/${repoId}/preflight`,
    undefined,
    undefined,
    coveragePct !== undefined ? { coverage_pct: coveragePct } : undefined,
  );
}

export async function deleteRepo(repoId: string): Promise<{ ok: boolean; deleted_pages: number }> {
  return apiDelete<{ ok: boolean; deleted_pages: number }>(`/api/repos/${repoId}`);
}

export async function getRepoStats(repoId: string): Promise<RepoStatsResponse> {
  return apiGet<RepoStatsResponse>(`/api/repos/${repoId}/stats`);
}
