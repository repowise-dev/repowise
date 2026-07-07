import { apiGet, apiPost, BASE_URL } from "./client";
import type { JobResponse } from "./types";

export async function listJobs(opts?: {
  repo_id?: string;
  status?: string;
  limit?: number;
  offset?: number;
}): Promise<JobResponse[]> {
  return apiGet<JobResponse[]>("/api/jobs", opts);
}

export async function getJob(jobId: string): Promise<JobResponse> {
  return apiGet<JobResponse>(`/api/jobs/${jobId}`);
}

/** Cancel a pending or running job. Actually stops the pipeline and marks
 * the job `cancelled`, releasing the active-job guard. */
export async function cancelJob(jobId: string): Promise<JobResponse> {
  return apiPost<JobResponse>(`/api/jobs/${jobId}/cancel`);
}

/** Returns the SSE stream URL for a job. Use with EventSource or the useSSE hook. */
export function getJobStreamUrl(jobId: string): string {
  return `${BASE_URL}/api/jobs/${jobId}/stream`;
}
