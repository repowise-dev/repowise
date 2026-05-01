"use client";

import { CheckCircle, XCircle, Loader2, AlertTriangle, X } from "lucide-react";
import { Progress } from "../ui/progress.js";
import { Badge } from "../ui/badge.js";
import { Button } from "../ui/button.js";
import { JobLog } from "./job-log.js";
import { formatTokens, formatNumber } from "../lib/format.js";

/** Subset of the Job shape this presentational shell renders. */
export interface GenerationProgressJob {
  id: string;
  status: "pending" | "running" | "completed" | "failed" | string;
  total_pages: number;
  completed_pages: number;
  failed_pages?: number;
  current_level?: number | null;
  started_at?: string | null;
  error_message?: string | null;
  config?: Record<string, unknown> | null;
}

export interface GenerationProgressProps {
  job: GenerationProgressJob | undefined;
  log: Array<{ text: string }>;
  /** Wall-clock elapsed time in ms. The wrapper owns the interval timer. */
  elapsed: number;
  /** Live cost in USD (null until first SSE progress event with cost). */
  actualCost: number | null;
  /** True when the job has been pending past the stuck-threshold and never started. */
  stuckPending: boolean;
  cancelling: boolean;
  onCancel: () => void;
}

export function GenerationProgress({
  job,
  log,
  elapsed,
  actualCost,
  stuckPending,
  cancelling,
  onCancel,
}: GenerationProgressProps) {
  const progress = job
    ? job.total_pages > 0
      ? Math.round((job.completed_pages / job.total_pages) * 100)
      : 0
    : 0;

  const elapsedStr = `${Math.floor(elapsed / 60000)}m ${Math.floor((elapsed % 60000) / 1000)}s`;
  const isPending = job?.status === "pending";
  const isRunning = job?.status === "running";
  const isInflight = isPending || isRunning;
  const isDone = job?.status === "completed";
  const isFailed = job?.status === "failed";

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        {isInflight && <Loader2 className="h-4 w-4 animate-spin text-[var(--color-accent-primary)] shrink-0" />}
        {isDone && <CheckCircle className="h-4 w-4 text-[var(--color-fresh)] shrink-0" />}
        {isFailed && <XCircle className="h-4 w-4 text-[var(--color-outdated)] shrink-0" />}

        <span className="text-sm font-medium text-[var(--color-text-primary)]">
          {isPending && "Queued — waiting for worker…"}
          {isRunning && `Generating level ${job?.current_level ?? "?"}…`}
          {isDone && "Generation complete"}
          {isFailed && "Generation failed"}
        </span>

        <span className="ml-auto text-xs text-[var(--color-text-tertiary)] tabular-nums">
          {elapsedStr}
        </span>

        {isInflight && (
          <Button
            size="sm"
            variant="ghost"
            onClick={onCancel}
            disabled={cancelling}
            className="h-7 px-2 text-xs"
            aria-label="Cancel job"
          >
            <X className="h-3.5 w-3.5 mr-1" />
            {cancelling ? "Cancelling…" : "Cancel"}
          </Button>
        )}
      </div>

      {stuckPending && (
        <div className="flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
          <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
          <div className="flex-1">
            <p className="font-medium">Job hasn&apos;t started after {Math.round(elapsed / 1000)}s.</p>
            <p className="mt-0.5 opacity-80">
              The server may have crashed before the worker could pick it up. Cancel
              this job and try again — if it keeps happening, check the server logs.
            </p>
          </div>
        </div>
      )}

      <div className="space-y-1">
        <Progress
          value={progress}
          {...(isFailed ? { indicatorClassName: "bg-[var(--color-outdated)]" } : {})}
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

      {isDone && job && (
        <div className="grid grid-cols-3 gap-2">
          <div className="rounded border border-[var(--color-border-default)] p-2 text-center">
            <p className="text-lg font-semibold text-[var(--color-text-primary)]">
              {formatNumber(job.completed_pages)}
            </p>
            <p className="text-xs text-[var(--color-text-tertiary)]">pages</p>
          </div>
          <div className="rounded border border-[var(--color-border-default)] p-2 text-center">
            <p className="text-lg font-semibold text-[var(--color-text-primary)]">
              {formatTokens((job.config?.total_input_tokens as number) ?? 0)}
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

      {isFailed && job?.error_message && (
        <p className="text-sm text-[var(--color-outdated)]">{job.error_message}</p>
      )}

      {log.length > 0 && <JobLog entries={log} />}
    </div>
  );
}
