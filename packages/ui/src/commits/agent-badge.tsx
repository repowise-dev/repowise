"use client";

import { Bot } from "lucide-react";
import { cn } from "../lib/cn";

/** Human-readable autonomy tier: 1 = bot, 2 = agent driven by a human in the
 *  loop, 3 = assisted/co-authored. */
export function agentTierLabel(tier: number | null | undefined): string | null {
  if (tier === 1) return "autonomous";
  if (tier === 2) return "agent";
  if (tier === 3) return "assisted";
  return null;
}

export interface AgentBadgeProps {
  agentName: string;
  tier?: number | null | undefined;
  confidence?: string | null | undefined;
  className?: string;
}

/** Compact provenance chip for agent-attributed commits. */
export function AgentBadge({ agentName, tier, confidence, className }: AgentBadgeProps) {
  const tierLabel = agentTierLabel(tier);
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-md bg-[var(--color-accent-muted)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--color-accent-primary)]",
        className,
      )}
      title={`Attributed to ${agentName}${tierLabel ? ` (${tierLabel})` : ""}${
        confidence ? ` — ${confidence} confidence` : ""
      }`}
    >
      <Bot className="h-2.5 w-2.5" />
      <span className="max-w-[110px] truncate">{agentName}</span>
      {tierLabel && <span className="opacity-70">{tierLabel}</span>}
    </span>
  );
}

/** At or below this many commits in the index, an author reads as new. */
const NEW_CONTRIBUTOR_MAX_COMMITS = 2;

/**
 * Whether a commit's author is new to this repo.
 *
 * Keyed on the author's total commits in the index, not on `author_experience`
 * (their running count at commit time). Experience necessarily starts near zero
 * for everyone at the old edge of the indexed window, so gating on it labels the
 * repo's most prolific author a newcomer on their oldest indexed commits. A
 * total does not move with position in the window. Agent-authored commits are
 * excluded by the callers, which show provenance instead.
 */
export function isNewContributor(commitCount: number | null | undefined): boolean {
  return commitCount != null && commitCount <= NEW_CONTRIBUTOR_MAX_COMMITS;
}

/** "New contributor" marker — few commits by this author across the index. */
export function NewContributorBadge({ commitCount }: { commitCount: number }) {
  return (
    <span
      className="inline-flex shrink-0 items-center rounded-md bg-[var(--color-caution)]/15 px-1.5 py-0.5 text-[10px] font-medium text-[var(--color-caution)]"
      title={`Author has ${commitCount} commit${commitCount === 1 ? "" : "s"} in the indexed history`}
    >
      new contributor
    </span>
  );
}
