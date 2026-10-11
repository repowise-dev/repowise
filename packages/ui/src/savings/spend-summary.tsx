"use client";

import * as React from "react";

import { formatCost, formatDate, formatNumber, formatTokens, parseDate } from "../lib/format";
import type { SpendView } from "./types";

export interface SpendSummaryProps {
  spend: SpendView;
}

/**
 * What Repowise's own model work cost.
 *
 * Called "Repowise model spend" and not "Indexing cost", because the
 * `llm_costs` table it reads records generation and other model-backed
 * operations too: the old label named a subset and counted the whole.
 *
 * A definition list, not a card grid. Four near-identical bordered tiles
 * claimed four separate subjects where there is one, and a `<dl>` is what
 * labelled values are.
 *
 * Deliberately adjacent to savings and never netted against them. They are
 * different accounts -- one is what agents avoided, the other is what Repowise
 * charged a provider -- and a single "net" figure would imply a settlement
 * that never happens.
 */
export function SpendSummary({ spend }: SpendSummaryProps) {
  const since = safeDate(spend.since);

  if (spend.total_calls === 0) {
    return (
      <p className="text-sm text-[var(--color-text-secondary)]">
        No model calls recorded for this repository, so nothing has been spent.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <dl className="grid grid-cols-2 gap-x-8 gap-y-4 sm:grid-cols-4">
        <Item label="Spent" value={formatCost(spend.total_cost_usd)} />
        <Item label="Calls" value={formatNumber(spend.total_calls)} />
        {/* Compact, like every other token figure on the page. Spelled out,
            9,400,000 sits beside a 12.9M savings total and the two read as
            different units. */}
        <Item label="Input tokens" value={formatTokens(spend.total_input_tokens)} />
        <Item label="Output tokens" value={formatTokens(spend.total_output_tokens)} />
      </dl>
      <p className="text-xs text-[var(--color-text-tertiary)]">
        Metered provider spend for indexing, page generation and other model-backed
        operations{since ? `, since ${since}` : ""}. Reported beside agent savings, never
        subtracted from them.
      </p>
    </div>
  );
}

function Item({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <dt className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
        {label}
      </dt>
      <dd className="text-[22px] font-semibold leading-none tabular-nums text-[var(--color-text-primary)]">
        {value}
      </dd>
    </div>
  );
}

function safeDate(iso: string | null): string | null {
  if (!iso) return null;
  const d = parseDate(iso);
  if (Number.isNaN(d.getTime())) return null;
  return formatDate(d);
}
