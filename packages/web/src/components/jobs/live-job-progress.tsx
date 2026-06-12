"use client";

import { useJob } from "@/lib/hooks/use-job";

/**
 * Live progress text for a running/pending generation job. Subscribes to the
 * job's SSE stream so dashboard rows tick without a page refresh; falls back
 * to the server-rendered snapshot until the first event arrives.
 */
export function LiveJobProgress({
  jobId,
  initialCompleted,
  initialTotal,
}: {
  jobId: string;
  initialCompleted: number;
  initialTotal: number;
}) {
  const { job, sse } = useJob(jobId);
  const completed =
    sse.data?.completed_pages ?? job?.completed_pages ?? initialCompleted;
  const total = sse.data?.total_pages ?? job?.total_pages ?? initialTotal;
  const pct = total > 0 ? Math.round((completed / total) * 100) : 0;

  return (
    <span className="inline-flex items-center gap-2 text-[var(--color-accent-primary)]">
      <span className="tabular-nums">
        {completed}/{total} pages
      </span>
      <span className="h-1 w-16 overflow-hidden rounded-full bg-[var(--color-bg-inset)]">
        <span
          className="block h-full rounded-full bg-[var(--color-accent-primary)] transition-[width] duration-500"
          style={{ width: `${pct}%` }}
        />
      </span>
    </span>
  );
}
