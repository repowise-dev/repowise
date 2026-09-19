"use client";

import * as React from "react";

import { PageLede } from "../shared/page-lede";
import { StatRibbon, type RibbonStat } from "../stats/stat-ribbon";
import { formatCost, formatDate, formatTokens, parseDate } from "../lib/format";
import type { SavingsView } from "./types";

export interface SavingsLedeProps {
  data: SavingsView;
  /** Where "Method and limits" goes. Omit and the link is not rendered. */
  methodologyHref?: string | undefined;
  LinkComponent?: React.ElementType | undefined;
}

/**
 * The page's subject: what agents avoided, and what the figure rests on.
 *
 * The distinctive element here is the evidence, not the size of the number.
 * The total is one figure at lede weight and everything beside it exists to
 * qualify it: how much is measured against how much is inferred, how much of
 * it carries a rate, and since when. A bigger number with none of that would
 * be a worse page.
 *
 * Performs no arithmetic beyond percentages of figures the report already
 * computed. Every quantity shown is a field.
 */
export function SavingsLede({ data, methodologyHref, LinkComponent }: SavingsLedeProps) {
  const total = data.saved_input_tokens;
  const measured = data.measured_saved_input_tokens;
  const inferred = data.inferred_saved_input_tokens;
  const unpriced = data.unpriced_saved_input_tokens;

  // "Estimated" for as long as any part of the total rests on a counterfactual
  // rather than a known before and after. The word is the honest one and it
  // costs nothing; dropping it once inferred evidence is present is how a
  // headline starts overclaiming.
  const label = inferred > 0 ? "Estimated agent savings" : "Agent savings";

  const stats: RibbonStat[] = [
    {
      label: "Measured",
      value: formatTokens(measured),
      hint: "A known before and after: the tokens an operation that actually ran removed.",
      sub: pctOf(measured, total),
    },
    {
      label: "Inferred",
      value: formatTokens(inferred),
      hint: "A documented counterfactual: the exploration a Repowise answer replaced.",
      sub: pctOf(inferred, total),
    },
    {
      label: "Valued",
      value: formatCost(data.priced_input_savings_usd),
      hint: "Each event is priced at the rate recorded when it happened, so this covers only events that carried one.",
      sub:
        unpriced > 0
          ? `on ${formatTokens(data.priced_saved_input_tokens)} of ${formatTokens(total)}`
          : "on all savings",
    },
    {
      label: "Interactions",
      value: data.unique_events.toLocaleString(),
      hint: "Logical agent interactions recorded. One interaction contributes to the total once.",
      // "9 saved tokens" reads as nine tokens. It is nine interactions.
      sub: `${data.saving_interactions.toLocaleString()} produced a saving`,
    },
    {
      label: "MCP answered",
      value: data.mcp_queries_answered.toLocaleString(),
      hint: "Successful or usably partial MCP calls. Dead ends are counted separately and save nothing.",
      ...(data.dead_ends > 0 ? { sub: `${data.dead_ends.toLocaleString()} dead ends` } : {}),
    },
  ];

  return (
    <div className="flex flex-col gap-6">
      <PageLede
        label={label}
        labelHint="Input tokens your agent never had to read."
        value={formatTokens(total)}
        unit="input tokens"
        layout="beside"
      >
        <p>
          These are input tokens your agent never had to read, recorded one event per
          interaction across the <code>repowise distill</code> path, the replacement hooks
          and MCP calls.{" "}
          {inferred > 0
            ? "Part of the total is inferred, so the figure is an estimate rather than a measurement."
            : "Every part of the total is a measured before and after."}
        </p>
        <p>
          {unpriced > 0 ? (
            <>
              {formatTokens(unpriced)} of those tokens carry no rate, so the dollar figure
              values the rest and not the whole total.
            </>
          ) : (
            <>Every event carried a rate, so the dollar figure values the whole total.</>
          )}{" "}
          <Coverage
            data={data}
            methodologyHref={methodologyHref}
            LinkComponent={LinkComponent}
          />
        </p>
      </PageLede>

      <StatRibbon stats={stats} />
    </div>
  );
}

/**
 * The quiet permanent line: what window the figures cover, and where the
 * method is written down.
 *
 * Stays visible after the reset notice is dismissed, which is the whole
 * requirement -- a reader who dismissed an explanation months ago still needs
 * to know these figures do not reach back past a date. Worded as coverage and
 * read from `first_event_at` rather than from a hardcoded reset date, so it
 * describes the data actually rendered instead of a constant no test can
 * check.
 */
function Coverage({
  data,
  methodologyHref,
  LinkComponent,
}: {
  data: SavingsView;
  methodologyHref?: string | undefined;
  LinkComponent?: React.ElementType | undefined;
}) {
  const A = LinkComponent ?? "a";
  const since = safeDate(data.first_event_at);
  const window =
    data.window_days === null
      ? since
        ? `Measured since ${since}.`
        : null
      : `Covering the last ${data.window_days} days.`;

  return (
    <span className="text-[var(--color-text-tertiary)]">
      {window}
      {window && methodologyHref ? " " : null}
      {methodologyHref && (
        <A
          href={methodologyHref}
          className="text-[var(--color-accent-primary)] hover:underline"
        >
          Method and limits
        </A>
      )}
    </span>
  );
}

/** A share, or nothing. A denominator of zero has no percentage, and "0%"
 *  would be a claim rather than an absence. */
function pctOf(part: number, whole: number): string | undefined {
  if (whole <= 0) return undefined;
  return `${Math.round((part / whole) * 100)}% of total`;
}

/** Render a timestamp, or nothing if it is absent or unparseable. A date is
 *  qualifying a published number, so a bad one is worse than none. */
function safeDate(iso: string | null): string | null {
  if (!iso) return null;
  const d = parseDate(iso);
  if (Number.isNaN(d.getTime())) return null;
  return formatDate(d);
}
