"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { GenerationProgress } from "@repowise-dev/ui/jobs/generation-progress";
import { useJob } from "@/lib/hooks/use-job";
import { cancelJob } from "@/lib/api/jobs";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import { computeElapsedMs } from "@/lib/jobs/progress";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";

interface Props {
  jobId: string;
  repoName?: string;
  /** Fired once the job reaches a terminal state, with which one it reached
   *  so the host can tell "finished" from "failed" without re-reading the job. */
  onDone?: (status: "completed" | "failed" | "cancelled") => void;
  /** Start a fresh run after a failure or cancellation. */
  onRetry?: () => void;
  /** Suppress the completion/failure toasts (when the host surface renders
   * its own completion moment). */
  quiet?: boolean;
}

const PENDING_STUCK_THRESHOLD_MS = 30_000;

export function GenerationProgressWrapper({ jobId, repoName, onDone, onRetry, quiet }: Props) {
  const { job, sse, messages, phase } = useJob(jobId);
  const [elapsed, setElapsed] = useState(0);
  const [actualCost, setActualCost] = useState<number | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const notifiedRef = useRef(false);

  useEffect(() => {
    const updateElapsed = () => setElapsed(computeElapsedMs(job));
    updateElapsed();
    const id = setInterval(updateElapsed, 1000);
    return () => clearInterval(id);
  }, [job]);

  useEffect(() => {
    const cost = sse.data?.actual_cost_usd;
    if (cost != null) setActualCost(cost);
  }, [sse.data]);

  const log = useMemo(
    () => messages.map((m) => ({ text: m.text, level: m.level })),
    [messages],
  );

  useEffect(() => {
    if (notifiedRef.current) return;
    if (job?.status === "completed") {
      notifiedRef.current = true;
      if (!quiet) {
        toast.success(`Documentation updated${repoName ? ` — ${repoName}` : ""}`, {
          description: `${formatNumber(job.completed_pages)} pages generated`,
        });
      }
      onDone?.("completed");
    } else if (job?.status === "failed") {
      notifiedRef.current = true;
      if (!quiet) {
        toast.error("Generation failed", {
          description: job.error_message ?? "Unknown error",
        });
      }
      onDone?.("failed");
    } else if (job?.status === "cancelled") {
      notifiedRef.current = true;
      if (!quiet) {
        toast.info("Job cancelled", {
          description: "The pipeline was stopped before completion.",
        });
      }
      onDone?.("cancelled");
    }
  }, [job?.status, job?.completed_pages, job?.error_message, repoName, onDone, quiet]);

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
      // The stream/poll flips the job to "cancelled"; the status effect above
      // owns the toast so cancel-from-button and cancel-from-elsewhere match.
    } catch (e) {
      toast.error("Couldn't cancel job", {
        description: toFriendlyMessage(e),
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
      phase={phase}
      onRetry={onRetry}
      settingsHref="/settings"
    />
  );
}
