import { Bug } from "lucide-react";
import { Badge } from "../ui/badge";
import { summarizeFixHistory } from "../lib/fix-history";

export interface FixHistoryBadgeProps {
  /** Counted bug fixes in the trailing defect window. */
  count?: number | null;
  /** Timestamp of the most recent counted fix, if known. */
  lastFixAt?: string | null;
  /** Decayed fix mass past its trigger. Ignored unless an age is available. */
  bugMagnet?: boolean | null;
  /** Override the tooltip — symbol-level counts hedge, file-level ones don't. */
  title?: string;
  className?: string;
}

const DEFAULT_TITLE =
  "Bug-fix commits that touched this file in the trailing defect window.";

/** Symbol-level counts are matched by line range, so their copy hedges. */
export const SYMBOL_FIX_TITLE =
  "Bug fixes that landed in this symbol in the trailing defect window (approximate: matched by line range).";

/**
 * The headline "N fixes, last X ago" pill. Renders nothing when the file has
 * no counted fixes, and drops the magnet wording when there is no timestamp to
 * anchor it — both rules live in {@link summarizeFixHistory}. Aggregate counts
 * only: no inducing commit is ever named.
 */
export function FixHistoryBadge({
  count,
  lastFixAt,
  bugMagnet,
  title,
  className,
}: FixHistoryBadgeProps) {
  const fix = summarizeFixHistory(count, lastFixAt, bugMagnet);
  if (!fix) return null;

  return (
    <Badge
      variant={fix.magnet ? "outdated" : "outline"}
      className={className}
      title={title ?? DEFAULT_TITLE}
    >
      <Bug className="h-2.5 w-2.5" />
      {fix.magnet ? `Bug magnet · ${fix.label}` : fix.label}
    </Badge>
  );
}
