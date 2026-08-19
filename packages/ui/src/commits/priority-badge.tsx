import { cn } from "../lib/cn";
import type { ReviewPriority } from "@repowise-dev/types/git";

// Colours track review attention, not absolute danger: "Below typical" and
// "Typical" are calm (this commit is not unusual for the repo); only
// "Elevated", the top third of the repo's own distribution, draws the eye.
//
// All three lost their fill. The queue defaults to risk-sorted, so the first
// screen is by construction almost entirely "Elevated": a tinted amber pill on
// every row stopped marking anything and just turned the leftmost column into
// a block of colour competing with the subjects beside it. A dot plus a word
// carries the same three-way distinction at a fraction of the ink, and the
// pill still reads as elevated when one turns up further down a date-sorted
// page, which is where it actually needs to catch the eye.
const STYLES: Record<ReviewPriority, string> = {
  high: "text-[var(--color-warning)]",
  moderate: "text-[var(--color-text-tertiary)]",
  low: "text-[var(--color-text-tertiary)]",
};

const DOTS: Record<ReviewPriority, string> = {
  high: "bg-[var(--color-warning)]",
  moderate: "bg-[var(--color-border-hover)]",
  low: "bg-[var(--color-success)]/60",
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
        "inline-flex items-center gap-1.5 whitespace-nowrap text-xs font-medium",
        STYLES[priority],
        className,
      )}
      title="Review priority relative to this repo's own commit-risk distribution"
    >
      <span
        aria-hidden
        className={cn("h-1.5 w-1.5 shrink-0 rounded-full", DOTS[priority])}
      />
      {LABELS[priority]}
    </span>
  );
}
