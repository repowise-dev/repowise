import Link from "next/link";
import { formatRelativeTime } from "@repowise-dev/ui/lib/format";
import type { JobResponse } from "@/lib/api/types";
import { LiveJobProgress } from "@/components/jobs/live-job-progress";

/** Status as a dot plus the word, on the success/error/neutral set only.
 *  A filled pill per row tiles into stripes down a list and outweighs the
 *  thing it labels. */
const STATUS_INK: Record<string, string> = {
  completed: "var(--color-success)",
  failed: "var(--color-error)",
  running: "var(--color-accent-primary)",
  pending: "var(--color-text-tertiary)",
  cancelled: "var(--color-text-tertiary)",
  paused: "var(--color-text-tertiary)",
};

export function JobRows({
  jobs,
  nameFor,
}: {
  jobs: JobResponse[];
  /** Repo name for a job's repository id, so a row says which repo it ran on
   *  — the old list showed a model name and a page count with no subject. */
  nameFor: (repositoryId: string) => string | null;
}) {
  if (jobs.length === 0) return null;

  return (
    <ul className="m-0 list-none divide-y divide-[var(--color-border-default)] border-t border-[var(--color-border-default)] p-0">
      {jobs.map((job) => {
        const live = job.status === "running" || job.status === "pending";
        const ink = STATUS_INK[job.status] ?? "var(--color-text-tertiary)";
        const repoName = nameFor(job.repository_id);

        return (
          <li key={job.id}>
            <Link
              href={`/repos/${job.repository_id}/overview`}
              className="flex flex-col gap-1 py-2.5 no-underline transition-colors hover:bg-[var(--color-bg-wash-hover)] sm:flex-row sm:items-baseline sm:gap-4"
            >
              <span className="flex min-w-0 shrink-0 items-center gap-2 sm:w-56">
                <span
                  aria-hidden
                  className="h-1.5 w-1.5 shrink-0 rounded-full"
                  style={{ background: ink }}
                />
                <span className="text-[13px] font-medium text-[var(--color-text-primary)]">
                  {repoName ?? "Unknown repository"}
                </span>
                <span className="text-[11px]" style={{ color: ink }}>
                  {job.status}
                </span>
              </span>

              <span className="min-w-0 flex-1 text-xs tabular-nums text-[var(--color-text-secondary)]">
                {live ? (
                  <LiveJobProgress
                    jobId={job.id}
                    initialCompleted={job.completed_pages}
                    initialTotal={job.total_pages}
                  />
                ) : (
                  `${job.total_pages.toLocaleString()} pages`
                )}
                {job.model_name && (
                  <span className="text-[var(--color-text-tertiary)]"> · {job.model_name}</span>
                )}
              </span>

              <span className="shrink-0 text-xs text-[var(--color-text-tertiary)]">
                {formatRelativeTime(job.updated_at)}
              </span>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
