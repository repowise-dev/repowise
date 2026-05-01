"use client";

import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { GenerationProgress } from "@repowise-dev/ui/jobs/generation-progress";
import { useJob } from "@/lib/hooks/use-job";
import { cancelJob } from "@/lib/api/jobs";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import type { JobProgressEvent } from "@/lib/api/types";

interface Props {
  jobId: string;
  repoName?: string;
  onDone?: () => void;
}

const PENDING_STUCK_THRESHOLD_MS = 30_000;

export function GenerationProgressWrapper({ jobId, repoName, onDone }: Props) {
  const { job, sse } = useJob(jobId);
  const [log, setLog] = useState<Array<{ text: string }>>([]);
  const [elapsed, setElapsed] = useState(0);
  const [actualCost, setActualCost] = useState<number | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const startRef = useRef(Date.now());
  const notifiedRef = useRef(false);

  useEffect(() => {
    const id = setInterval(() => setElapsed(Date.now() - startRef.current), 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (!sse.data) return;
    const ev = sse.data as JobProgressEvent;
    if (ev.current_page) {
      setLog((prev) => [
        ...prev,
        { text: `[L${ev.current_level ?? "?"}] ${ev.current_page}` },
      ]);
    }
    if (ev.actual_cost_usd != null) {
      setActualCost(ev.actual_cost_usd);
    }
  }, [sse.data]);

  useEffect(() => {
    if (notifiedRef.current) return;
    if (job?.status === "completed") {
      notifiedRef.current = true;
      toast.success(`Documentation updated${repoName ? ` — ${repoName}` : ""}`, {
        description: `${formatNumber(job.completed_pages)} pages generated`,
      });
      onDone?.();
    } else if (job?.status === "failed") {
      notifiedRef.current = true;
      toast.error("Generation failed", {
        description: job.error_message ?? "Unknown error",
      });
      onDone?.();
    }
  }, [job?.status, job?.completed_pages, job?.error_message, repoName, onDone]);

  const isPending = job?.status === "pending";
  const stuckPending =
    isPending &&
    job?.started_at == null &&
    elapsed > PENDING_STUCK_THRESHOLD_MS;

  async function handleCancel() {
    if (!job) return;
    setCancelling(true);
    try {
      await cancelJob(job.id);
      toast.success("Job cancelled");
      onDone?.();
    } catch (e) {
      toast.error("Couldn't cancel job", {
        description: e instanceof Error ? e.message : "Unknown error",
      });
    } finally {
      setCancelling(false);
    }
  }

  return (
    <GenerationProgress
      job={job ?? undefined}
      log={log}
      elapsed={elapsed}
      actualCost={actualCost}
      stuckPending={stuckPending}
      cancelling={cancelling}
      onCancel={handleCancel}
    />
  );
}
