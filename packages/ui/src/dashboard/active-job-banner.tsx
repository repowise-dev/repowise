"use client";

import { Loader2, CheckCircle, XCircle, ExternalLink } from "lucide-react";
import { Progress } from "../ui/progress";
import { formatNumber, formatRelativeTime } from "../lib/format";
import type { Job } from "@repowise-dev/types/jobs";

export interface ActiveJobBannerProps {
  /**
   * Latest job for the repo, or `null` if there is none. Wrappers are
   * responsible for polling cadence + freshness window — the shell only
   * decides what to render based on the snapshot it is given.
   */
  job: Job | null;
  /**
   * Optional href for the "Details" link rendered alongside completed jobs.
   * Hosted passes `/s/{shortId}/overview`, OSS web `/repos/{repoId}/overview`.
   * If omitted, the link is suppressed.
   */
  detailsHref?: string;
}

const PHASE_LABELS: Record<number, string> = {
  0: "Indexing",
  1: "Analysing",
  2: "Generating docs",
};

export function ActiveJobBanner({ job, detailsHref }: ActiveJobBannerProps) {
  if (!job) return null;
  if (job.status === "pending") return null;

  const isRunning = job.status === "running";
  const isDone = job.status === "completed";
  const isFailed = job.status === "failed";

  const progress =
    job.total_pages > 0
      ? Math.min(100, Math.round((job.completed_pages / job.total_pages) * 100))
      : 0;

  const mode = (job.config?.mode as string) ?? "sync";
  const label = mode === "full_resync" ? "Full Re-index" : "Sync";
  const phaseLabel = PHASE_LABELS[job.current_level ?? 0] ?? "Processing";

  return (
    <div className="px-4 py-2 border-b border-[var(--color-border-default)] bg-[var(--color-bg-inset)]">
      <div className="flex items-center gap-3">
        {isRunning && <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--color-accent-primary)] shrink-0" />}
        {isDone && <CheckCircle className="h-3.5 w-3.5 text-[var(--color-success)] shrink-0" />}
        {isFailed && <XCircle className="h-3.5 w-3.5 text-[var(--color-error)] shrink-0" />}

        <span className="text-xs text-[var(--color-text-secondary)]">
          {isRunning && `${label} · ${phaseLabel}…`}
          {isDone && `${label} complete`}
          {isFailed && `${label} failed`}
        </span>

        {isRunning && (
          <div className="flex-1 max-w-[200px]">
            <Progress value={progress} className="h-1.5" />
          </div>
        )}

        <span className="text-[10px] text-[var(--color-text-tertiary)] tabular-nums">
          {isRunning && job.total_pages > 0 && `${formatNumber(job.completed_pages)}/${formatNumber(job.total_pages)}`}
          {isDone && (
            <>
              {formatNumber(job.completed_pages)} pages
              {job.config?.total_input_tokens != null && (
                <> · {formatNumber(job.config.total_input_tokens as number)} tokens</>
              )}
              {job.finished_at && <> · {formatRelativeTime(job.finished_at)}</>}
            </>
          )}
          {isFailed && (job.error_message ?? "Unknown error")}
        </span>

        {isDone && job.completed_pages > 0 && detailsHref && (
          <a
            href={detailsHref}
            className="text-[10px] text-[var(--color-accent-primary)] hover:underline flex items-center gap-0.5 shrink-0"
          >
            Details <ExternalLink className="h-2.5 w-2.5" />
          </a>
        )}
      </div>
    </div>
  );
}
