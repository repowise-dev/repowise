"use client";

import { Scale, TrendingUp } from "lucide-react";
import { Card, CardContent } from "../ui/card";
import { formatCost, formatTokens } from "../lib/format";

export interface RoiCardProps {
  /** Estimated dollars saved for the coding agent (distill + MCP). */
  savedUsd: number;
  /** Real dollars spent generating/refreshing this repo's docs. */
  spentUsd: number;
  /** Total agent tokens saved, for the supporting caption. */
  savedTokens: number;
}

/**
 * Ties the two halves of the Costs page together: what repowise spent
 * generating the docs against what it saved the coding agent. The page shows
 * both numbers separately; this states the relationship the reader is doing in
 * their head — return multiple and net position — in one line.
 *
 * Savings are an estimate (chars/4, priced at the agent's input rate); spend is
 * metered. Shown only once there is real spend to compare against.
 */
export function RoiCard({ savedUsd, spentUsd, savedTokens }: RoiCardProps) {
  if (spentUsd <= 0) return null;

  const net = savedUsd - spentUsd;
  const multiple = savedUsd / spentUsd;
  const ahead = net >= 0;

  return (
    <Card>
      <CardContent className="py-5">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <Scale className="h-5 w-5 shrink-0 text-[var(--color-accent-primary)]" />
            <div>
              <div className="text-xs font-medium uppercase tracking-wide text-[var(--color-text-tertiary)]">
                Return on indexing
              </div>
              <p className="mt-0.5 text-sm text-[var(--color-text-secondary)]">
                Spent{" "}
                <span className="font-medium text-[var(--color-text-primary)]">
                  {formatCost(spentUsd)}
                </span>{" "}
                generating docs · saved your agent{" "}
                <span className="font-medium text-[var(--color-success)]">
                  {formatCost(savedUsd)}
                </span>{" "}
                ({formatTokens(savedTokens)} tokens)
              </p>
            </div>
          </div>
          <div className="flex items-center gap-6 text-right">
            <div>
              <div className="flex items-center justify-end gap-1 text-2xl font-semibold tabular-nums text-[var(--color-text-primary)]">
                <TrendingUp className="h-4 w-4 text-[var(--color-success)]" />
                {multiple >= 10 ? multiple.toFixed(0) : multiple.toFixed(1)}×
              </div>
              <div className="text-xs text-[var(--color-text-tertiary)]">return</div>
            </div>
            <div>
              <div
                className={`text-2xl font-semibold tabular-nums ${
                  ahead ? "text-[var(--color-success)]" : "text-[var(--color-text-primary)]"
                }`}
              >
                {ahead ? "+" : "−"}
                {formatCost(Math.abs(net))}
              </div>
              <div className="text-xs text-[var(--color-text-tertiary)]">net</div>
            </div>
          </div>
        </div>
        <p className="mt-3 text-xs leading-snug text-[var(--color-text-tertiary)]">
          Savings are estimated and priced at your agent&apos;s input rate; generation spend is
          metered. A one-time indexing cost pays back across every agent session that reads these
          docs.
        </p>
      </CardContent>
    </Card>
  );
}
