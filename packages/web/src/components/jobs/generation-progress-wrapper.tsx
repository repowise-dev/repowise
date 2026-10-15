"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import {
  GenerationProgress,
  jobOutcome,
  providerIssue,
  type GenerationProgressLabels,
  type JobOutcome,
} from "@repowise-dev/ui/jobs";
import { useJob } from "@/lib/hooks/use-job";
import { cancelJob } from "@/lib/api/jobs";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import { computeElapsedMs } from "@/lib/jobs/progress";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import { providerSetupHref } from "@/lib/utils/page-href";
import { useIssueText } from "@/lib/jobs/use-issue-text";
import { useTranslations } from "next-intl";

interface Props {
  jobId: string;
  repoName?: string;
  /** Fired once the job reaches a terminal state, with how it turned out so
   *  the host can tell "finished" from "finished with problems" or "failed"
   *  without re-reading the job. */
  onDone?: (outcome: JobOutcome) => void;
  /** Start a fresh run after a failure or cancellation. */
  onRetry?: () => void;
  /** Offered on a finished panel; hosts that keep failures on screen clear it here. */
  onDismiss?: () => void;
  /** Suppress the completion/failure toasts (when the host surface renders
   * its own completion moment). */
  quiet?: boolean;
}

const PENDING_STUCK_THRESHOLD_MS = 30_000;

export function GenerationProgressWrapper({
  jobId,
  repoName,
  onDone,
  onRetry,
  onDismiss,
  quiet,
}: Props) {
  const t = useTranslations("jobs");
  const { job, sse, messages, phase, isActive } = useJob(jobId);
  const issueText = useIssueText();
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

  // Settle on the fetched job, not the stream's "done" frame: only the fetch
  // carries the run summary that says whether pages failed.
  const outcome = isActive ? null : jobOutcome(job);
  useEffect(() => {
    if (notifiedRef.current || !outcome || !job) return;
    notifiedRef.current = true;
    const issue = providerIssue(job);
    if (!quiet) {
      if (outcome === "completed") {
        toast.success(t("completed", { name: repoName ? ` — ${repoName}` : "" }), {
          description: t("pagesGenerated", { count: formatNumber(job.completed_pages) }),
        });
      } else if (outcome === "warning") {
        toast.warning(t("completedWithProblems", { name: repoName ? ` (${repoName})` : "" }), {
          description: issue
            ? issueText(issue)
            : t("pagesFailed", { count: formatNumber(job.failed_pages) }),
        });
      } else if (outcome === "failed") {
        toast.error(t("failed"), {
          description: issue ? issueText(issue) : (job.error_message ?? t("unknownError")),
        });
      } else {
        toast.info(t("cancelled"), { description: t("cancelledDescription") });
      }
    }
    onDone?.(outcome);
  }, [outcome, job, repoName, onDone, quiet, t, issueText]);

  const labels = useMemo<Partial<GenerationProgressLabels>>(
    () => ({
      warningTitle: t("progress.warningTitle"),
      noPagesWritten: t("progress.noPagesWritten"),
      somePagesFailed: (failed, total) =>
        t("progress.somePagesFailed", {
          failed: formatNumber(failed),
          total: formatNumber(total),
        }),
      docsSkipped: t("progress.docsSkipped"),
      retry: t("progress.retry"),
      startAgain: t("progress.startAgain"),
      providerSettings: t("progress.providerSettings"),
      dismiss: t("progress.dismiss"),
    }),
    [t],
  );

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
      toast.error(t("cancelFailed"), {
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
      onDismiss={onDismiss}
      settingsHref={job ? providerSetupHref(job.repository_id) : undefined}
      labels={labels}
      formatIssue={issueText}
    />
  );
}
