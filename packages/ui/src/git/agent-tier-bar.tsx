"use client";

import { cn } from "../lib/cn";

/**
 * Canonical autonomy-tier labels. The same numeric tiers surface on file
 * history, owner profiles, and commit detail — this is the single source so
 * the wording can't drift across those three surfaces.
 */
export const AGENT_TIER_LABELS: Record<string, string> = {
  "1": "autonomous",
  "2": "human-driven",
  "3": "assisted",
};

const TIER_TONE: Record<string, string> = {
  "1": "bg-[var(--color-accent-primary)]",
  "2": "bg-[var(--color-info)]",
  "3": "bg-[var(--color-accent-secondary)]",
};

const TIER_ORDER = ["1", "2", "3"];

export interface AgentTierBarProps {
  /** Map of autonomy tier ("1"|"2"|"3") → commit count. */
  tierCounts: Record<string, number>;
  className?: string;
}

/**
 * One stacked bar showing the mix of agent-autonomy tiers, replacing the
 * three different text-chip treatments that previously rendered the same
 * `tier_counts` data on file history, owner profiles, and commit detail.
 */
export function AgentTierBar({ tierCounts, className }: AgentTierBarProps) {
  const entries = TIER_ORDER.map((tier) => ({
    tier,
    count: tierCounts[tier] ?? 0,
  })).filter((e) => e.count > 0);

  const total = entries.reduce((s, e) => s + e.count, 0);
  if (total === 0) return null;

  return (
    <div className={cn("space-y-2", className)}>
      <div className="flex h-2 w-full overflow-hidden rounded-full bg-[var(--color-bg-inset)]">
        {entries.map((e) => (
          <div
            key={e.tier}
            className={cn("h-full", TIER_TONE[e.tier] ?? "bg-[var(--color-accent-primary)]")}
            style={{ width: `${(e.count / total) * 100}%` }}
            title={`${AGENT_TIER_LABELS[e.tier] ?? `tier ${e.tier}`}: ${e.count}`}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-[var(--color-text-tertiary)]">
        {entries.map((e) => (
          <span key={e.tier} className="inline-flex items-center gap-1.5">
            <span
              className={cn(
                "h-2 w-2 rounded-full",
                TIER_TONE[e.tier] ?? "bg-[var(--color-accent-primary)]",
              )}
            />
            {AGENT_TIER_LABELS[e.tier] ?? `tier ${e.tier}`}
            <span className="tabular-nums text-[var(--color-text-secondary)]">{e.count}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
