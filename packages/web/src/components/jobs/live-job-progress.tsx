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
  // A phase that cannot know its item count reports no denominator, which the
  // API writes as 0 (see job_executor._async_update). There is no fraction to
  // show then, so the text drops the denominator instead of rendering "241/0
  // pages", which reads as a number that means something.
  const hasTotal = total > 0;
  const pct = hasTotal ? Math.round((completed / total) * 100) : 0;

  return (
    <span className="inline-flex items-center gap-2 text-[var(--color-model)]">
      <span className="tabular-nums">
        {hasTotal ? `${completed}/${total} pages` : `${completed} pages`}
      </span>
      <span className="h-1 w-16 overflow-hidden rounded-full bg-[var(--color-bg-inset)]">
        <span
          className="block h-full rounded-full bg-[var(--color-model)] transition-[width] duration-500"
          style={{ width: `${pct}%` }}
        />
      </span>
    </span>
  );
}
