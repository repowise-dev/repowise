"use client";

import { useEffect, useMemo } from "react";
import useSWR from "swr";
import { getJob, getJobStreamUrl } from "@/lib/api/jobs";
import { useSSE } from "./use-sse";
import type { JobResponse, JobProgressEvent, JobMessageEvent } from "@/lib/api/types";
import { mergeJobProgress } from "@/lib/jobs/progress";

/**
 * Combines polling (SWR) for job metadata with SSE for live progress events.
 * - When the job is running, SSE is active and progress events update in real-time.
 * - When done/failed, SSE closes and SWR has the final state.
 */
export function useJob(jobId: string | null) {
  const { data: job, mutate } = useSWR<JobResponse>(
    jobId ? `/api/jobs/${jobId}` : null,
    jobId ? () => getJob(jobId) : null,
    {
      refreshInterval: (j) =>
        j?.status === "running" || j?.status === "pending" ? 5000 : 0,
    },
  );

  const isActive = job?.status === "running" || job?.status === "pending";
  const streamUrl = jobId && isActive ? getJobStreamUrl(jobId) : null;

  const sse = useSSE<JobProgressEvent>(streamUrl, { enabled: !!streamUrl });

  // When SSE marks done, trigger a final SWR revalidation.
  useEffect(() => {
    if (sse.isDone && isActive) {
      void mutate();
    }
  }, [isActive, mutate, sse.isDone]);

  const liveJob = useMemo(() => {
    const ev = sse.data as JobProgressEvent | null;
    return mergeJobProgress(job, ev);
  }, [job, sse.data]);

  // Pipeline log lines from `event: message` frames; the wrapper renders
  // them in the JobLog tail.
  const messages = useMemo(
    () =>
      (sse.messages as JobMessageEvent[]).filter(
        (m) => m && typeof m === "object" && typeof m.text === "string",
      ),
    [sse.messages],
  );

  const phase = (sse.data as JobProgressEvent | null)?.phase ?? null;

  return { job: liveJob, sse, isActive, messages, phase };
}
