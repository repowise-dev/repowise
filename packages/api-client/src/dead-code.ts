import { apiGet, apiPost, apiPatch } from "./client";
import type {
  DeadCodeFindingResponse,
  DeadCodePatchRequest,
  DeadCodeSummaryResponse,
} from "./types";

export async function listDeadCode(
  repoId: string,
  opts?: {
    kind?: string;
    min_confidence?: number;
    status?: string;
    safe_only?: boolean;
    limit?: number;
  },
): Promise<DeadCodeFindingResponse[]> {
  return apiGet<DeadCodeFindingResponse[]>(`/api/repos/${repoId}/dead-code`, opts);
}

export async function analyzeDeadCode(repoId: string): Promise<{ job_id: string }> {
  return apiPost<{ job_id: string }>(`/api/repos/${repoId}/dead-code/analyze`);
}

export async function getDeadCodeSummary(repoId: string): Promise<DeadCodeSummaryResponse> {
  return apiGet<DeadCodeSummaryResponse>(`/api/repos/${repoId}/dead-code/summary`);
}

export async function patchDeadCodeFinding(
  findingId: string,
  data: DeadCodePatchRequest,
): Promise<DeadCodeFindingResponse> {
  return apiPatch<DeadCodeFindingResponse>(`/api/dead-code/${findingId}`, data);
}
