"use client";

/**
 * GraphTruncationBanner — states the bound when the server capped the
 * full-graph response to top-N by PageRank, and offers a stepped "load more"
 * escape hatch (one page of importance at a time).
 *
 * A cap is normal on any large repo, not a fault, so this reads as a status
 * line rather than a warning: no amber fill, no alert glyph, no border box. It
 * used to spend all three on a message that fires on every big-repo load, and
 * sat directly above the canvas it was describing. Rule 10 — a marker means
 * there is something to do.
 *
 * Lives in `packages/ui` so the hosted frontend can reuse it.
 */

import { cn } from "../lib/cn";
import { formatNumber } from "../lib/format";

export const LOAD_MORE_STEP = 1500;

/**
 * Highest node cap the "load more" ladder will offer.
 *
 * Was 6,000 — a number nothing had rendered, promised while the canvas was
 * still spending 8-12 seconds in an FA2 worker on every load. The layout is
 * seeded-and-painted now (no worker), and the largest graph actually measured
 * end to end is this repo's whole file set: 3,192 nodes / 13,216 edges, which
 * paints immediately. 3,000 is inside that, and it is exactly two presses of
 * the 1,500 step. Raising it again is a benchmark, not an edit.
 */
export const LOAD_MORE_CEILING = 3000;

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

const linkClass =
  "shrink-0 font-medium text-[var(--color-accent-primary)] hover:underline";

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
        "flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[12px] text-[var(--color-text-secondary)]",
        className,
      )}
    >
      <p className="min-w-0 flex-1">
        Showing the{" "}
        <span className="font-medium tabular-nums text-[var(--color-text-primary)]">
          {formatNumber(shown)}
        </span>{" "}
        most-connected files of{" "}
        <span className="font-medium tabular-nums text-[var(--color-text-primary)]">
          {formatNumber(total)}
        </span>
        .
        {slowHint && canLoadMore && " Loading more will be slower."}
      </p>
      {onSwitchToArchitecture && (
        <button type="button" onClick={onSwitchToArchitecture} className={linkClass}>
          See all of them grouped
        </button>
      )}
      {onLoadMore && canLoadMore && (
        <button
          type="button"
          onClick={() => onLoadMore(nextLimit)}
          className={linkClass}
        >
          Load {formatNumber(Math.min(LOAD_MORE_STEP, nextLimit - limit))} more
        </button>
      )}
    </div>
  );
}
