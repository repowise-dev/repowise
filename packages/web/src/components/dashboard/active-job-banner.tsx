"use client";

import useSWR from "swr";
import Link from "next/link";
import { Loader2, CheckCircle, XCircle, ExternalLink } from "lucide-react";
import { Progress } from "@/components/ui/progress";
import { listJobs } from "@/lib/api/jobs";
import { formatNumber, formatRelativeTime } from "@/lib/utils/format";
import type { JobResponse } from "@/lib/api/types";

interface ActiveJobBannerProps {
  repoId: string;
}

export function ActiveJobBanner({ repoId }: ActiveJobBannerProps) {
  const { data: jobs } = useSWR<JobResponse[]>(
    `/api/jobs?repo_id=${repoId}&limit=1`,
    () => listJobs({ repo_id: repoId, limit: 1 }),
    { refreshInterval: (data) => {
      const j = data?.[0];
      if (j?.status === "running") return 5000;  // poll faster when running
      return 30000;  // slow poll otherwise
    }},
  );

  const job = jobs?.[0];
  if (!job) return null;

  const isRunning = job.status === "running";
  const isDone = job.status === "completed";
  const isFailed = job.status === "failed";

  // Don't show pending jobs (they may never start) or old terminal states
  if (job.status === "pending") return null;
  if (!isRunning) {
    const finishedAt = job.finished_at ? new Date(job.finished_at).getTime() : 0;
    const age = Date.now() - finishedAt;
    if (age > 60_000) return null;
  }

  const progress = job.total_pages > 0
    ? Math.min(100, Math.round((job.completed_pages / job.total_pages) * 100))
    : 0;

  const mode = (job.config?.mode as string) ?? "sync";
  const label = mode === "full_resync" ? "Full Re-index" : "Sync";

  const PHASE_LABELS: Record<number, string> = {
    0: "Indexing",
    1: "Analysing",
    2: "Generating docs",
  };
  const phaseLabel = PHASE_LABELS[job.current_level ?? 0] ?? "Processing";

  return (
    <div className="px-4 py-2 border-b border-[var(--color-border-default)] bg-[var(--color-bg-inset)]">
      <div className="flex items-center gap-3">
        {/* Status icon */}
        {isRunning && <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--color-accent-primary)] shrink-0" />}
        {isDone && <CheckCircle className="h-3.5 w-3.5 text-[var(--color-success)] shrink-0" />}
        {isFailed && <XCircle className="h-3.5 w-3.5 text-[var(--color-error)] shrink-0" />}

        {/* Label */}
        <span className="text-xs text-[var(--color-text-secondary)]">
          {isRunning && `${label} · ${phaseLabel}…`}
          {isDone && `${label} complete`}
          {isFailed && `${label} failed`}
        </span>

        {/* Progress bar (running only) */}
        {isRunning && (
          <div className="flex-1 max-w-[200px]">
            <Progress value={progress} className="h-1.5" />
          </div>
        )}

        {/* Stats */}
        <span className="text-[10px] text-[var(--color-text-tertiary)] tabular-nums">
          {isRunning && job.total_pages > 0 && `${formatNumber(job.completed_pages)}/${formatNumber(job.total_pages)}`}
          {isDone && (
            <>
              {formatNumber(job.completed_pages)} pages
              {job.config?.total_input_tokens && (
                <> · {formatNumber(job.config.total_input_tokens as number)} tokens</>
              )}
              {job.finished_at && <> · {formatRelativeTime(job.finished_at)}</>}
            </>
          )}
          {isFailed && (job.error_message ?? "Unknown error")}
        </span>

        {/* Link to overview for details */}
        {isDone && job.completed_pages > 0 && (
          <Link
            href={`/repos/${repoId}/overview`}
            className="text-[10px] text-[var(--color-accent-primary)] hover:underline flex items-center gap-0.5 shrink-0"
          >
            Details <ExternalLink className="h-2.5 w-2.5" />
          </Link>
        )}
      </div>
    </div>
  );
}
