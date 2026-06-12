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

/** "New contributor" marker — low cumulative prior-commit count at commit time. */
export function NewContributorBadge({ experience }: { experience: number }) {
  return (
    <span
      className="inline-flex shrink-0 items-center rounded-md bg-[var(--color-caution)]/15 px-1.5 py-0.5 text-[10px] font-medium text-[var(--color-caution)]"
      title={`Author had ${experience} prior commit${experience === 1 ? "" : "s"} in this repo at the time`}
    >
      new contributor
    </span>
  );
}
