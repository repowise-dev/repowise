"use client";

import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { CheckCircle, XCircle, Loader2, RefreshCw } from "lucide-react";
import { useJob } from "@/lib/hooks/use-job";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import { JobLog } from "./job-log";
import { formatTokens, formatNumber } from "@/lib/utils/format";
import type { JobProgressEvent } from "@/lib/api/types";

interface Props {
  jobId: string;
  repoName?: string;
  /** Called when the job reaches a terminal state */
  onDone?: () => void;
}

export function GenerationProgress({ jobId, repoName, onDone }: Props) {
  const { job, sse } = useJob(jobId);
  const [log, setLog] = useState<Array<{ text: string }>>([]);
  const [elapsed, setElapsed] = useState(0);
  const [actualCost, setActualCost] = useState<number | null>(null);
  const startRef = useRef(Date.now());
  const notifiedRef = useRef(false);

  // Elapsed timer
  useEffect(() => {
    const id = setInterval(() => setElapsed(Date.now() - startRef.current), 1000);
    return () => clearInterval(id);
  }, []);

  // Accumulate log entries and track running cost from SSE progress events
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

  // Toast on terminal state
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

  const progress = job
    ? job.total_pages > 0
      ? Math.round((job.completed_pages / job.total_pages) * 100)
      : 0
    : 0;

  const elapsedStr = `${Math.floor(elapsed / 60000)}m ${Math.floor((elapsed % 60000) / 1000)}s`;
  const isRunning = job?.status === "running" || job?.status === "pending";
  const isDone = job?.status === "completed";
  const isFailed = job?.status === "failed";

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center gap-2">
        {isRunning && <Loader2 className="h-4 w-4 animate-spin text-[var(--color-accent-primary)] shrink-0" />}
        {isDone && <CheckCircle className="h-4 w-4 text-[var(--color-fresh)] shrink-0" />}
        {isFailed && <XCircle className="h-4 w-4 text-[var(--color-outdated)] shrink-0" />}

        <span className="text-sm font-medium text-[var(--color-text-primary)]">
          {isRunning && `Generating level ${job?.current_level ?? "?"}…`}
          {isDone && "Generation complete"}
          {isFailed && "Generation failed"}
        </span>

        <span className="ml-auto text-xs text-[var(--color-text-tertiary)] tabular-nums">
          {elapsedStr}
        </span>
      </div>

      {/* Progress bar */}
      <div className="space-y-1">
        <Progress
          value={progress}
          indicatorClassName={isFailed ? "bg-[var(--color-outdated)]" : undefined}
        />
        <div className="flex justify-between text-xs text-[var(--color-text-tertiary)]">
          <span>
            {formatNumber(job?.completed_pages ?? 0)} /{" "}
            {formatNumber(job?.total_pages ?? 0)} pages
          </span>
          {(job?.failed_pages ?? 0) > 0 && (
            <Badge variant="stale" className="text-xs py-0">
              {job!.failed_pages} failed
            </Badge>
          )}
          <span>{progress}%</span>
        </div>
      </div>

      {/* Live cost */}
      {actualCost != null && (
        <div className="flex items-center gap-1.5 text-xs text-[var(--color-text-tertiary)]">
          <span>Cost: ${actualCost.toFixed(4)}</span>
          {isRunning && (
            <span className="inline-flex items-center gap-0.5 rounded bg-[var(--color-accent-primary)]/15 px-1 py-px text-[10px] font-medium text-[var(--color-accent-primary)]">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--color-accent-primary)]" />
              live
            </span>
          )}
        </div>
      )}

      {/* Summary on done */}
      {isDone && (
        <div className="grid grid-cols-3 gap-2">
          <div className="rounded border border-[var(--color-border-default)] p-2 text-center">
            <p className="text-lg font-semibold text-[var(--color-text-primary)]">
              {formatNumber(job!.completed_pages)}
            </p>
            <p className="text-xs text-[var(--color-text-tertiary)]">pages</p>
          </div>
          <div className="rounded border border-[var(--color-border-default)] p-2 text-center">
            <p className="text-lg font-semibold text-[var(--color-text-primary)]">
              {formatTokens((job!.config?.total_input_tokens as number) ?? 0)}
            </p>
            <p className="text-xs text-[var(--color-text-tertiary)]">tokens in</p>
          </div>
          <div className="rounded border border-[var(--color-border-default)] p-2 text-center">
            <p className="text-lg font-semibold text-[var(--color-text-primary)]">
              {elapsedStr}
            </p>
            <p className="text-xs text-[var(--color-text-tertiary)]">elapsed</p>
          </div>
        </div>
      )}

      {/* Error */}
      {isFailed && job?.error_message && (
        <p className="text-sm text-[var(--color-outdated)]">{job.error_message}</p>
      )}

      {/* Live log */}
      {log.length > 0 && <JobLog entries={log} />}
    </div>
  );
}
