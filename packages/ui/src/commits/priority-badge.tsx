import { cn } from "../lib/cn";
import type { ReviewPriority } from "@repowise-dev/types/git";

// Colours track review attention, not absolute danger: "Below typical" and
// "Typical" are calm (this commit is not unusual for the repo); only "Elevated"
// — the top third of the repo's own distribution — draws the eye.
const STYLES: Record<ReviewPriority, string> = {
  high: "bg-[var(--color-warning)]/15 text-[var(--color-warning)] border-[var(--color-warning)]/25",
  moderate:
    "bg-[var(--color-bg-elevated)] text-[var(--color-text-secondary)] border-[var(--color-border-default)]",
  low: "bg-[var(--color-success)]/15 text-[var(--color-success)] border-[var(--color-success)]/25",
};

// Repo-relative tercile wording — where the commit sits in *its own repo's*
// risk distribution, so a 44th-percentile commit reads "Typical", never the
// absolute-sounding "Moderate".
const LABELS: Record<ReviewPriority, string> = {
  high: "Elevated",
  moderate: "Typical",
  low: "Below typical",
};

/**
 * Review-priority pill. The priority is **repo-relative** (where the commit
 * sits in its own repo's risk distribution), not the absolute calibration band
 * — so it stays meaningful on repos whose typical commit is large.
 */
export function PriorityBadge({
  priority,
  className,
}: {
  priority: ReviewPriority;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded border px-1.5 py-0.5 text-xs font-medium tabular-nums",
        STYLES[priority],
        className,
      )}
      title="Review priority relative to this repo's own commit-risk distribution"
    >
      {LABELS[priority]}
    </span>
  );
}
