"use client";

/**
 * GraphTruncationBanner — shown when the server capped the full-graph
 * response to top-N by PageRank. Communicates the cap to the user and offers
 * a stepped "load more" escape hatch (one page of importance at a time).
 *
 * Lives in `packages/ui` so the hosted frontend can reuse it.
 */

import { AlertTriangle } from "lucide-react";
import { Button } from "../ui/button";
import { cn } from "../lib/cn";
import { formatNumber } from "../lib/format";

export const LOAD_MORE_STEP = 1500;
export const LOAD_MORE_CEILING = 6000;
const SLOW_HINT_THRESHOLD = 3000;

export interface GraphTruncationBannerProps {
  shown: number;
  total: number;
  /** Current node cap in effect; drives the next stepped target. */
  limit: number;
  /** Step the cap up by LOAD_MORE_STEP (capped at LOAD_MORE_CEILING). */
  onLoadMore?: (nextLimit: number) => void;
  /** When known, suggests a healthier scope to switch to. */
  onSwitchToArchitecture?: () => void;
  className?: string;
}

export function GraphTruncationBanner({
  shown,
  total,
  limit,
  onLoadMore,
  onSwitchToArchitecture,
  className,
}: GraphTruncationBannerProps) {
  const nextLimit = Math.min(limit + LOAD_MORE_STEP, LOAD_MORE_CEILING, total);
  const canLoadMore = nextLimit > limit;
  const slowHint = nextLimit > SLOW_HINT_THRESHOLD;

  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "flex items-center gap-3 rounded-lg border border-[var(--color-warning)]/40 bg-[var(--color-warning)]/10 px-3 py-2 text-[12px] text-[var(--color-text-primary)]",
        className,
      )}
    >
      <AlertTriangle className="h-4 w-4 shrink-0 text-[var(--color-warning)]" />
      <p className="min-w-0 flex-1">
        Showing <span className="font-semibold tabular-nums">{formatNumber(shown)}</span> of{" "}
        <span className="font-semibold tabular-nums">{formatNumber(total)}</span> by importance.
        {slowHint && canLoadMore && (
          <span className="ml-1 text-[var(--color-text-secondary)]">
            Loading more may be slow.
          </span>
        )}
      </p>
      <div className="flex shrink-0 items-center gap-2">
        {onSwitchToArchitecture && (
          <Button
            size="sm"
            variant="ghost"
            onClick={onSwitchToArchitecture}
            className="h-7 px-2 text-xs font-medium text-[var(--color-warning)] hover:bg-[var(--color-warning)]/15 hover:text-[var(--color-warning)]"
          >
            Switch to Architecture
          </Button>
        )}
        {onLoadMore && canLoadMore && (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => onLoadMore(nextLimit)}
            className="h-7 px-2 text-xs font-medium text-[var(--color-warning)] hover:bg-[var(--color-warning)]/15 hover:text-[var(--color-warning)]"
          >
            Load {formatNumber(Math.min(LOAD_MORE_STEP, nextLimit - limit))} more
          </Button>
        )}
      </div>
    </div>
  );
}
