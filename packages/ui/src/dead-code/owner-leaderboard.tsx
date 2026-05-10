"use client";

import * as React from "react";
import { cn } from "../lib/cn";

export interface OwnerLeaderboardFinding {
  primary_owner: string | null;
  lines: number;
  safe_to_delete?: boolean;
}

interface OwnerLeaderboardProps {
  findings: OwnerLeaderboardFinding[];
  /** Maximum bars to render. Default 8. */
  topN?: number;
  /** Only count safe_to_delete findings. */
  safeOnly?: boolean;
  className?: string;
  onSelect?: (owner: string) => void;
}

/**
 * Bar chart of dead-code lines owned per primary contributor — shows who has
 * the most cleanup leverage. Renders inline (no recharts dep) so the chart
 * stays cheap and resilient to data shape.
 */
export function OwnerLeaderboard({
  findings,
  topN = 8,
  safeOnly = false,
  className,
  onSelect,
}: OwnerLeaderboardProps) {
  const tallies = React.useMemo(() => {
    const map = new Map<string, { lines: number; count: number }>();
    for (const f of findings) {
      if (safeOnly && !f.safe_to_delete) continue;
      const owner = f.primary_owner?.trim() || "Unowned";
      const prev = map.get(owner) ?? { lines: 0, count: 0 };
      prev.lines += Number.isFinite(f.lines) ? f.lines : 0;
      prev.count += 1;
      map.set(owner, prev);
    }
    return Array.from(map.entries())
      .map(([owner, v]) => ({ owner, ...v }))
      .sort((a, b) => b.lines - a.lines)
      .slice(0, topN);
  }, [findings, safeOnly, topN]);

  const max = tallies[0]?.lines ?? 0;

  if (tallies.length === 0) {
    return (
      <div
        className={cn(
          "rounded-md border border-dashed border-[var(--color-border-default)] p-4 text-center text-xs text-[var(--color-text-tertiary)]",
          className,
        )}
      >
        No owner attribution yet — run analysis to populate.
      </div>
    );
  }

  return (
    <ul className={cn("space-y-2", className)}>
      {tallies.map((t) => {
        const widthPct = max > 0 ? Math.max((t.lines / max) * 100, 4) : 0;
        const Tag = onSelect ? "button" : "div";
        return (
          <li key={t.owner}>
            <Tag
              type={onSelect ? "button" : undefined}
              onClick={onSelect ? () => onSelect(t.owner) : undefined}
              className={cn(
                "block w-full text-left",
                onSelect && "transition hover:opacity-90",
              )}
            >
              <div className="flex items-baseline justify-between gap-3 text-xs">
                <span className="truncate text-[var(--color-text-secondary)]" title={t.owner}>
                  {t.owner}
                </span>
                <span className="shrink-0 font-mono tabular-nums text-[var(--color-text-tertiary)]">
                  {t.lines.toLocaleString()} lines · {t.count}
                </span>
              </div>
              <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-[var(--color-bg-elevated)]">
                <div
                  className="h-full rounded-full bg-rose-400/60 transition-[width] duration-300"
                  style={{ width: `${widthPct}%` }}
                />
              </div>
            </Tag>
          </li>
        );
      })}
    </ul>
  );
}
