import { cn } from "../lib/cn.js";
import type { ReviewPriority } from "@repowise-dev/types/git";

const STYLES: Record<ReviewPriority, string> = {
  high: "bg-red-500/15 text-red-400 border-red-500/25",
  moderate: "bg-yellow-500/15 text-yellow-400 border-yellow-500/25",
  low: "bg-green-500/15 text-green-400 border-green-500/25",
};

const LABELS: Record<ReviewPriority, string> = {
  high: "High",
  moderate: "Moderate",
  low: "Low",
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
