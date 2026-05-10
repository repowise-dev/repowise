"use client";

import * as React from "react";
import { TrendingUp, TrendingDown, Minus } from "lucide-react";
import { cn } from "../lib/cn";

export interface TrendStripHotspot {
  file_path: string;
  churn_percentile: number;
  commit_count_90d: number;
  commit_count_30d: number;
  bus_factor: number;
  temporal_hotspot_score?: number | null;
  primary_owner?: string | null;
}

interface HotspotTrendStripProps {
  hotspots: TrendStripHotspot[];
  topN?: number;
  className?: string;
  onSelect?: (filePath: string) => void;
}

type Trend = "heating" | "cooling" | "stable";

/**
 * Derive heating/cooling vs the 90d baseline from the 30d window. We extrapolate
 * the 30d count to a 90d-equivalent (×3) and compare against the actual 90d
 * count: significantly higher means heating, lower means cooling.
 */
function trendFor(h: TrendStripHotspot): Trend {
  if (h.commit_count_90d === 0) return "stable";
  const expected = h.commit_count_30d * 3;
  const ratio = expected / h.commit_count_90d;
  if (ratio >= 1.25) return "heating";
  if (ratio <= 0.75) return "cooling";
  return "stable";
}

/**
 * Compact list of top hotspots with churn percentile + heating/cooling badge.
 * Designed to sit next to a treemap and tell the "what's getting worse" story
 * at a glance.
 */
export function HotspotTrendStrip({
  hotspots,
  topN = 8,
  className,
  onSelect,
}: HotspotTrendStripProps) {
  const items = React.useMemo(
    () => [...hotspots].sort((a, b) => b.churn_percentile - a.churn_percentile).slice(0, topN),
    [hotspots, topN],
  );

  if (items.length === 0) {
    return (
      <div
        className={cn(
          "rounded-md border border-dashed border-[var(--color-border-default)] p-4 text-center text-xs text-[var(--color-text-tertiary)]",
          className,
        )}
      >
        No hotspots yet.
      </div>
    );
  }

  return (
    <ul className={cn("divide-y divide-[var(--color-border-default)]", className)}>
      {items.map((h) => {
        const trend = trendFor(h);
        const Tag = onSelect ? "button" : "div";
        return (
          <li key={h.file_path}>
            <Tag
              type={onSelect ? "button" : undefined}
              onClick={onSelect ? () => onSelect(h.file_path) : undefined}
              className={cn(
                "flex w-full items-center gap-3 px-2 py-2 text-left text-xs",
                onSelect && "transition hover:bg-[var(--color-bg-elevated)]",
              )}
            >
              <TrendBadge trend={trend} />
              <span
                className="flex-1 truncate font-mono text-[11px] text-[var(--color-text-primary)]"
                title={h.file_path}
              >
                {h.file_path}
              </span>
              <span className="shrink-0 tabular-nums text-[var(--color-text-tertiary)]">
                {Math.round(h.churn_percentile)}th %ile
              </span>
              {h.bus_factor <= 1 && (
                <span className="shrink-0 rounded-full border border-red-500/40 bg-red-500/10 px-1.5 py-0.5 text-[10px] font-medium text-red-300">
                  bus×1
                </span>
              )}
            </Tag>
          </li>
        );
      })}
    </ul>
  );
}

function TrendBadge({ trend }: { trend: Trend }) {
  if (trend === "heating") {
    return (
      <span className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-rose-500/15 text-rose-400">
        <TrendingUp className="h-3 w-3" />
      </span>
    );
  }
  if (trend === "cooling") {
    return (
      <span className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-emerald-500/15 text-emerald-400">
        <TrendingDown className="h-3 w-3" />
      </span>
    );
  }
  return (
    <span className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[var(--color-bg-elevated)] text-[var(--color-text-tertiary)]">
      <Minus className="h-3 w-3" />
    </span>
  );
}
