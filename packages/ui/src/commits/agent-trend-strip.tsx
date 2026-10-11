"use client";

import { Bot } from "lucide-react";
import type { AgentTrend } from "@repowise-dev/types/git";
import { cn } from "../lib/cn";

export interface AgentTrendStripProps {
  trend: AgentTrend;
  className?: string;
}

/**
 * Compact agent-share trend: headline share plus a monthly bar strip.
 * Renders nothing when no commit in the window carries agent attribution.
 */
export function AgentTrendStrip({ trend, className }: AgentTrendStripProps) {
  if (trend.agent_commits === 0) return null;
  // Cap the strip at the last 12 months so it stays a glanceable sparkline.
  const buckets = trend.buckets.slice(-12);
  const topAgents = trend.agent_names.slice(0, 3);

  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-2",
        className,
      )}
    >
      <span className="flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)]">
        <Bot className="h-3.5 w-3.5 text-[var(--color-accent-primary)]" />
        <span className="font-semibold tabular-nums text-[var(--color-text-primary)]">
          {Math.round(trend.agent_pct)}%
        </span>
        of indexed commits are agent-attributed
        <span className="tabular-nums text-[var(--color-text-tertiary)]">
          ({trend.agent_commits} of {trend.total_commits})
        </span>
      </span>

      <div className="flex h-6 items-end gap-0.5" aria-label="Monthly agent share">
        {buckets.map((b) => (
          <div
            key={b.month}
            className="flex h-full w-2 items-end rounded-sm bg-[var(--color-bg-elevated)]"
            title={`${b.month}: ${Math.round(b.agent_pct)}% agent (${b.agent_commits}/${b.total_commits})`}
          >
            <div
              className="w-full rounded-sm bg-[var(--color-accent-primary)]/70"
              style={{
                height: `${Math.max(b.agent_pct, b.agent_commits > 0 ? 8 : 0)}%`,
              }}
            />
          </div>
        ))}
      </div>

      {topAgents.length > 0 && (
        <span className="text-[10px] text-[var(--color-text-tertiary)]">
          {topAgents.map((a) => `${a.name} (${a.count})`).join(" · ")}
        </span>
      )}
    </div>
  );
}
