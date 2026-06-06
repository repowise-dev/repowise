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
  /** Raw (non-distilled) agent commands a filter would have caught. */
  missed_events?: number;
  missed_tokens_est?: number;
  missed_window_days?: number;
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
  const missed = data?.available && (data.missed_events ?? 0) > 0;

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
            {missed && (
              <div className="flex items-baseline gap-2 border-t border-[var(--color-border-secondary)] pt-2">
                <span className="text-sm font-medium text-amber-500 tabular-nums">
                  ~{formatTokens(data.missed_tokens_est ?? 0)}
                </span>
                <span className="text-xs text-[var(--color-text-secondary)]">
                  missed — {(data.missed_events ?? 0).toLocaleString()} raw command
                  {(data.missed_events ?? 0) === 1 ? "" : "s"} bypassed distill in the last{" "}
                  {data.missed_window_days ?? 7} days
                </span>
              </div>
            )}
            <p className="text-[10px] text-[var(--color-text-tertiary)] leading-snug">
              {data.events.toLocaleString()} distillation{data.events === 1 ? "" : "s"} via the{" "}
              <code>repowise distill</code> command/hook path. MCP response truncation is not
              counted. Estimate at {data.pricing_model} input rate.
              {missed && (
                <>
                  {" "}
                  Missed savings are scanned from local agent transcripts (nothing leaves this
                  machine) — see <code>repowise saved --missed</code>.
                </>
              )}
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
