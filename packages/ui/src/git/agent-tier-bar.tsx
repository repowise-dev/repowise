"use client";

import { ProportionBar } from "../shared/proportion-bar";

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
  return (
    <ProportionBar
      className={className}
      label="Agent commits by autonomy tier"
      segments={TIER_ORDER.map((tier) => {
        const count = tierCounts[tier] ?? 0;
        return {
          key: tier,
          label: AGENT_TIER_LABELS[tier] ?? `tier ${tier}`,
          value: count,
          detail: count.toLocaleString(),
        };
      })}
    />
  );
}
