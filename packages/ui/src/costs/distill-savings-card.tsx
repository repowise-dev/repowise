"use client";

import { Scissors } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { formatCost, formatTokens } from "../lib/format";

export interface DistillSavingsGroup {
  group: string;
  events: number;
  raw_tokens: number;
  distilled_tokens: number;
  saved_tokens: number;
}

export interface DistillSavingsData {
  available: boolean;
  events: number;
  raw_tokens: number;
  distilled_tokens: number;
  saved_tokens: number;
  estimated_usd_saved: number;
  pricing_model: string;
  per_filter: DistillSavingsGroup[];
  per_day: DistillSavingsGroup[];
}

export interface DistillSavingsCardProps {
  /** Savings rollup from /distill-savings; undefined while loading. */
  data?: DistillSavingsData;
}

/**
 * Tokens saved by `repowise distill` (command + hook path). MCP response
 * truncation is not part of this ledger, and the card says so — the number
 * shown is real avoided agent input, not budget-capped responses.
 */
export function DistillSavingsCard({ data }: DistillSavingsCardProps) {
  const hasData = data?.available && data.events > 0;
  const pct =
    hasData && data.raw_tokens > 0
      ? Math.round((data.saved_tokens / data.raw_tokens) * 100)
      : 0;
  const topFilters = hasData ? data.per_filter.slice(0, 3) : [];

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Scissors className="h-4 w-4 text-cyan-500" />
          Distill savings
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        {hasData ? (
          <div className="space-y-3">
            <div className="flex items-baseline gap-2">
              <span className="text-2xl font-semibold text-[var(--color-text-primary)] tabular-nums">
                {formatTokens(data.saved_tokens)}
              </span>
              <span className="text-xs text-[var(--color-text-secondary)]">
                tokens saved ({pct}%) ·{" "}
                <span className="font-medium text-green-500">
                  {formatCost(data.estimated_usd_saved)}
                </span>{" "}
                est.
              </span>
            </div>
            <div className="space-y-1">
              {topFilters.map((f) => {
                const width =
                  data.saved_tokens > 0
                    ? Math.max(4, Math.round((f.saved_tokens / data.saved_tokens) * 100))
                    : 0;
                return (
                  <div key={f.group} className="flex items-center gap-2 text-xs">
                    <span className="w-24 shrink-0 truncate text-[var(--color-text-secondary)]">
                      {f.group}
                    </span>
                    <div className="flex-1 h-1.5 rounded bg-[var(--color-bg-inset)] overflow-hidden">
                      <div
                        className="h-full rounded bg-[var(--color-accent-primary)]"
                        style={{ width: `${width}%` }}
                      />
                    </div>
                    <span className="w-14 shrink-0 text-right tabular-nums text-[var(--color-text-primary)]">
                      {formatTokens(f.saved_tokens)}
                    </span>
                  </div>
                );
              })}
            </div>
            <p className="text-[10px] text-[var(--color-text-tertiary)] leading-snug">
              {data.events.toLocaleString()} distillation{data.events === 1 ? "" : "s"} via the{" "}
              <code>repowise distill</code> command/hook path. MCP response truncation is not
              counted. Estimate at {data.pricing_model} input rate.
            </p>
          </div>
        ) : (
          <p className="text-xs text-[var(--color-text-tertiary)] leading-snug max-w-[300px]">
            No distillation savings recorded yet. Run noisy commands through{" "}
            <code>repowise distill &lt;cmd&gt;</code> or install the rewrite hook (
            <code>repowise hook rewrite install</code>) to start trimming agent context.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
