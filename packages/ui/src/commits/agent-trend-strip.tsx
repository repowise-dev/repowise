"use client";

import { Bot } from "lucide-react";
import type { AgentTrend } from "@repowise-dev/types/git";
import { cn } from "../lib/cn";

export interface AgentTrendStripProps {
  trend: AgentTrend;
  className?: string;
}

function share(part: number, whole: number): number {
  return whole > 0 ? (part / whole) * 100 : 0;
}

/**
 * Compact attribution trend: the headline share plus a monthly bar strip.
 *
 * The share is of commits with a known source and always sits beside its
 * coverage; below the coverage floor only the count is shown. Each bar splits
 * a month into agent, human-attested and unknown, so unmarked commits read as
 * unknown rather than human. Renders nothing when attribution is off or no
 * commit in the window carries agent attribution.
 */
export function AgentTrendStrip({ trend, className }: AgentTrendStripProps) {
  if (!trend.enabled || trend.agent_commits === 0) return null;
  // Cap the strip at the last 12 months so it stays a glanceable sparkline.
  const buckets = trend.buckets.slice(-12);
  const topAgents = trend.agent_names.slice(0, 3);
  const coverage = Math.round(trend.coverage_pct ?? 0);

  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-2",
        className,
      )}
    >
      <span className="flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)]">
        <Bot className="h-3.5 w-3.5 text-[var(--color-accent-primary)]" />
        {trend.agent_pct_of_covered != null ? (
          <>
            at least
            <span className="font-semibold tabular-nums text-[var(--color-text-primary)]">
              {Math.round(trend.agent_pct_of_covered)}%
            </span>
            of covered commits are agent-attributed
          </>
        ) : (
          <>
            <span className="font-semibold tabular-nums text-[var(--color-text-primary)]">
              {trend.agent_commits}
            </span>
            agent-attributed commits; too few have a known source for a share
          </>
        )}
        <span className="tabular-nums text-[var(--color-text-tertiary)]">
          (coverage {coverage}%, {trend.unknown_commits} unknown)
        </span>
      </span>

      <div className="flex h-6 items-end gap-0.5" aria-label="Monthly commits by attribution">
        {buckets.map((b) => (
          <div
            key={b.month}
            className="flex h-full w-2 flex-col justify-end overflow-hidden rounded-sm bg-[var(--color-bg-elevated)]"
            title={`${b.month}: ${b.agent_commits} agent, ${b.human_attested_commits} human-attested, ${b.unknown_commits} unknown`}
          >
            <div
              className="w-full bg-[var(--color-text-tertiary)]/40"
              style={{ height: `${share(b.human_attested_commits, b.total_commits)}%` }}
            />
            <div
              className="w-full bg-[var(--color-accent-primary)]/70"
              style={{ height: `${share(b.agent_commits, b.total_commits)}%` }}
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
