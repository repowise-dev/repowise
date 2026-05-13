"use client";

/**
 * GraphTruncationBanner — shown when the server capped the full-graph
 * response to top-N by PageRank. Communicates the cap to the user and offers
 * an escape hatch ("load all").
 *
 * Lives in `packages/ui` so the hosted frontend can reuse it.
 */

import { AlertTriangle } from "lucide-react";
import { Button } from "../ui/button";
import { cn } from "../lib/cn";
import { formatNumber } from "../lib/format";

export interface GraphTruncationBannerProps {
  shown: number;
  total: number;
  onLoadAll?: () => void;
  /** When known, suggests a healthier scope to switch to. */
  onSwitchToArchitecture?: () => void;
  className?: string;
}

export function GraphTruncationBanner({
  shown,
  total,
  onLoadAll,
  onSwitchToArchitecture,
  className,
}: GraphTruncationBannerProps) {
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "flex items-center gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[12px] text-amber-300",
        className,
      )}
    >
      <AlertTriangle className="h-4 w-4 shrink-0" />
      <p className="min-w-0 flex-1">
        Showing <span className="font-semibold tabular-nums">{formatNumber(shown)}</span> of{" "}
        <span className="font-semibold tabular-nums">{formatNumber(total)}</span> nodes — the
        full graph was capped for performance.
      </p>
      <div className="flex shrink-0 items-center gap-2">
        {onSwitchToArchitecture && (
          <Button
            size="sm"
            variant="ghost"
            onClick={onSwitchToArchitecture}
            className="h-7 px-2 text-[11px] text-amber-200 hover:bg-amber-500/20 hover:text-amber-100"
          >
            Switch to Architecture
          </Button>
        )}
        {onLoadAll && (
          <Button
            size="sm"
            variant="ghost"
            onClick={onLoadAll}
            className="h-7 px-2 text-[11px] text-amber-200 hover:bg-amber-500/20 hover:text-amber-100"
          >
            Load all
          </Button>
        )}
      </div>
    </div>
  );
}
