import * as React from "react";
import { Button } from "../ui/button";
import { cn } from "../lib/cn";

interface ResultsFooterProps {
  /** Number of items currently rendered in the parent component. */
  shown: number;
  /** Authoritative total reported by the server. */
  total: number;
  /** True when more pages are available. */
  hasMore: boolean;
  /** Disables the load-more button while the next page is in flight. */
  loading?: boolean;
  /**
   * Called when the user requests more rows. The parent decides the page
   * size; this primitive only owns the affordance and the count surface.
   */
  onLoadMore?: () => void;
  /** Optional label tweak — defaults to "results". */
  noun?: string;
  className?: string;
}

/**
 * Footer for any list that pages through a {@link Paginated} envelope. The
 * goal is to never let the UI silently hide data: the count makes the
 * "shown N of M" contract explicit even when the list happens to fit on
 * one page.
 */
export function ResultsFooter({
  shown,
  total,
  hasMore,
  loading = false,
  onLoadMore,
  noun = "results",
  className,
}: ResultsFooterProps) {
  if (total === 0) return null;
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-3 border-t border-[var(--color-border)] px-3 py-2 text-xs text-[var(--color-text-secondary)]",
        className,
      )}
    >
      <span className="tabular-nums">
        Showing <span className="font-medium text-[var(--color-text-primary)]">{shown.toLocaleString()}</span>
        {" "}of{" "}
        <span className="font-medium text-[var(--color-text-primary)]">{total.toLocaleString()}</span>
        {" "}{noun}
      </span>
      {hasMore && onLoadMore && (
        <Button size="sm" variant="outline" onClick={onLoadMore} disabled={loading}>
          {loading ? "Loading…" : "Load more"}
        </Button>
      )}
    </div>
  );
}
