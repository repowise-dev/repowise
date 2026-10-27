"use client";

import { AlertTriangle, CheckCircle, XCircle, ExternalLink, Settings, X } from "lucide-react";
import { Spinner } from "../ui/spinner";
import { Progress } from "../ui/progress";
import { formatNumber, formatRelativeTime } from "../lib/format";
import type { Job } from "@repowise-dev/types/jobs";
import { jobOutcome, providerIssue, type ProviderIssue } from "../jobs/job-outcome";

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
  /** Where the repo's provider is set up; linked when a failure or warning
   *  is the provider's. */
  settingsHref?: string;
  /** Hides a failed or warning banner; a clean finish fades on its own. */
  onDismiss?: () => void;
  /** Words a provider issue in the host's language; defaults to its message. */
  formatIssue?: (issue: ProviderIssue) => string;
  labels?: Partial<ActiveJobBannerLabels>;
}

export interface ActiveJobBannerLabels {
  finishedWithProblems: (action: string) => string;
  pagesFailed: (count: number) => string;
  providerSettings: string;
  dismiss: string;
}

export const DEFAULT_ACTIVE_JOB_BANNER_LABELS: ActiveJobBannerLabels = {
  finishedWithProblems: (action) => `${action} finished with problems`,
  pagesFailed: (count) => `${formatNumber(count)} pages not written`,
  providerSettings: "Provider settings",
  dismiss: "Dismiss",
};

const PHASE_LABELS: Record<number, string> = {
  0: "Indexing",
  1: "Analysing",
  2: "Generating docs",
};

export function ActiveJobBanner({
  job,
  detailsHref,
  settingsHref,
  onDismiss,
  formatIssue = (issue) => issue.message,
  labels,
}: ActiveJobBannerProps) {
  if (!job) return null;
  if (job.status === "pending") return null;

  const l = { ...DEFAULT_ACTIVE_JOB_BANNER_LABELS, ...labels };
  const outcome = jobOutcome(job);
  const isRunning = job.status === "running";
  const isDone = outcome === "completed";
  const isWarning = outcome === "warning";
  const isFailed = outcome === "failed";
  const issue = providerIssue(job);
  const isProblem = isFailed || isWarning;

  const progress =
    job.total_pages > 0
      ? Math.min(100, Math.round((job.completed_pages / job.total_pages) * 100))
      : 0;

  const mode = (job.config?.mode as string) ?? "sync";
  const label = mode === "full_resync" ? "Full Re-index" : "Sync";
  const phaseLabel = PHASE_LABELS[job.current_level ?? 0] ?? "Processing";

  return (
    <div
      // A failure interrupts; a warning is announced politely.
      role={isFailed ? "alert" : isWarning ? "status" : undefined}
      className="border-b border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-1.5 sm:px-[var(--page-pad)]"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        {isRunning ? <Spinner size="sm" className="text-[var(--color-text-tertiary)]" /> : <OutcomeIcon outcome={outcome} />}

        <span className="text-xs text-[var(--color-text-secondary)]">
          {isRunning && `${label} · ${phaseLabel}…`}
          {isDone && `${label} complete`}
          {isWarning && l.finishedWithProblems(label)}
          {isFailed && `${label} failed`}
        </span>

        {isRunning && (
          <div className="w-24 sm:w-48">
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
          {isWarning && (issue ? formatIssue(issue) : l.pagesFailed(job.failed_pages))}
          {isFailed && (issue ? formatIssue(issue) : (job.error_message ?? "Unknown error"))}
        </span>

        {isProblem && issue && settingsHref && (
          <a
            href={settingsHref}
            className="text-[10px] text-[var(--color-accent-primary)] hover:underline flex items-center gap-0.5 shrink-0"
          >
            <Settings className="h-2.5 w-2.5" /> {l.providerSettings}
          </a>
        )}

        {isDone && job.completed_pages > 0 && detailsHref && (
          <a
            href={detailsHref}
            className="text-[10px] text-[var(--color-accent-primary)] hover:underline flex items-center gap-0.5 shrink-0"
          >
            Details <ExternalLink className="h-2.5 w-2.5" />
          </a>
        )}

        {isProblem && onDismiss && <DismissButton label={l.dismiss} onDismiss={onDismiss} />}
      </div>
    </div>
  );
}

function OutcomeIcon({ outcome }: { outcome: ReturnType<typeof jobOutcome> }) {
  if (outcome === "completed") {
    return <CheckCircle className="h-3.5 w-3.5 text-[var(--color-success)] shrink-0" />;
  }
  if (outcome === "warning") {
    return <AlertTriangle className="h-3.5 w-3.5 text-[var(--color-warning)] shrink-0" />;
  }
  if (outcome === "failed") {
    return <XCircle className="h-3.5 w-3.5 text-[var(--color-error)] shrink-0" />;
  }
  return null;
}

function DismissButton({ label, onDismiss }: { label: string; onDismiss: () => void }) {
  return (
    <button
      type="button"
      onClick={onDismiss}
      aria-label={label}
      className="ml-auto rounded p-0.5 text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-focus-ring)]"
    >
      <X className="h-3 w-3" />
    </button>
  );
}
