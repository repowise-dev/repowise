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

/** Returns the SSE stream URL for a job. Use with EventSource or the useSSE hook.
 *
 * An EventSource can't send the Authorization header, so when the server has a
 * key set the browser authenticates the stream with a per-job `token` (from the
 * job-launch response or `JobResponse.stream_token`) instead. The token is
 * scoped to this one job id and expires quickly; the raw API key is never put
 * in the query string. When no token is available (open local server) the URL
 * is header-authenticated as before. */
export function getJobStreamUrl(jobId: string, streamToken?: string | null): string {
  const base = `${BASE_URL}/api/jobs/${jobId}/stream`;
  if (!streamToken) return base;
  return `${base}?token=${encodeURIComponent(streamToken)}`;
}
