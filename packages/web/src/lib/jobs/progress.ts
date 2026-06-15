import type { JobProgressEvent, JobResponse } from "@/lib/api/types";

export function computeElapsedMs(job: JobResponse | undefined, nowMs = Date.now()): number {
  if (!job) return 0;
  const start = Date.parse(job.started_at ?? job.created_at);
  if (!Number.isFinite(start)) return 0;
  const end =
    job.status === "completed" || job.status === "failed"
      ? Date.parse(job.finished_at ?? "")
      : nowMs;
  const effectiveEnd = Number.isFinite(end) ? end : nowMs;
  return Math.max(0, effectiveEnd - start);
}

export function mergeJobProgress(
  job: JobResponse | undefined,
  ev: JobProgressEvent | null,
): JobResponse | undefined {
  if (!job || !ev || ev.job_id !== job.id) return job;
  return {
    ...job,
    status: ev.status ?? job.status,
    completed_pages: ev.completed_pages ?? job.completed_pages,
    total_pages: ev.total_pages ?? job.total_pages,
    failed_pages: ev.failed_pages ?? job.failed_pages,
    current_level: ev.current_level ?? job.current_level,
  };
}
